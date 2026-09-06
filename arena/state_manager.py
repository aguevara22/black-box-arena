from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from .oracle_protocol import ORACLE_CACHE_VERSION, ORACLE_PROTOCOL_VERSION, oracle_input_hash
    from .protocol import canonical_json, sha256_text, short_hash
    from .utils import jsonl, paths
except ImportError:  # direct module execution
    from oracle_protocol import ORACLE_CACHE_VERSION, ORACLE_PROTOCOL_VERSION, oracle_input_hash
    from protocol import canonical_json, sha256_text, short_hash
    from utils import jsonl, paths


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canon(obj: Any) -> str:
    return canonical_json(obj)


def _hash(obj: Any) -> str:
    return short_hash(obj)


@dataclass
class BlackboardSnapshot:
    problem: str
    journal: str
    direction: str
    recent_breakthroughs: list[dict[str, Any]]
    recent_findings: list[dict[str, Any]]
    recent_finish_records: list[dict[str, Any]]
    recent_jobs: list[dict[str, Any]]
    recent_oracle_log: list[dict[str, Any]]
    last_reply: dict[str, Any] | None
    digest: str


class StateManager:
    def __init__(self, challenge: str):
        self.challenge = challenge
        self.shared = paths.shared_dir(challenge)
        self.state = paths.state_dir(challenge)
        self.lock = asyncio.Lock()
        paths.ensure_state_layout(challenge)
        self._problem_text = paths.problem_path(challenge).read_text(encoding="utf-8")

    # ---- read paths ----

    def problem(self) -> str:
        return self._problem_text

    def contestant_dir(self, contestant_id: str) -> Path:
        d = paths.contestant_state_dir(self.challenge, contestant_id)
        d.mkdir(parents=True, exist_ok=True)
        for fname, default in (
            ("journal.md", ""),
            ("direction.md", ""),
        ):
            f = d / fname
            if not f.exists():
                f.write_text(default, encoding="utf-8")
        return d

    def journal(self, contestant_id: str) -> str:
        return (self.contestant_dir(contestant_id) / "journal.md").read_text(encoding="utf-8")

    def direction(self, contestant_id: str) -> str:
        return (self.contestant_dir(contestant_id) / "direction.md").read_text(encoding="utf-8")

    def digest(self) -> str:
        path = self.shared / "digest.md"
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def recent_breakthroughs(self, n: int = 10) -> list[dict[str, Any]]:
        return jsonl.tail(self.shared / "breakthroughs.jsonl", n)

    def recent_findings(self, n: int = 20) -> list[dict[str, Any]]:
        return jsonl.tail(self.shared / "findings.jsonl", n)

    def recent_finish_records(self, n: int = 10) -> list[dict[str, Any]]:
        return jsonl.tail(self.shared / "finish.jsonl", n)

    def recent_jobs(self, n: int = 30) -> list[dict[str, Any]]:
        return jsonl.tail(self.shared / "jobs.jsonl", n)

    def iter_job_records(self):
        return jsonl.iter_records(self.shared / "jobs.jsonl")

    def recent_dag_events(self, n: int = 50) -> list[dict[str, Any]]:
        return jsonl.tail(self.shared / "dag.jsonl", n)

    def iter_dag_records(self):
        return jsonl.iter_records(self.shared / "dag.jsonl")

    def recent_oracle_log(self, n: int = 30) -> list[dict[str, Any]]:
        return jsonl.tail(self.shared / "oracle_log.jsonl", n)

    def last_reply(self, contestant_id: str) -> dict[str, Any] | None:
        turns = jsonl.tail(self.contestant_dir(contestant_id) / "turns.jsonl", 1)
        return turns[0] if turns else None

    def snapshot(self, contestant_id: str) -> BlackboardSnapshot:
        return BlackboardSnapshot(
            problem=self.problem(),
            journal=self.journal(contestant_id),
            direction=self.direction(contestant_id),
            recent_breakthroughs=self.recent_breakthroughs(),
            recent_findings=self.recent_findings(),
            recent_finish_records=self.recent_finish_records(),
            recent_jobs=self.recent_jobs(),
            recent_oracle_log=self.recent_oracle_log(),
            last_reply=self.last_reply(contestant_id),
            digest=self.digest(),
        )

    # ---- oracle log (ground_truth: oracle) ----
    # Every oracle call is append-only and correlated: the cache honours only
    # records whose response carries this challenge, a request id and the
    # input hash under the current cache version. Records from before a
    # protocol version are legacy and never served from cache (trust epochs).

    async def record_oracle_prediction(
        self,
        contestant_id: str,
        input_payload: Any,
        predict: Any,
        hypothesis: str | None,
    ) -> dict[str, Any]:
        rec = {
            "ts": _ts(),
            "phase": "predicted",
            "contestant_id": contestant_id,
            "input": input_payload,
            "input_hash": oracle_input_hash(self.challenge, input_payload),
            "predict": predict,
            "hypothesis": hypothesis,
            "predicted_at": _ts(),
        }
        async with self.lock:
            jsonl.append(self.shared / "oracle_log.jsonl", rec)
            with open(self.shared / "oracle_log.md", "a", encoding="utf-8") as f:
                f.write(
                    f"\n### {rec['ts']} — {contestant_id} predicted\n"
                    f"input: {_canon(input_payload)}\n"
                    f"prediction: {_canon(predict)}\n"
                    f"hypothesis: {hypothesis or ''}\n"
                )
        return rec

    async def record_oracle_result(
        self,
        contestant_id: str,
        input_payload: Any,
        result: dict[str, Any],
        predict: Any = None,
        cache_hit: bool = False,
    ) -> dict[str, Any]:
        ih = oracle_input_hash(self.challenge, input_payload)
        prediction_correct: bool | None = None
        if predict is not None and result.get("status") == "ok":
            prediction_correct = _canon(predict) == _canon(result.get("output"))
        protocol_verified = self._result_matches_input(result, ih)
        rec = {
            "ts": _ts(),
            "phase": "executed",
            "contestant_id": contestant_id,
            "input": input_payload,
            "input_hash": ih,
            "cache_version": ORACLE_CACHE_VERSION,
            "predict": predict,
            "prediction_correct": prediction_correct,
            "result": result,
            "oracle_protocol_verified": protocol_verified,
            "cache_hit": cache_hit,
            "executed_at": _ts(),
        }
        async with self.lock:
            jsonl.append(self.shared / "oracle_log.jsonl", rec)
            with open(self.shared / "oracle_log.md", "a", encoding="utf-8") as f:
                f.write(
                    f"\n### {rec['ts']} — {contestant_id} executed\n"
                    f"input: {_canon(input_payload)}\n"
                    f"result: {_canon(result)}\n"
                    f"protocol verified: {protocol_verified}\n"
                    f"cache hit: {cache_hit}\n"
                )
        return rec

    def _result_matches_input(self, result: dict[str, Any], input_hash: str) -> bool:
        return (
            isinstance(result, dict)
            and result.get("status") == "ok"
            and result.get("protocol_version") == ORACLE_PROTOCOL_VERSION
            and result.get("challenge") == self.challenge
            and isinstance(result.get("request_id"), str)
            and result.get("input_hash") == input_hash
        )

    def _cache_record_is_valid(self, rec: dict[str, Any], input_hash: str) -> bool:
        result = rec.get("result")
        return (
            rec.get("cache_version") == ORACLE_CACHE_VERSION
            and rec.get("phase") == "executed"
            and rec.get("input_hash") == input_hash
            and isinstance(result, dict)
            and self._result_matches_input(result, input_hash)
        )

    def oracle_cache_lookup(self, input_payload: Any) -> dict[str, Any] | None:
        target = oracle_input_hash(self.challenge, input_payload)
        latest: dict[str, Any] | None = None
        for rec in jsonl.iter_records(self.shared / "oracle_log.jsonl"):
            if self._cache_record_is_valid(rec, target):
                latest = rec.get("result")
        return latest

    # ---- write paths (mutations serialized by self.lock) ----

    async def append_finding(self, contestant_id: str, text: str) -> dict[str, Any]:
        rec = {
            "ts": _ts(),
            "contestant_id": contestant_id,
            "text": text,
            "id": _hash({"c": contestant_id, "t": text, "ts": time.time()}),
        }
        async with self.lock:
            jsonl.append(self.shared / "findings.jsonl", rec)
            with open(self.shared / "findings.md", "a", encoding="utf-8") as f:
                f.write(f"\n### {rec['ts']} — {contestant_id}\n{text}\n")
        return rec

    async def append_breakthrough(
        self, contestant_id: str, text: str, promoted_via: str, source_id: str | None = None
    ) -> dict[str, Any]:
        rec = {
            "ts": _ts(),
            "contestant_id": contestant_id,
            "text": text,
            "promoted_via": promoted_via,
            "source_id": source_id,
            "id": _hash({"c": contestant_id, "t": text, "ts": time.time()}),
        }
        async with self.lock:
            jsonl.append(self.shared / "breakthroughs.jsonl", rec)
            with open(self.shared / "breakthroughs.md", "a", encoding="utf-8") as f:
                f.write(
                    f"\n### {rec['ts']} — {contestant_id} (via {promoted_via})\n{text}\n"
                )
        return rec

    async def write_direction(self, contestant_id: str, text: str) -> None:
        async with self.lock:
            (self.contestant_dir(contestant_id) / "direction.md").write_text(text, encoding="utf-8")

    async def append_journal(self, contestant_id: str, text: str) -> None:
        async with self.lock:
            with open(self.contestant_dir(contestant_id) / "journal.md", "a", encoding="utf-8") as f:
                f.write(f"\n## {_ts()}\n{text}\n")

    async def append_turn(self, contestant_id: str, record: dict[str, Any]) -> None:
        async with self.lock:
            jsonl.append(self.contestant_dir(contestant_id) / "turns.jsonl", record)

    async def append_finish_rejection(
        self, contestant_id: str, job_id: str, detail: str
    ) -> dict[str, Any]:
        rec = {
            "ts": _ts(),
            "phase": "rejected",
            "contestant_id": contestant_id,
            "job_id": job_id,
            "detail": detail,
            "id": _hash({"phase": "finish_rejected", "j": job_id, "ts": time.time()}),
        }
        async with self.lock:
            jsonl.append(self.shared / "finish.jsonl", rec)
            with open(self.shared / "finish.md", "a", encoding="utf-8") as f:
                f.write(
                    f"\n### {rec['ts']} — {contestant_id} finish REJECTED by kernel "
                    f"(job {job_id})\n{detail}\n"
                )
        return rec

    async def append_finish_proposal(
        self,
        contestant_id: str,
        text: str,
        job_id: str | None = None,
        hygiene_report: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        rec = {
            "ts": _ts(),
            "phase": "proposed",
            "contestant_id": contestant_id,
            "text": text,
            "job_id": job_id,
            "hygiene_report": hygiene_report,
            "id": _hash({"phase": "finish", "c": contestant_id, "t": text, "ts": time.time()}),
        }
        async with self.lock:
            jsonl.append(self.shared / "finish.jsonl", rec)
            with open(self.shared / "finish.md", "a", encoding="utf-8") as f:
                f.write(f"\n### {rec['ts']} — {contestant_id} proposed finish ({rec['id']})\n{text}\n")
        return rec

    def finish_proposal(self, proposal_id: str) -> dict[str, Any] | None:
        for rec in jsonl.iter_records(self.shared / "finish.jsonl"):
            if rec.get("phase") == "proposed" and rec.get("id") == proposal_id:
                return rec
        return None

    async def append_finish_verification(
        self,
        contestant_id: str,
        proposal_id: str,
        agree: bool,
        reason: str,
    ) -> dict[str, Any]:
        rec = {
            "ts": _ts(),
            "phase": "verified",
            "contestant_id": contestant_id,
            "proposal_id": proposal_id,
            "agree": agree,
            "reason": reason,
            "id": _hash(
                {
                    "phase": "verify",
                    "c": contestant_id,
                    "proposal_id": proposal_id,
                    "agree": agree,
                    "reason": reason,
                    "ts": time.time(),
                }
            ),
        }
        async with self.lock:
            jsonl.append(self.shared / "finish.jsonl", rec)
            verdict = "agreed" if agree else "rejected"
            with open(self.shared / "finish.md", "a", encoding="utf-8") as f:
                f.write(
                    f"\n### {rec['ts']} — {contestant_id} {verdict} proposal {proposal_id}\n{reason}\n"
                )
        return rec

    async def mark_solved(self, record: dict[str, Any]) -> None:
        solved_path = self.state / "SOLVED"
        tmp_path = self.state / ".SOLVED.tmp"
        content = _canon(record)
        async with self.lock:
            tmp_path.write_text(content, encoding="utf-8")
            tmp_path.replace(solved_path)

    # ---- job log ----

    async def append_job_event(self, record: dict[str, Any]) -> dict[str, Any]:
        rec = {"ts": _ts(), **record}
        async with self.lock:
            jsonl.append(self.shared / "jobs.jsonl", rec)
            with open(self.shared / "jobs.md", "a", encoding="utf-8") as f:
                event = rec.get("event", "?")
                jid = rec.get("job_id", "?")
                cid = rec.get("contestant_id", "?")
                if event == "job_finished":
                    result = rec.get("result") or {}
                    f.write(
                        f"\n### {rec['ts']} — {jid} finished ({cid}, {rec.get('mode', '?')})\n"
                        f"outcome: {result.get('outcome')} {result.get('failure_kind') or ''}\n"
                    )
                else:
                    f.write(f"\n### {rec['ts']} — {jid} {event} ({cid})\n")
        return rec

    # ---- DAG log ----

    async def append_dag_event(self, record: dict[str, Any]) -> dict[str, Any]:
        rec = {"ts": _ts(), **record}
        async with self.lock:
            jsonl.append(self.shared / "dag.jsonl", rec)
            with open(self.shared / "dag.md", "a", encoding="utf-8") as f:
                f.write(
                    f"\n### {rec['ts']} — {rec.get('event', '?')}"
                    f" {rec.get('node_id', rec.get('decomp_id', ''))}"
                    f" ({rec.get('actor', '?')})\n"
                )
        return rec

    # ---- proof sources ----

    async def store_proof_source(
        self, node_id: str, source: str, job_id: str
    ) -> dict[str, Any]:
        """Persist the kernel-accepted source for a proved node. Written once
        per (node, proof); re-proving appends a superseding index entry and
        overwrites the .lean file (the index is the record; the file is the
        latest accepted content)."""
        pdir = paths.proofs_dir(self.challenge)
        rec = {
            "ts": _ts(),
            "node_id": node_id,
            "job_id": job_id,
            "source_sha256": sha256_text(source),
            "path": f"proofs/{node_id}.lean",
        }
        async with self.lock:
            (pdir / f"{node_id}.lean").write_text(source, encoding="utf-8")
            jsonl.append(pdir / "index.jsonl", rec)
        return rec

    def proof_source(self, node_id: str) -> str | None:
        f = paths.proofs_dir(self.challenge) / f"{node_id}.lean"
        return f.read_text(encoding="utf-8") if f.exists() else None

    # ---- counters ----

    async def round_counter(self) -> int:
        path = self.state / "round_counter.txt"
        async with self.lock:
            current = int(path.read_text().strip()) if path.exists() else 0
            current += 1
            path.write_text(str(current))
            return current

    def is_solved_flag_present(self) -> bool:
        return (self.state / "SOLVED").exists()
