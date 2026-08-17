"""Lean-kernel + blackboard daemon — the SOLE writer of state/<challenge>/**.

Successor of oracle_daemon.py. Wraps StateManager + LeanWorkspace +
KernelRunner + JobRegistry behind a small localhost HTTP API. Two (or more)
independent contestant sessions talk to it via client.py. Every write funnels
through this one process and event loop, so the single-writer assumption in
state_manager.py holds and concurrent contestants cannot corrupt shared state.

Nothing is secret anymore — the kernel has no answer key — but the surviving
discipline is the same sovereignty: the daemon's kernel verdict, recorded in
the append-only job log, is the only admissible evidence. Contestants never
touch the Lean workspace; they submit source as job payloads and poll.
"""
from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import logging
import signal
from datetime import datetime, timezone
from typing import Any

from aiohttp import web
from dotenv import load_dotenv

try:
    from . import assembly, historian, promotion
    from .config import load_config
    from .dag import DagError, DagStore
    from .hygiene import scan_source
    from .jobs import Job, JobRegistry
    from .kernel_runner import KernelRunner
    from .lean_workspace import LeanWorkspace, WorkspaceError
    from .protocol import JOB_MODES, check_cache_key, sha256_text
    from .state_manager import StateManager
    from .utils import logging as logsetup
    from .utils import paths
except ImportError:  # direct script execution
    import assembly
    import historian
    import promotion
    from config import load_config
    from dag import DagError, DagStore
    from hygiene import scan_source
    from jobs import Job, JobRegistry
    from kernel_runner import KernelRunner
    from lean_workspace import LeanWorkspace, WorkspaceError
    from protocol import JOB_MODES, check_cache_key, sha256_text
    from state_manager import StateManager
    from utils import logging as logsetup
    from utils import paths

log = logging.getLogger("daemon")

# Modes contestants may submit directly. `final` builds are daemon-assembled
# by the finish flow only.
CONTESTANT_MODES = ("eval", "proof", "skeleton")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dumps(obj: Any) -> str:
    return json.dumps(obj, default=str)


def _ok(**kw: Any) -> web.Response:
    payload: dict[str, Any] = {"ok": True}
    payload.update(kw)
    status = kw.pop("_status", 200) if "_status" in kw else 200
    return web.json_response(payload, status=status, dumps=_dumps)


def _err(message: str, status: int = 400) -> web.Response:
    return web.json_response({"ok": False, "error": message}, status=status, dumps=_dumps)


