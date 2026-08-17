"""Async executor for Lean check jobs.

A fixed pool of worker tasks drains an asyncio.Queue. Per job:
augment the submitted source with a daemon-controlled trailer (fidelity
check + `#print axioms`), write it as a candidate file, compile with
`lake env lean --json`, parse diagnostics, run the axiom audit, record the
verdict. The trailer starts at a line the daemon knows, and only audit
messages at or below that line are trusted.

Job modes:
  eval      compute values against the frozen Defs (#eval/#reduce/decide) —
            the successor of the old numeric oracle. No trailer, no audit.
  proof     prove a declaration. Trailer: fidelity theorem (when a statement
            is registered) + #print axioms. sorryAx forbidden unless the
            proof was compiled against stubbed dependencies (M2).
  skeleton  decomposition check: children may be `sorry`; sorryAx allowed.
  final     daemon-assembled full build (M3); strict audit.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable

try:
    from . import hygiene, lean_diagnostics
    from .config import HygieneConfig, KernelConfig
    from .jobs import Job, JobRegistry
    from .lean_workspace import LeanWorkspace
except ImportError:  # direct module execution
    import hygiene
    import lean_diagnostics
    from config import HygieneConfig, KernelConfig
    from jobs import Job, JobRegistry
    from lean_workspace import LeanWorkspace

log = logging.getLogger("daemon.kernel")

FIDELITY_DECL = "_arena_fidelity"


def build_trailer(job: Job) -> tuple[str, str | None]:
    """Return (trailer_text, audited_decl). The audited decl is the fidelity
    theorem when a statement is registered (its axioms subsume the target's),
    else the bare decl. Skeleton jobs additionally require every declared
    child to exist with its declared statement (defeq elaboration)."""
    if job.mode == "eval" or not job.decl:
        return ("", None)
    lines = ["", "-- arena audit trailer (daemon-appended; do not imitate)"]
    for i, child in enumerate(job.children):
        lines.append(
            f"example : {child['statement']} := {child['name']}"
        )
    audited = job.decl
    if job.statement:
        lines.append(f"theorem {FIDELITY_DECL} : {job.statement} := {job.decl}")
        audited = FIDELITY_DECL
    lines.append(f"#print axioms {audited}")
    return ("\n".join(lines) + "\n", audited)


class KernelRunner:
    def __init__(
        self,
        workspace: LeanWorkspace,
        kernel_cfg: KernelConfig,
        hygiene_cfg: HygieneConfig,
        registry: JobRegistry,
        on_finished: Callable[[Job, dict[str, Any]], Awaitable[None]] | None = None,
    ):
        self.workspace = workspace
        self.kernel_cfg = kernel_cfg
        self.hygiene_cfg = hygiene_cfg
        self.registry = registry
        self.on_finished = on_finished
        self.queue: asyncio.Queue[Job] = asyncio.Queue()
        self._workers: list[asyncio.Task[None]] = []

    def start(self) -> None:
        for i in range(max(1, self.kernel_cfg.max_concurrent_builds)):
            self._workers.append(asyncio.create_task(self._worker(i)))

    async def stop(self) -> None:
        for task in self._workers:
            task.cancel()
        for task in self._workers:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._workers.clear()

    async def enqueue(self, job: Job) -> None:
        await self.registry.submit(job)
        await self.queue.put(job)

    async def _worker(self, index: int) -> None:
        while True:
            job = await self.queue.get()
            try:
                await self.registry.mark_started(job)
                result = await self._execute(job)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — a job must never kill the worker
                log.exception("worker %d: job %s crashed", index, job.job_id)
                result = {
                    "outcome": "failed",
                    "failure_kind": "internal",
                    "detail": f"{type(exc).__name__}: {exc}",
                }
            await self._finish(job, result)

    async def _finish(self, job: Job, result: dict[str, Any]) -> None:
        result.setdefault("toolchain_fingerprint", self.workspace.fingerprint)
        if job.predict in ("ok", "fail"):
            outcome_ok = result.get("outcome") == "ok"
            result["prediction_correct"] = (job.predict == "ok") == outcome_ok
        # Hook runs BEFORE the job_finished event so that side effects it
        # persists (proof sources, DAG status) exist by the time the job log
        # says the job is done — the log never references missing artifacts.
        if self.on_finished is not None:
            try:
                await self.on_finished(job, result)
            except Exception:  # noqa: BLE001
                log.exception("on_finished hook failed for job %s", job.job_id)
        await self.registry.mark_finished(job, result)
        job.source = ""
        job.raw_source = ""

    async def _execute(self, job: Job) -> dict[str, Any]:
        started = time.time()
        trailer, audited = build_trailer(job)
        source = job.source
        trailer_start_line = source.count("\n") + 2  # trailer begins after source
        full = source + ("\n" if not source.endswith("\n") else "") + trailer

        path = self.workspace.write_candidate(job.job_id, full)
        try:
            code, stdout, stderr = await self.workspace.compile_candidate(
                path, timeout=job.timeout
            )
        finally:
            self.workspace.remove_candidate(job.job_id)

        duration = round(time.time() - started, 2)
        if code == -1 and "timeout" in stderr:
            return {
                "outcome": "failed",
                "failure_kind": "timeout",
                "detail": stderr,
                "duration_s": duration,
            }

        diags = lean_diagnostics.parse_output(stdout)
        errs = lean_diagnostics.errors(diags)
        record: dict[str, Any] = {
            "duration_s": duration,
            "diagnostics": lean_diagnostics.truncate_for_record(diags),
        }
        if job.mode == "eval":
            # eval output (the #eval results) arrives as information messages
            record["infos"] = [
                d.as_record() for d in diags if d.severity == "information"
            ][: lean_diagnostics.MAX_DIAGNOSTICS]

        if errs or code not in (0,):
            if not errs and code != 0:
                record["detail"] = f"lean exited {code}: {stderr[-2000:]}"
            record["outcome"] = "failed"
            record["failure_kind"] = "compile_error"
            return record

        if audited is not None:
            axiom_map = lean_diagnostics.extract_axioms(diags, min_line=trailer_start_line)
            if audited not in axiom_map:
                record["outcome"] = "failed"
                record["failure_kind"] = "hygiene_audit"
                record["detail"] = (
                    f"audit message for {audited} not found in kernel output "
                    f"(expected at/after line {trailer_start_line})"
                )
                return record
            allow_sorry = job.mode == "skeleton" or bool(job.stubbed_deps)
            audit = hygiene.audit_axioms(
                audited, axiom_map[audited], self.hygiene_cfg, allow_sorry=allow_sorry
            )
            record["axioms"] = {audited: axiom_map[audited]}
            record["fidelity_ok"] = bool(job.statement)
            if job.stubbed_deps:
                record["proved_modulo"] = list(job.stubbed_deps)
            if not audit.ok:
                record["outcome"] = "failed"
                record["failure_kind"] = "hygiene_audit"
                record["detail"] = "; ".join(audit.violations)
                return record

        record["outcome"] = "ok"
        return record
