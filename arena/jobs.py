"""Check-job registry: in-memory index over the append-only jobs.jsonl log.

The daemon is the sole writer. Every lifecycle transition is an appended
event (job_submitted / job_started / job_finished); the registry replays the
log once at startup — crash recovery marks jobs that were in flight as
failed (contestants resubmit; the cache makes completed work free).

Cache: content-addressed by protocol.check_cache_key (which embeds the
toolchain fingerprint). Deterministic outcomes (ok, compile_error,
hygiene failures) are cached; timeouts and internal errors are not —
they may be load-dependent.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

try:
    from .protocol import short_hash
    from .state_manager import StateManager
except ImportError:  # direct module execution
    from protocol import short_hash
    from state_manager import StateManager

CACHEABLE_FAILURES = {"compile_error", "hygiene_source", "hygiene_audit"}
TERMINAL = "done"


@dataclass
class Job:
    job_id: str
    contestant_id: str
    mode: str
    cache_key: str
    source_sha256: str
    node_id: str | None = None
    decl: str | None = None
    statement: str | None = None
    predict: str | None = None
    hypothesis: str | None = None
    timeout: int = 120
    source: str = ""  # composed file the kernel compiles; never logged raw
    raw_source: str = ""  # the contestant's own submission (persisted on proved nodes)
    children: list[dict[str, str]] = field(default_factory=list)  # skeleton jobs
    decomp_id: str | None = None  # skeleton jobs: proposal to accept on ok
    stubbed_deps: list[str] = field(default_factory=list)  # proof jobs: unproved children
    finish_text: str = ""  # final jobs: the proposer's solution summary
    status: str = "queued"  # queued | building | done
    result: dict[str, Any] | None = None
    submitted_ts: float = field(default_factory=time.time)

    def public_view(self) -> dict[str, Any]:
        view: dict[str, Any] = {
            "job_id": self.job_id,
            "contestant_id": self.contestant_id,
            "mode": self.mode,
            "node_id": self.node_id,
            "status": self.status,
        }
        if self.result is not None:
            view["result"] = self.result
        return view


class JobRegistry:
    def __init__(self, state: StateManager):
        self.state = state
        self.jobs: dict[str, Job] = {}
        self.cache: dict[str, dict[str, Any]] = {}

    # ---- startup ----

    def recover(self) -> int:
        """Replay jobs.jsonl: rebuild the cache index and count jobs that were
        in flight when a previous daemon died (their submitters see status
        done/failed{daemon_restart} if they poll old ids)."""
        interrupted = 0
        for rec in self.state.iter_job_records():
            event = rec.get("event")
            job_id = rec.get("job_id", "")
            if event == "job_finished":
                result = rec.get("result") or {}
                key = rec.get("cache_key", "")
                if key and self._cacheable(result):
                    self.cache[key] = result
                if job_id in self.jobs:
                    del self.jobs[job_id]
            elif event in ("job_submitted", "job_started") and job_id:
                self.jobs[job_id] = Job(
                    job_id=job_id,
                    contestant_id=rec.get("contestant_id", ""),
                    mode=rec.get("mode", ""),
                    cache_key=rec.get("cache_key", ""),
                    source_sha256=rec.get("source_sha256", ""),
                    node_id=rec.get("node_id"),
                    status="building",
                )
        for job in self.jobs.values():
            job.status = TERMINAL
            job.result = {
                "outcome": "failed",
                "failure_kind": "daemon_restart",
                "detail": "daemon restarted while this job was in flight; resubmit",
            }
            interrupted += 1
        return interrupted

    @staticmethod
    def _cacheable(result: dict[str, Any]) -> bool:
        if result.get("outcome") == "ok":
            return True
        return result.get("failure_kind") in CACHEABLE_FAILURES

    # ---- lifecycle ----

    def new_job_id(self, contestant_id: str, source_sha256: str) -> str:
        return "J" + short_hash(
            {"c": contestant_id, "s": source_sha256, "t": time.time()}
        )

    def cache_lookup(self, cache_key: str) -> dict[str, Any] | None:
        return self.cache.get(cache_key)

    async def submit(self, job: Job) -> None:
        self.jobs[job.job_id] = job
        await self.state.append_job_event(
            {
                "event": "job_submitted",
                "job_id": job.job_id,
                "contestant_id": job.contestant_id,
                "mode": job.mode,
                "node_id": job.node_id,
                "decl": job.decl,
                "cache_key": job.cache_key,
                "source_sha256": job.source_sha256,
                "predict": job.predict,
                "hypothesis": job.hypothesis,
                "timeout": job.timeout,
            }
        )

    async def mark_started(self, job: Job) -> None:
        job.status = "building"
        await self.state.append_job_event(
            {"event": "job_started", "job_id": job.job_id, "contestant_id": job.contestant_id}
        )

    async def mark_finished(self, job: Job, result: dict[str, Any]) -> None:
        job.status = TERMINAL
        job.result = result
        if self._cacheable(result):
            self.cache[job.cache_key] = result
        await self.state.append_job_event(
            {
                "event": "job_finished",
                "job_id": job.job_id,
                "contestant_id": job.contestant_id,
                "mode": job.mode,
                "node_id": job.node_id,
                "decl": job.decl,
                "cache_key": job.cache_key,
                "source_sha256": job.source_sha256,
                "predict": job.predict,
                "result": result,
            }
        )

    def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)

    def queue_depth(self) -> int:
        return sum(1 for j in self.jobs.values() if j.status in ("queued", "building"))