async def main_async(args: argparse.Namespace) -> int:
    load_dotenv()
    cfg = load_config(paths.config_path(args.challenge))
    paths.ensure_state_layout(args.challenge)
    logsetup.setup(paths.state_dir(args.challenge))

    state = StateManager(args.challenge)
    contestant_ids = {c.id for c in cfg.contestants}

    if cfg.hygiene.goal_sha256:
        actual = sha256_text(paths.goal_path(args.challenge).read_text(encoding="utf-8"))
        if actual != cfg.hygiene.goal_sha256:
            log.error(
                "Goal.lean hash mismatch: config freezes %s but file is %s — refusing to start",
                cfg.hygiene.goal_sha256, actual,
            )
            return 1

    workspace: LeanWorkspace | None = None
    registry = JobRegistry(state)
    runner: KernelRunner | None = None
    lease_ttl = args.lease_ttl_seconds or cfg.kernel.lease_ttl_seconds
    dag = DagStore(state, args.challenge, lease_ttl)
    dag.load()
    if cfg.kernel.enabled:
        workspace = LeanWorkspace(args.challenge, cfg.kernel)
        try:
            workspace.materialize()
        except WorkspaceError as exc:
            log.error("workspace materialization failed: %s", exc)
            return 1
        interrupted = registry.recover()
        if interrupted:
            log.warning("recovered job log: %d in-flight jobs marked failed", interrupted)
        # persist the frozen-input hashes for post-solve consolidation
        (state.state / "frozen_hashes.json").write_text(
            json.dumps(
                {"toolchain_fingerprint": workspace.fingerprint, "files": workspace.frozen_hashes},
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        await dag.ensure_root(
            name="goal_root",
            statement=cfg.hygiene.goal_def,
            gloss="the frozen goal (Goal.lean)",
        )

        async def _on_finished(job: Job, result: dict[str, Any]) -> None:
            if job.mode == "final":
                if result.get("outcome") == "ok":
                    report = {
                        "axioms": result.get("axioms"),
                        "fidelity_ok": result.get("fidelity_ok"),
                        "toolchain_fingerprint": workspace.fingerprint,
                        "assembly_sha256": job.source_sha256,
                        "frozen_hashes_ok": workspace.frozen_hashes == workspace._hash_frozen(),
                    }
                    rec = await state.append_finish_proposal(
                        job.contestant_id, job.finish_text,
                        job_id=job.job_id, hygiene_report=report,
                    )
                    result["finish_proposal_id"] = rec["id"]
                    log.info("[%s] finish proposal %s created by final job %s",
                             job.contestant_id, rec["id"], job.job_id)
                else:
                    await state.append_finish_rejection(
                        job.contestant_id, job.job_id,
                        f"{result.get('failure_kind')}: {result.get('detail', '')}",
                    )
                return
            if result.get("outcome") == "ok":
                if job.mode == "proof" and job.node_id and job.node_id in dag.nodes:
                    await state.store_proof_source(job.node_id, job.raw_source, job.job_id)
                    await dag.mark_proved(
                        job.node_id,
                        job.contestant_id,
                        job.job_id,
                        sha256_text(job.raw_source),
                        proved_modulo=list(job.stubbed_deps),
                    )
                    log.info("[%s] node %s proved by job %s (modulo %s)",
                             job.contestant_id, job.node_id, job.job_id, job.stubbed_deps or "nothing")
                elif job.mode == "skeleton" and job.decomp_id:
                    try:
                        children = await dag.accept_decomposition(job.decomp_id, job.job_id)
                        log.info("decomposition %s accepted; children: %s",
                                 job.decomp_id, [c.node_id for c in children])
                    except DagError as exc:
                        log.warning("decomposition %s not accepted: %s", job.decomp_id, exc)
            via = promotion.predictive_promotion(
                job.predict, result.get("prediction_correct"), job.hypothesis
            )
            if via and cfg.breakthroughs.allow_predictive_promotion:
                await state.append_breakthrough(
                    job.contestant_id, job.hypothesis or "", promoted_via=via
                )
                log.info("[%s] predictive promotion via job %s", job.contestant_id, job.job_id)

        runner = KernelRunner(workspace, cfg.kernel, cfg.hygiene, registry, _on_finished)
        runner.start()

    host = args.host or cfg.daemon.host
    port = args.port or cfg.daemon.port

    log.info(
        "daemon starting | challenge=%s kernel=%s contestants=%s addr=%s:%d",
        args.challenge, cfg.kernel.enabled, sorted(contestant_ids), host, port,
    )

    def _valid_contestant(cid: Any) -> bool:
        return isinstance(cid, str) and bool(cid) and (not contestant_ids or cid in contestant_ids)

    def _rounds_done() -> int:
        p = state.state / "round_counter.txt"
        try:
            return int(p.read_text().strip()) if p.exists() else 0
        except (ValueError, OSError):
            return 0

    async def _read_json(request: web.Request) -> Any:
        try:
            raw = await request.read()
            return json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:  # noqa: BLE001
            return None

    # ---- handlers ----

    async def health(request: web.Request) -> web.Response:
        return _ok(
            challenge=cfg.challenge.name,
            kernel_enabled=cfg.kernel.enabled,
            lean_version=workspace.lean_version if workspace else None,
            toolchain_fingerprint=workspace.fingerprint if workspace else None,
            queue_depth=registry.queue_depth(),
            solved=state.is_solved_flag_present(),
            rounds_done=_rounds_done(),
            contestants=sorted(contestant_ids),
        )

    async def problem(request: web.Request) -> web.Response:
        return _ok(
            problem=state.problem(),
            kernel_description=cfg.kernel.description,
            kernel_enabled=cfg.kernel.enabled,
        )

    async def snapshot(request: web.Request) -> web.Response:
        cid = request.query.get("contestant_id", "")
        if not _valid_contestant(cid):
            return _err(f"unknown contestant_id {cid!r}; expected one of {sorted(contestant_ids)}")
        snap = state.snapshot(cid)
        frontier_nodes = await dag.frontier()
        my_leases = [
            n.public_view()
            for n in dag.nodes.values()
            if n.lease is not None and n.lease.holder == cid
        ]
        return _ok(
            contestant_id=cid,
            snapshot=dataclasses.asdict(snap),
            dag_frontier=[n.public_view() for n in frontier_nodes],
            my_leases=my_leases,
            solved=state.is_solved_flag_present(),
            rounds_done=_rounds_done(),
            kernel_enabled=cfg.kernel.enabled,
            kernel_description=cfg.kernel.description,
        )

    async def check(request: web.Request) -> web.Response:
        if runner is None or workspace is None:
            return _err("kernel is disabled for this challenge", status=409)
        data = await _read_json(request)
        if not isinstance(data, dict):
            return _err("invalid JSON body")
        cid = data.get("contestant_id", "")
        if not _valid_contestant(cid):
            return _err(f"unknown contestant_id {cid!r}")
        mode = data.get("mode", "")
        if mode not in CONTESTANT_MODES:
            return _err(f"mode must be one of {list(CONTESTANT_MODES)}")
        source = data.get("source")
        if not isinstance(source, str) or not source.strip():
            return _err("missing 'source'")
        if len(source.encode("utf-8")) > cfg.kernel.max_source_bytes:
            return _err(f"source exceeds {cfg.kernel.max_source_bytes} bytes", status=413)
        decl = data.get("decl")
        statement = data.get("statement")
        node_id = data.get("node_id")
        if isinstance(node_id, str) and node_id:
            node = dag.nodes.get(node_id)
            if node is None:
                return _err(f"unknown DAG node {node_id!r}", status=404)
            # the node is authoritative for what is being proved
            decl = node.name
            statement = node.statement
        elif mode in ("proof", "skeleton") and not isinstance(decl, str):
            return _err(f"{mode} jobs require 'decl' (the declaration under audit) or 'node_id'")
        predict = data.get("predict")
        if predict is not None and predict not in ("ok", "fail"):
            return _err("predict must be 'ok' or 'fail'")
        hypothesis = data.get("hypothesis")
        timeout = min(
            int(data.get("timeout") or cfg.kernel.timeout_seconds),
            cfg.kernel.max_timeout_seconds,
        )

        # Proof jobs against a DAG node compile in dependency context: proved
        # children as real sources, unproved ones as daemon-generated stubs.
        composed = source
        stubbed: list[str] = []
        if mode == "proof" and isinstance(node_id, str) and node_id in dag.nodes:
            composed, stubbed = assembly.compose_proof_file(dag, state, node_id, source)

        source_sha = sha256_text(source)
        job_id = registry.new_job_id(cid, source_sha)
        # The cache key hashes what the kernel actually compiles (the composed
        # file), so a DAG-state change — a child getting proved — is a
        # different cache entry, never a stale verdict.
        cache_key = check_cache_key(
            challenge=cfg.challenge.name,
            fingerprint=workspace.fingerprint,
            mode=mode,
            target=node_id or decl,
            source_sha256=sha256_text(composed),
            flags={"max_heartbeats": cfg.hygiene.max_heartbeats},
        )

        job = Job(
            job_id=job_id,
            contestant_id=cid,
            mode=mode,
            cache_key=cache_key,
            source_sha256=source_sha,
            node_id=node_id,
            decl=decl,
            statement=statement,
            predict=predict,
            hypothesis=hypothesis,
            timeout=timeout,
            source=composed,
            raw_source=source,
            stubbed_deps=stubbed,
        )

        # Hygiene scans the CONTESTANT's submission, not the daemon's stubs.
        scan = scan_source(source, cfg.hygiene, mode)
        if not scan.ok:
            result = {
                "outcome": "failed",
                "failure_kind": "hygiene_source",
                "violations": scan.violations,
            }
            if predict in ("ok", "fail"):
                result["prediction_correct"] = predict == "fail"
            await registry.submit(job)
            await registry.mark_finished(job, result)
            return _ok(job_id=job_id, status="done", result=result)

        cached = registry.cache_lookup(cache_key)
        if cached is not None:
            result = dict(cached)
            result["cache_hit"] = True
            if predict in ("ok", "fail"):
                result["prediction_correct"] = (predict == "ok") == (result.get("outcome") == "ok")
            # replicate the proved-node side effects a live run would have
            if (
                result.get("outcome") == "ok"
                and mode == "proof"
                and isinstance(node_id, str)
                and node_id in dag.nodes
                and dag.nodes[node_id].status not in ("proved", "accepted")
            ):
                await state.store_proof_source(node_id, source, job_id)
                await dag.mark_proved(
                    node_id, cid, job_id, source_sha, proved_modulo=stubbed
                )
            await registry.submit(job)
            await registry.mark_finished(job, result)
            via = promotion.predictive_promotion(
                predict, result.get("prediction_correct"), hypothesis
            )
            if via and cfg.breakthroughs.allow_predictive_promotion:
                await state.append_breakthrough(cid, hypothesis or "", promoted_via=via)
            return _ok(job_id=job_id, status="done", result=result, cache_hit=True)

        await runner.enqueue(job)
        return _ok(job_id=job_id, status="queued", _status=202)

    async def check_status(request: web.Request) -> web.Response:
        job_id = request.match_info.get("job_id", "")
        job = registry.get(job_id)
        if job is None:
            return _err(f"unknown job {job_id!r}", status=404)
        return _ok(**job.public_view())

    # ---- DAG handlers ----

    async def dag_view(request: web.Request) -> web.Response:
        view = request.query.get("view", "full")
        if view == "frontier":
            nodes = await dag.frontier()
            return _ok(root=dag.root_id, frontier=[n.public_view() for n in nodes])
        await dag._expire_all_due()
        return _ok(**dag.public_view())

    def _dag_err(exc: DagError) -> web.Response:
        return _err(str(exc), status=exc.status)

    async def dag_node(request: web.Request) -> web.Response:
        data = await _read_json(request)
        if not isinstance(data, dict):
            return _err("invalid JSON body")
        cid = data.get("contestant_id", "")
        if not _valid_contestant(cid):
            return _err(f"unknown contestant_id {cid!r}")
        try:
            node, created = await dag.propose_node(
                cid,
                str(data.get("name", "")),
                str(data.get("statement", "")),
                str(data.get("gloss", "")),
            )
        except DagError as exc:
            return _dag_err(exc)
        return _ok(node=node.public_view(), created=created)

    async def dag_claim(request: web.Request) -> web.Response:
        data = await _read_json(request)
        if not isinstance(data, dict):
            return _err("invalid JSON body")
        cid = data.get("contestant_id", "")
        if not _valid_contestant(cid):
            return _err(f"unknown contestant_id {cid!r}")
        node_id = data.get("node_id", "")
        try:
            lease = await dag.claim(str(node_id), cid)
        except DagError as exc:
            return _dag_err(exc)
        return _ok(node_id=node_id, lease_id=lease.lease_id, expires_ts=lease.expires_ts)

    async def dag_release(request: web.Request) -> web.Response:
        data = await _read_json(request)
        if not isinstance(data, dict):
            return _err("invalid JSON body")
        cid = data.get("contestant_id", "")
        if not _valid_contestant(cid):
            return _err(f"unknown contestant_id {cid!r}")
        try:
            await dag.release(str(data.get("node_id", "")), cid)
        except DagError as exc:
            return _dag_err(exc)
        return _ok()

    async def dag_accept(request: web.Request) -> web.Response:
        data = await _read_json(request)
        if not isinstance(data, dict):
            return _err("invalid JSON body")
        cid = data.get("contestant_id", "")
        if not _valid_contestant(cid):
            return _err(f"unknown contestant_id {cid!r}")
        reason = data.get("reason", "")
        if not isinstance(reason, str) or not reason.strip():
            return _err("accept requires a 'reason' (statement-fidelity check)")
        try:
            node = await dag.accept_node(str(data.get("node_id", "")), cid, reason)
        except DagError as exc:
            return _dag_err(exc)
        return _ok(node=node.public_view())

    async def dag_decompose(request: web.Request) -> web.Response:
        """Propose a decomposition: children metadata + a skeleton source
        (children declared `:= sorry`, parent proved from them). The daemon
        submits the skeleton as a check job; on kernel ok the decomposition
        is accepted and the children join the frontier."""
        if runner is None or workspace is None:
            return _err("kernel is disabled for this challenge", status=409)
        data = await _read_json(request)
        if not isinstance(data, dict):
            return _err("invalid JSON body")
        cid = data.get("contestant_id", "")
        if not _valid_contestant(cid):
            return _err(f"unknown contestant_id {cid!r}")
        parent_id = str(data.get("node_id", ""))
        parent = dag.nodes.get(parent_id)
        if parent is None:
            return _err(f"unknown DAG node {parent_id!r}", status=404)
        children = data.get("children")
        if not isinstance(children, list):
            return _err("decompose requires 'children' (list of {name, statement, gloss})")
        source = data.get("source")
        if not isinstance(source, str) or not source.strip():
            return _err("decompose requires 'source' (the skeleton)")
        if len(source.encode("utf-8")) > cfg.kernel.max_source_bytes:
            return _err(f"source exceeds {cfg.kernel.max_source_bytes} bytes", status=413)
        timeout = min(
            int(data.get("timeout") or cfg.kernel.timeout_seconds),
            cfg.kernel.max_timeout_seconds,
        )

        scan = scan_source(source, cfg.hygiene, "skeleton")
        if not scan.ok:
            return _err(
                "skeleton rejected by hygiene scan: " + "; ".join(scan.violations),
                status=409,
            )

        source_sha = sha256_text(source)
        job_id = registry.new_job_id(cid, source_sha)
        try:
            decomp_id = await dag.propose_decomposition(cid, parent_id, children, job_id)
        except DagError as exc:
            return _dag_err(exc)

        cache_key = check_cache_key(
            challenge=cfg.challenge.name,
            fingerprint=workspace.fingerprint,
            mode="skeleton",
            target=parent_id,
            source_sha256=source_sha,
            flags={"max_heartbeats": cfg.hygiene.max_heartbeats},
        )
        job = Job(
            job_id=job_id,
            contestant_id=cid,
            mode="skeleton",
            cache_key=cache_key,
            source_sha256=source_sha,
            node_id=parent_id,
            decl=parent.name,
            statement=parent.statement,
            timeout=timeout,
            source=source,
            raw_source=source,
            children=[
                {"name": str(c.get("name", "")), "statement": str(c.get("statement", ""))}
                for c in children
            ],
            decomp_id=decomp_id,
        )
        cached = registry.cache_lookup(cache_key)
        if cached is not None:
            result = dict(cached)
            result["cache_hit"] = True
            if result.get("outcome") == "ok":
                try:
                    await dag.accept_decomposition(decomp_id, job_id)
                except DagError as exc:
                    log.warning("cached decomposition %s not accepted: %s", decomp_id, exc)
            await registry.submit(job)
            await registry.mark_finished(job, result)
            return _ok(job_id=job_id, decomp_id=decomp_id, status="done", result=result)
        await runner.enqueue(job)
        return _ok(job_id=job_id, decomp_id=decomp_id, status="queued", _status=202)

    async def finding(request: web.Request) -> web.Response:
        data = await _read_json(request)
        if not isinstance(data, dict):
            return _err("invalid JSON body")
        cid = data.get("contestant_id", "")
        if not _valid_contestant(cid):
            return _err(f"unknown contestant_id {cid!r}")
        text = data.get("text")
        if not isinstance(text, str) or not text.strip():
            return _err("missing 'text'")
        rec = await state.append_finding(cid, text)
        return _ok(id=rec["id"])

    async def breakthrough(request: web.Request) -> web.Response:
        data = await _read_json(request)
        if not isinstance(data, dict):
            return _err("invalid JSON body")
        cid = data.get("contestant_id", "")
        if not _valid_contestant(cid):
            return _err(f"unknown contestant_id {cid!r}")
        text = data.get("text")
        if not isinstance(text, str) or not text.strip():
            return _err("missing 'text'")
        job_id = data.get("job_id")
        if isinstance(job_id, str) and job_id:
            via = promotion.kernel_promotion(registry, job_id)
            if via is not None:
                rec = await state.append_breakthrough(cid, text, promoted_via=via)
                return _ok(promoted=True, via=via, id=rec["id"])
            await state.append_finding(
                cid, f"[unpromoted breakthrough candidate — job {job_id} not ok] {text}"
            )
            return _ok(promoted=False, via=None)
        await state.append_finding(
            cid, f"[unpromoted breakthrough candidate — no kernel evidence cited] {text}"
        )
        return _ok(promoted=False, via=None)

    async def direction(request: web.Request) -> web.Response:
        data = await _read_json(request)
        if not isinstance(data, dict):
            return _err("invalid JSON body")
        cid = data.get("contestant_id", "")
        if not _valid_contestant(cid):
            return _err(f"unknown contestant_id {cid!r}")
        text = data.get("text")
        if not isinstance(text, str):
            return _err("missing 'text'")
        await state.write_direction(cid, text)
        return _ok()

    async def journal(request: web.Request) -> web.Response:
        data = await _read_json(request)
        if not isinstance(data, dict):
            return _err("invalid JSON body")
        cid = data.get("contestant_id", "")
        if not _valid_contestant(cid):
            return _err(f"unknown contestant_id {cid!r}")
        text = data.get("text")
        if not isinstance(text, str) or not text.strip():
            return _err("missing 'text'")
        await state.append_journal(cid, text)
        return _ok()

    async def turn(request: web.Request) -> web.Response:
        data = await _read_json(request)
        if not isinstance(data, dict):
            return _err("invalid JSON body")
        cid = data.get("contestant_id", "")
        if not _valid_contestant(cid):
            return _err(f"unknown contestant_id {cid!r}")
        record = data.get("record")
        if not isinstance(record, dict):
            record = {}
        record.setdefault("contestant_id", cid)
        record.setdefault("ts", _now_iso())
        await state.append_turn(cid, record)
        rnd = await state.round_counter()
        if cfg.historian.enabled and rnd > 0 and rnd % cfg.historian.every_n_rounds == 0:
            asyncio.create_task(historian.write_digest(state))
        return _ok(round=rnd)

    async def finish(request: web.Request) -> web.Response:
        """The mechanical finish gate. The daemon assembles the full proof
        from stored node sources, rebuilds it strictly (no stubs; sorryAx
        forbidden), audits axioms, and checks the root against the frozen
        goal by defeq. A finish proposal exists only if that build is green;
        peer verification then covers statement fidelity."""
        if runner is None or workspace is None:
            return _err("kernel is disabled for this challenge", status=409)
        data = await _read_json(request)
        if not isinstance(data, dict):
            return _err("invalid JSON body")
        cid = data.get("contestant_id", "")
        if not _valid_contestant(cid):
            return _err(f"unknown contestant_id {cid!r}")
        text = data.get("text")
        if not isinstance(text, str) or not text.strip():
            return _err("finish requires 'text' containing the solution summary")

        final_source, missing = assembly.build_final_assembly(dag, state)
        if missing:
            return _err(
                "finish rejected: the proof DAG is not complete — unproved or "
                f"unstored nodes: {missing}",
                status=409,
            )
        root = dag.nodes[dag.root_id]  # type: ignore[index]
        source_sha = sha256_text(final_source)
        job_id = registry.new_job_id(cid, source_sha)
        cache_key = check_cache_key(
            challenge=cfg.challenge.name,
            fingerprint=workspace.fingerprint,
            mode="final",
            target=cfg.hygiene.goal_def,
            source_sha256=source_sha,
            flags={"max_heartbeats": cfg.hygiene.max_heartbeats},
        )
        job = Job(
            job_id=job_id,
            contestant_id=cid,
            mode="final",
            cache_key=cache_key,
            source_sha256=source_sha,
            node_id=dag.root_id,
            decl=root.name,
            statement=cfg.hygiene.goal_def,
            timeout=cfg.kernel.max_timeout_seconds,
            source=final_source,
            raw_source=final_source,
            finish_text=text,
        )
        # defense-in-depth: re-scan the assembly (mode final forbids sorry)
        scan = scan_source(final_source, cfg.hygiene, "final")
        if not scan.ok:
            return _err(
                "finish rejected: assembled proof failed the hygiene scan: "
                + "; ".join(scan.violations),
                status=409,
            )
        await runner.enqueue(job)
        log.info("[%s] final assembly enqueued as job %s", cid, job_id)
        return _ok(job_id=job_id, status="queued", needs_verification=True, _status=202)

    async def verify_finish(request: web.Request) -> web.Response:
        data = await _read_json(request)
        if not isinstance(data, dict):
            return _err("invalid JSON body")
        cid = data.get("contestant_id", "")
        if not _valid_contestant(cid):
            return _err(f"unknown contestant_id {cid!r}")
        proposal_id = data.get("proposal_id")
        if not isinstance(proposal_id, str) or not proposal_id.strip():
            return _err("verify requires 'proposal_id'")
        proposal = state.finish_proposal(proposal_id)
        if proposal is None:
            return _err(f"unknown finish proposal {proposal_id!r}", status=404)
        if proposal.get("contestant_id") == cid:
            return _err("finish proposal must be verified by the other contestant")
        agree = data.get("agree")
        if not isinstance(agree, bool):
            return _err("verify requires boolean 'agree'")
        reason = data.get("reason", "")
        if not isinstance(reason, str):
            return _err("verify 'reason' must be a string")
        if not agree and not reason.strip():
            return _err("rejected verification requires a reason")
        if agree:
            # cheap mechanical re-checks before SOLVED can be written
            fjob_id = proposal.get("job_id")
            fjob = registry.get(fjob_id) if isinstance(fjob_id, str) else None
            fjob_ok = (
                fjob is not None
                and fjob.status == "done"
                and (fjob.result or {}).get("outcome") == "ok"
            )
            if not fjob_ok:
                return _err(
                    "cannot agree: the proposal's final-assembly job is not a "
                    "finished, successful kernel run",
                    status=409,
                )
            if workspace is not None and workspace.frozen_hashes != workspace._hash_frozen():
                return _err(
                    "cannot agree: frozen workspace files changed since materialization",
                    status=409,
                )

        rec = await state.append_finish_verification(cid, proposal_id, agree, reason)
        if agree:
            solved_record = {
                "ts": _now_iso(),
                "proposal": proposal,
                "verification": rec,
            }
            await state.mark_solved(solved_record)
            log.info("[%s] verified finish proposal %s; SOLVED flag set", cid, proposal_id)
            return _ok(solved=True, proposal_id=proposal_id, verification_id=rec["id"])
        log.info("[%s] rejected finish proposal %s", cid, proposal_id)
        return _ok(solved=False, proposal_id=proposal_id, verification_id=rec["id"])

    app = web.Application()
    app.router.add_get("/health", health)
    app.router.add_get("/problem", problem)
    app.router.add_get("/snapshot", snapshot)
    app.router.add_post("/check", check)
    app.router.add_get("/check/{job_id}", check_status)
    app.router.add_get("/dag", dag_view)
    app.router.add_post("/dag/node", dag_node)
    app.router.add_post("/dag/claim", dag_claim)
    app.router.add_post("/dag/release", dag_release)
    app.router.add_post("/dag/accept", dag_accept)
    app.router.add_post("/dag/decompose", dag_decompose)
    app.router.add_post("/finding", finding)
    app.router.add_post("/breakthrough", breakthrough)
    app.router.add_post("/direction", direction)
    app.router.add_post("/journal", journal)
    app.router.add_post("/turn", turn)
    app.router.add_post("/finish", finish)
    app.router.add_post("/verify", verify_finish)

    web_runner = web.AppRunner(app)
    await web_runner.setup()
    site = web.TCPSite(web_runner, host, port)
    await site.start()
    log.info("daemon listening on http://%s:%d", host, port)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass
    try:
        await stop_event.wait()
    finally:
        log.info("daemon shutting down")
        await web_runner.cleanup()
        if runner is not None:
            await runner.stop()
    return 0


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Lean-kernel + blackboard daemon")
    ap.add_argument(
        "--challenge",
        required=True,
        help="challenge directory name under challenges/",
    )
    ap.add_argument("--host", default=None, help="override daemon.host from config")
    ap.add_argument("--port", type=int, default=None, help="override daemon.port from config")
    ap.add_argument(
        "--lease-ttl-seconds", type=int, default=None,
        help="override kernel.lease_ttl_seconds (used by the smoke test)",
    )
    return ap.parse_args()


def main() -> int:
    args = parse_args()
    try:
        return asyncio.run(main_async(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
