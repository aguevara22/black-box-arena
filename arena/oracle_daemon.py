"""Sealed-oracle + blackboard daemon — the SOLE writer of state/<challenge>/**.

Wraps StateManager + SealedOracleRunner + promotion + historian behind a small
localhost HTTP API. Two independent contestant sessions (e.g. `codex` and
`claude`) talk to it via client.py. Because every write funnels through this one
process and event loop, the single-writer assumption baked into state_manager.py
holds, and concurrent contestants cannot corrupt the shared files.

The daemon NEVER imports the challenge oracle. Only the SealedOracleRunner's
subprocess (oracle_worker.py) loads challenges/<name>/oracle.py. Contestants
receive only oracle ANSWERS — that is the seal.
"""
from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import logging
import re
import signal
from datetime import datetime, timezone
from typing import Any

from aiohttp import web
from dotenv import load_dotenv

try:
    from . import historian, promotion
    from .config import FinishGateConfig, build_input_validator, load_config
    from .oracle_runner import SealedOracleRunner
    from .state_manager import StateManager
    from .utils import logging as logsetup
    from .utils import paths
except ImportError:  # direct script execution
    import historian
    import promotion
    from config import FinishGateConfig, build_input_validator, load_config
    from oracle_runner import SealedOracleRunner
    from state_manager import StateManager
    from utils import logging as logsetup
    from utils import paths

log = logging.getLogger("daemon")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dumps(obj: Any) -> str:
    return json.dumps(obj, default=str)


def _ok(**kw: Any) -> web.Response:
    payload: dict[str, Any] = {"ok": True}
    payload.update(kw)
    return web.json_response(payload, dumps=_dumps)


def _err(message: str, status: int = 400) -> web.Response:
    return web.json_response({"ok": False, "error": message}, status=status, dumps=_dumps)


EVIDENCE_ROW_RE = re.compile(
    r"^EVIDENCE:\s*"
    r"input=(?P<input>.+?)\s*\|\s*"
    r"tags=(?P<tags>[^|]*?)\s*\|\s*"
    r"oracle=(?P<oracle>wall|[+-]?\d+)\s*\|\s*"
    r"proposed=(?P<proposed>wall|[+-]?\d+)\s*\|\s*"
    r"match=(?P<match>yes|no)\s*$",
    flags=re.IGNORECASE,
)


def _parse_evidence_line(line: str) -> dict[str, Any]:
    match = EVIDENCE_ROW_RE.fullmatch(line.strip())
    if match is None:
        raise ValueError(
            "expected EVIDENCE: input=<json> | tags=<comma-list> | "
            "oracle=<int or wall> | proposed=<int or wall> | match=yes|no"
        )
    try:
        input_payload = json.loads(match.group("input"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"input is not valid JSON: {exc.msg}") from exc
    tags = [tag.strip() for tag in match.group("tags").split(",")]
    if not tags or any(not tag for tag in tags):
        raise ValueError("tags must be a nonempty comma-list")

    def value(raw: str) -> int | str:
        return "wall" if raw.lower() == "wall" else int(raw)

    return {
        "input": input_payload,
        "tags": tags,
        "oracle": value(match.group("oracle")),
        "proposed": value(match.group("proposed")),
        "match": match.group("match").lower(),
    }


def _finish_gate_errors(text: str, gate: FinishGateConfig | None) -> list[str]:
    if gate is None:
        return []

    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    evidence_lines = [
        (line_number, line)
        for line_number, line in enumerate(text.splitlines(), start=1)
        if line.lstrip().lower().startswith("evidence:")
    ]
    for line_number, line in evidence_lines:
        try:
            rows.append(_parse_evidence_line(line))
        except ValueError as exc:
            errors.append(f"line {line_number}: {exc}")

    matching = [row for row in rows if row["match"] == "yes"]
    if len(matching) < gate.min_rows:
        errors.append(
            f"needs at least {gate.min_rows} parsed match=yes rows; found {len(matching)}"
        )
    covered = {tag for row in matching for tag in row["tags"]}
    missing_tags = [tag for tag in gate.required_tags if tag not in covered]
    if missing_tags:
        errors.append(f"missing required tags on match=yes rows: {', '.join(missing_tags)}")
    return errors


async def main_async(args: argparse.Namespace) -> int:
    load_dotenv()
    cfg = load_config(paths.config_path(args.challenge))
    paths.ensure_state_layout(args.challenge)
    logsetup.setup(paths.state_dir(args.challenge))

    state = StateManager(args.challenge)
    contestant_ids = {c.id for c in cfg.contestants}

    oracle: SealedOracleRunner | None = None
    validator = None
    if cfg.oracle.enabled:
        oracle = SealedOracleRunner(args.challenge, default_timeout=cfg.oracle.timeout_seconds)
        await oracle.start()
        validator = build_input_validator(cfg.oracle.input_schema)

    # Serializes the whole validate -> cache -> predict -> query -> record -> promote
    # sequence so two contestants hitting the same input can't double-execute.
    oracle_lock = asyncio.Lock()

    host = args.host or cfg.daemon.host
    port = args.port or cfg.daemon.port

    log.info(
        "daemon starting | challenge=%s oracle_enabled=%s contestants=%s addr=%s:%d",
        args.challenge, cfg.oracle.enabled, sorted(contestant_ids), host, port,
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

    async def _oracle_query_with_restart(input_payload: Any, timeout: int) -> dict[str, Any]:
        assert oracle is not None
        res = await oracle.query(input_payload, timeout=timeout)
        retryable_reasons = {
            "transport_exception",
            "transport_timeout",
            "protocol_mismatch",
        }
        if isinstance(res, dict) and res.get("status") == "error" and res.get("reason") in retryable_reasons:
            log.warning(
                "oracle transport/protocol error (%s: %s); restarting seal subprocess and retrying once",
                res.get("reason"),
                res.get("detail"),
            )
            try:
                await oracle.stop()
                await oracle.start()
            except Exception as exc:  # noqa: BLE001
                log.error("seal restart failed: %s", exc)
                return res
            res = await oracle.query(input_payload, timeout=timeout)
        return res

    # ---- handlers ----

    async def health(request: web.Request) -> web.Response:
        return _ok(
            challenge=cfg.challenge.name,
            oracle_enabled=cfg.oracle.enabled,
            solved=state.is_solved_flag_present(),
            rounds_done=_rounds_done(),
            contestants=sorted(contestant_ids),
        )

    async def problem(request: web.Request) -> web.Response:
        return _ok(
            problem=state.problem(),
            oracle_description=cfg.oracle.description,
            oracle_enabled=cfg.oracle.enabled,
        )

    async def snapshot(request: web.Request) -> web.Response:
        cid = request.query.get("contestant_id", "")
        if not _valid_contestant(cid):
            return _err(f"unknown contestant_id {cid!r}; expected one of {sorted(contestant_ids)}")
        snap = state.snapshot(cid)
        return _ok(
            contestant_id=cid,
            snapshot=dataclasses.asdict(snap),
            solved=state.is_solved_flag_present(),
            rounds_done=_rounds_done(),
            oracle_enabled=cfg.oracle.enabled,
            oracle_description=cfg.oracle.description,
        )

    async def oracle_query(request: web.Request) -> web.Response:
        if oracle is None:
            return _err("oracle is disabled for this challenge", status=409)
        data = await _read_json(request)
        if not isinstance(data, dict):
            return _err("invalid JSON body")
        cid = data.get("contestant_id", "")
        if not _valid_contestant(cid):
            return _err(f"unknown contestant_id {cid!r}")
        if "input" not in data:
            return _err("missing 'input'")
        input_payload = data["input"]
        predict = data.get("predict")
        hypothesis = data.get("hypothesis")
        timeout = int(data.get("timeout") or cfg.oracle.timeout_seconds)

        async with oracle_lock:
            if validator is not None:
                errs = sorted(validator.iter_errors(input_payload), key=lambda e: list(e.path))
                if errs:
                    detail = "; ".join(e.message for e in errs[:3])
                    result = {"status": "error", "reason": "schema_violation", "detail": detail}
                    await state.record_oracle_result(cid, input_payload, result, predict=predict)
                    return _ok(result=result, cache_hit=False, prediction_correct=None, promoted_breakthrough=False)

            cached = state.oracle_cache_lookup(input_payload)
            if cached is not None:
                rec = await state.record_oracle_result(cid, input_payload, cached, predict=predict, cache_hit=True)
                promoted = bool(rec.get("prediction_correct") and hypothesis)
                if promoted:
                    await state.append_breakthrough(cid, hypothesis, promoted_via="predictive_match")
                return _ok(
                    result=cached, cache_hit=True,
                    prediction_correct=rec.get("prediction_correct"),
                    promoted_breakthrough=promoted,
                )

            if predict is not None:
                await state.record_oracle_prediction(cid, input_payload, predict, hypothesis)

            res = await _oracle_query_with_restart(input_payload, timeout)
            rec = await state.record_oracle_result(cid, input_payload, res, predict=predict)
            promoted = bool(rec.get("prediction_correct") and hypothesis)
            if promoted:
                await state.append_breakthrough(cid, hypothesis, promoted_via="predictive_match")
            return _ok(
                result=res, cache_hit=False,
                prediction_correct=rec.get("prediction_correct"),
                promoted_breakthrough=promoted,
            )

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
        promoted = await promotion.evaluate_breakthrough_candidates(state, cid, [text], cfg.breakthroughs)
        if promoted:
            ptext, via = promoted[0]
            rec = await state.append_breakthrough(cid, ptext, promoted_via=via)
            return _ok(promoted=True, via=via, id=rec["id"])
        await state.append_finding(cid, f"[unpromoted breakthrough candidate] {text}")
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
        data = await _read_json(request)
        if not isinstance(data, dict):
            return _err("invalid JSON body")
        cid = data.get("contestant_id", "")
        if not _valid_contestant(cid):
            return _err(f"unknown contestant_id {cid!r}")
        text = data.get("text")
        if not isinstance(text, str) or not text.strip():
            return _err("finish requires 'text' containing the algorithm and verification target")
        if cfg.finish_gate is not None:
            evidence_errors = _finish_gate_errors(text, cfg.finish_gate)
            if evidence_errors:
                return _err(
                    "finish rejected by finish_gate before verification: "
                    f"{'; '.join(evidence_errors)}",
                    status=409,
                )
        rec = await state.append_finish_proposal(cid, text)
        log.info("[%s] finish proposal posted: %s", cid, rec["id"])
        return _ok(id=rec["id"], solved=False, needs_verification=True)

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
        if (
            agree
            and cfg.finish_gate is not None
        ):
            evidence_errors = _finish_gate_errors(
                str(proposal.get("text", "")), cfg.finish_gate
            )
            if evidence_errors:
                return _err(
                    "cannot agree to finish: stored proposal no longer satisfies finish_gate: "
                    f"{'; '.join(evidence_errors)}",
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
    app.router.add_post("/oracle", oracle_query)
    app.router.add_post("/finding", finding)
    app.router.add_post("/breakthrough", breakthrough)
    app.router.add_post("/direction", direction)
    app.router.add_post("/journal", journal)
    app.router.add_post("/turn", turn)
    app.router.add_post("/finish", finish)
    app.router.add_post("/verify", verify_finish)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
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
        await runner.cleanup()
        if oracle is not None:
            await oracle.stop()
    return 0


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Sealed-oracle + blackboard daemon")
    ap.add_argument(
        "--challenge",
        default="ising_lift",
        help="challenge directory name under challenges/ (default: ising_lift)",
    )
    ap.add_argument("--host", default=None, help="override daemon.host from config")
    ap.add_argument("--port", type=int, default=None, help="override daemon.port from config")
    return ap.parse_args()


def main() -> int:
    args = parse_args()
    try:
        return asyncio.run(main_async(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
