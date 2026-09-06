"""Read-only per-contestant parity scoreboard for a two-contestant arena.

Counts activity from the daemon's append-only JSONL logs under the state
root (shared/jobs.jsonl, shared/dag.jsonl, shared/findings.jsonl,
shared/breakthroughs.jsonl, contestants/<cid>/turns.jsonl) and never
writes anything.  Counting follows the event-log semantics documented in
dag.py / jobs.py / state_manager.py:

- a job is ONE job_id, not one lifecycle event (job_submitted /
  job_started / job_finished can all appear for the same job);
- cache hits (result.cache_hit) are reported separately and never as
  fresh ok builds; cached failures also live in the cache-hit bucket;
- "nodes proved" credit is last-writer-wins per node_id, mirroring the
  DAG replay semantics (dag.py) -- a superseding re-prove moves the
  credit to the latest prover and a node is never counted twice;
- status_changed events with nonempty proved_modulo are proofs MODULO
  sorry-stubbed children and are reported separately (deflation);
- node_accepted credits the accepting PEER (never the prover);
- findings prefixed "[unpromoted breakthrough candidate" are demoted
  breakthrough candidates and are excluded from the findings headline;
- breakthroughs are deduped on (promotion kind, source_id, text) per
  contestant -- the kind is promoted_via up to the first ':', because
  kernel_verdict tags embed the citing job id and a cache-hit
  resubmission mints a fresh job id for the same claim;
- jobs with no terminal event are counted as "unfinished" (in flight,
  or lost to a daemon restart -- no terminal event is ever appended);
- median job latency (submitted -> finished) is computed only over jobs
  that reached a kernel worker (have a job_started event), so cache-hit
  and hygiene-fail short-circuits do not skew it.  Capability (counts)
  and speed (rates, latency) are reported separately.

A metric whose source file is missing prints as "absent" (null in JSON)
rather than 0.  Exit code is always 0: this is a monitoring decision
aid, not a gate, and it renders no verdicts.

Usage:
    python -m arena.parity --challenge <name> [--state-root DIR]
                           [--since-hours N] [--json]
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

DAEMON_ACTOR = "daemon"
DEMOTED_FINDING_PREFIX = "[unpromoted breakthrough candidate"

PRIMARY_METRIC_ORDER = (
    "turns",
    "findings",
    "breakthroughs",
    "ok_jobs",
    "nodes_proved",
    "nodes_accepted",
    "proved_per_active_hour",
    "ok_jobs_per_active_hour",
)


def default_state_root() -> Path:
    """Resolve the state root exactly like arena/utils/paths.py:8-10."""
    project_root = Path(__file__).resolve().parents[1]
    return (
        Path(os.environ.get("ORACLE_STATE_ROOT", str(project_root / "state")))
        .expanduser()
        .resolve()
    )


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Tolerant reader matching arena/utils/jsonl.py: skip blank/corrupt lines."""
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(record, dict):
                    out.append(record)
    except OSError:
        return []
    return out


def _parse_ts(value: Any) -> datetime | None:
    """Parse an ISO-8601 ts string; naive values are assumed UTC."""
    if not isinstance(value, str):
        return None
    try:
        ts = datetime.fromisoformat(value)
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


def _hour_bucket(ts: datetime) -> datetime:
    return ts.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)


def collect(
    state_root: Path,
    challenge: str,
    since_hours: float | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build the full scoreboard as a JSON-serializable dict."""
    now = now or datetime.now(timezone.utc)
    cutoff = (
        now - timedelta(hours=since_hours) if since_hours is not None else None
    )

    def in_window(ts: datetime | None) -> bool:
        if cutoff is None:
            return True
        return ts is not None and ts >= cutoff

    challenge_dir = state_root / challenge
    shared = challenge_dir / "shared"
    contestants_dir = challenge_dir / "contestants"

    jobs_path = shared / "jobs.jsonl"
    dag_path = shared / "dag.jsonl"
    findings_path = shared / "findings.jsonl"
    breakthroughs_path = shared / "breakthroughs.jsonl"

    jobs_exists = jobs_path.exists()
    dag_exists = dag_path.exists()
    findings_exists = findings_path.exists()
    breakthroughs_exists = breakthroughs_path.exists()

    job_events = _read_jsonl(jobs_path)
    dag_events = _read_jsonl(dag_path)
    finding_records = _read_jsonl(findings_path)
    breakthrough_records = _read_jsonl(breakthroughs_path)

    # ---- contestant discovery: directories plus every attributed record ----
    cids: set[str] = set()
    if contestants_dir.is_dir():
        cids |= {p.name for p in contestants_dir.iterdir() if p.is_dir()}
    for rec in job_events:
        cid = rec.get("contestant_id")
        if isinstance(cid, str) and cid and cid != DAEMON_ACTOR:
            cids.add(cid)
    for rec in dag_events:
        for key in ("actor", "contestant_id"):
            cid = rec.get(key)
            if isinstance(cid, str) and cid and cid != DAEMON_ACTOR:
                cids.add(cid)
    for rec in finding_records + breakthrough_records:
        cid = rec.get("contestant_id")
        if isinstance(cid, str) and cid and cid != DAEMON_ACTOR:
            cids.add(cid)
    contestant_ids = sorted(cids)

    # ---- jobs: fold lifecycle events into one record per job_id ----
    jobs: dict[str, dict[str, Any]] = {}
    for rec in job_events:
        job_id = rec.get("job_id")
        event = rec.get("event")
        if not isinstance(job_id, str) or not job_id:
            continue
        job = jobs.setdefault(
            job_id,
            {
                "contestant_id": None,
                "mode": None,
                "submitted_ts": None,
                "started": False,
                "started_ts": None,
                "finished_ts": None,
                "result": None,
            },
        )
        cid = rec.get("contestant_id")
        if isinstance(cid, str) and cid:
            job["contestant_id"] = job["contestant_id"] or cid
        if rec.get("mode"):
            job["mode"] = job["mode"] or rec.get("mode")
        ts = _parse_ts(rec.get("ts"))
        if event == "job_submitted":
            job["submitted_ts"] = ts
        elif event == "job_started":
            job["started"] = True
            job["started_ts"] = ts
        elif event == "job_finished":
            job["finished_ts"] = ts
            result = rec.get("result")
            job["result"] = result if isinstance(result, dict) else {}

    # ---- DAG: window first, then last-writer-wins per node ----
    latest_proof: dict[str, dict[str, Any]] = {}
    latest_accept: dict[str, str] = {}
    for rec in dag_events:
        ts = _parse_ts(rec.get("ts"))
        if not in_window(ts):
            continue
        event = rec.get("event")
        node_id = rec.get("node_id")
        if not isinstance(node_id, str) or not node_id:
            continue
        if event == "status_changed" and rec.get("to") == "proved":
            latest_proof[node_id] = rec
        elif event == "node_accepted":
            actor = rec.get("actor")
            if isinstance(actor, str) and actor and actor != DAEMON_ACTOR:
                latest_accept[node_id] = actor

    # ---- per-contestant activity timestamps ----
    activity_all: dict[str, list[datetime]] = {cid: [] for cid in contestant_ids}

    def _record_activity(cid: Any, ts: datetime | None) -> None:
        if ts is not None and isinstance(cid, str) and cid in activity_all:
            activity_all[cid].append(ts)

    for job in jobs.values():
        for key in ("submitted_ts", "started_ts", "finished_ts"):
            _record_activity(job["contestant_id"], job[key])
    for rec in dag_events:
        actor = rec.get("actor")
        if actor == DAEMON_ACTOR:
            actor = rec.get("contestant_id")
        _record_activity(actor, _parse_ts(rec.get("ts")))
    for rec in finding_records + breakthrough_records:
        _record_activity(rec.get("contestant_id"), _parse_ts(rec.get("ts")))

    # ---- assemble per-contestant metrics ----
    contestants: dict[str, dict[str, Any]] = {}
    for cid in contestant_ids:
        turns_path = contestants_dir / cid / "turns.jsonl"
        turns_exists = turns_path.exists()
        turn_records = _read_jsonl(turns_path)
        for rec in turn_records:
            _record_activity(cid, _parse_ts(rec.get("ts")))

        turns: int | None = None
        if turns_exists:
            turns = sum(
                1
                for rec in turn_records
                if in_window(_parse_ts(rec.get("ts")))
            )

        findings: int | None = None
        findings_demoted: int | None = None
        if findings_exists:
            findings = 0
            findings_demoted = 0
            for rec in finding_records:
                if rec.get("contestant_id") != cid:
                    continue
                if not in_window(_parse_ts(rec.get("ts"))):
                    continue
                text = rec.get("text") or ""
                if isinstance(text, str) and text.startswith(
                    DEMOTED_FINDING_PREFIX
                ):
                    findings_demoted += 1
                else:
                    findings += 1

        breakthroughs: int | None = None
        if breakthroughs_exists:
            seen: set[tuple[Any, Any, Any]] = set()
            for rec in breakthrough_records:
                if rec.get("contestant_id") != cid:
                    continue
                if not in_window(_parse_ts(rec.get("ts"))):
                    continue
                # Dedupe on the promotion KIND, not the full tag: kernel
                # promotions are tagged "kernel_verdict:<job_id>", and a
                # cache-hit resubmission mints a fresh job id for the same
                # claim -- the full tag would count that claim twice.
                via = rec.get("promoted_via")
                via_kind = via.split(":", 1)[0] if isinstance(via, str) else via
                seen.add((via_kind, rec.get("source_id"), rec.get("text")))
            breakthroughs = len(seen)

        job_stats: dict[str, Any] | None = None
        median_latency: float | None = None
        if jobs_exists:
            submitted = 0
            ok = 0
            cache_hits = 0
            unfinished = 0
            failed: Counter[str] = Counter()
            latencies: list[float] = []
            for job in jobs.values():
                if job["contestant_id"] != cid:
                    continue
                window_ts = job["finished_ts"] or job["submitted_ts"]
                if not in_window(window_ts):
                    continue
                submitted += 1
                result = job["result"]
                if result is None:
                    unfinished += 1
                    continue
                if result.get("cache_hit"):
                    cache_hits += 1
                elif result.get("outcome") == "ok":
                    ok += 1
                elif result.get("outcome") == "failed":
                    failed[str(result.get("failure_kind") or "unknown")] += 1
                if (
                    job["started"]
                    and job["submitted_ts"] is not None
                    and job["finished_ts"] is not None
                    and not result.get("cache_hit")
                ):
                    delta = (
                        job["finished_ts"] - job["submitted_ts"]
                    ).total_seconds()
                    if delta >= 0:
                        latencies.append(delta)
            job_stats = {
                "submitted": submitted,
                "ok": ok,
                "cache_hits": cache_hits,
                "unfinished": unfinished,
                "failed": dict(sorted(failed.items())),
                "failed_total": sum(failed.values()),
            }
            if latencies:
                median_latency = float(statistics.median(latencies))

        dag_stats: dict[str, Any] | None = None
        if dag_exists:
            proved = 0
            proved_modulo = 0
            for rec in latest_proof.values():
                prover = rec.get("contestant_id") or rec.get("actor")
                if prover != cid:
                    continue
                if rec.get("proved_modulo"):
                    proved_modulo += 1
                else:
                    proved += 1
            accepted = sum(
                1 for actor in latest_accept.values() if actor == cid
            )
            dag_stats = {
                "proved": proved,
                "proved_modulo": proved_modulo,
                "accepted": accepted,
            }

        any_source = (
            turns_exists
            or jobs_exists
            or dag_exists
            or findings_exists
            or breakthroughs_exists
        )
        all_ts = activity_all.get(cid, [])
        last_ts = max(all_ts) if all_ts else None
        last_age_hours = (
            (now - last_ts).total_seconds() / 3600.0 if last_ts else None
        )
        active_hours: int | None = None
        if any_source:
            active_hours = len(
                {_hour_bucket(ts) for ts in all_ts if in_window(ts)}
            )

        def _rate(count: int | None) -> float | None:
            if count is None or not active_hours:
                return None
            return count / active_hours

        contestants[cid] = {
            "turns": turns,
            "last_activity_ts": last_ts.isoformat() if last_ts else None,
            "last_activity_age_hours": last_age_hours,
            "findings": findings,
            "findings_demoted": findings_demoted,
            "breakthroughs": breakthroughs,
            "jobs": job_stats,
            "dag": dag_stats,
            "active_hours": active_hours,
            "rates": {
                "proved_per_active_hour": _rate(
                    dag_stats["proved"] if dag_stats else None
                ),
                "ok_jobs_per_active_hour": _rate(
                    job_stats["ok"] if job_stats else None
                ),
            },
            "median_job_latency_seconds": median_latency,
        }

    # ---- head-to-head ----
    head_to_head: dict[str, Any] | None = None
    notes: list[str] = [
        "a job = one job_id; ok excludes cache hits; cached failures sit in the cache-hit bucket",
        "proved credit is last-writer-wins per node (a superseding re-prove moves the credit)",
        "unfinished jobs include daemon-restart casualties (no terminal event is ever logged for them)",
    ]
    if cutoff is not None:
        notes.append(
            f"window: events in the last {since_hours:g}h by daemon append ts; "
            "records without a parseable ts are excluded; last activity ignores the window"
        )
    if len(contestant_ids) == 2:
        a_id, b_id = contestant_ids
        a_vals = _primary_values(contestants[a_id])
        b_vals = _primary_values(contestants[b_id])
        metrics: dict[str, Any] = {}
        a_leads = b_leads = even = comparable = 0
        for name in PRIMARY_METRIC_ORDER:
            a_val, b_val = a_vals[name], b_vals[name]
            entry: dict[str, Any] = {"a": a_val, "b": b_val, "ratio": None}
            if a_val is not None and b_val is not None:
                comparable += 1
                if b_val:
                    entry["ratio"] = a_val / b_val
                elif not a_val:
                    entry["note"] = "both zero"
                else:
                    entry["note"] = "zero denominator"
                if a_val > b_val:
                    a_leads += 1
                elif b_val > a_val:
                    b_leads += 1
                else:
                    even += 1
            else:
                entry["note"] = "absent"
            metrics[name] = entry
        if comparable:
            summary = (
                f"{a_id} leads {a_leads}/{comparable} comparable primary metrics, "
                f"{b_id} leads {b_leads}, {even} even. "
                "Decision aid only -- no verdict."
            )
        else:
            summary = "no comparable primary metrics yet."
        head_to_head = {
            "pair": [a_id, b_id],
            "metrics": metrics,
            "summary": summary,
        }
    elif contestant_ids:
        notes.append(
            f"head-to-head skipped: expected 2 contestants, found {len(contestant_ids)}"
        )

    return {
        "challenge": challenge,
        "state_root": str(state_root),
        "generated_at": now.isoformat(),
        "window_hours": since_hours,
        "sources": {
            "jobs": jobs_exists,
            "dag": dag_exists,
            "findings": findings_exists,
            "breakthroughs": breakthroughs_exists,
        },
        "contestants": contestants,
        "head_to_head": head_to_head,
        "notes": notes,
    }


def _primary_values(c: dict[str, Any]) -> dict[str, Any]:
    jobs = c.get("jobs")
    dag = c.get("dag")
    rates = c.get("rates") or {}
    return {
        "turns": c.get("turns"),
        "findings": c.get("findings"),
        "breakthroughs": c.get("breakthroughs"),
        "ok_jobs": jobs.get("ok") if jobs is not None else None,
        "nodes_proved": dag.get("proved") if dag is not None else None,
        "nodes_accepted": dag.get("accepted") if dag is not None else None,
        "proved_per_active_hour": rates.get("proved_per_active_hour"),
        "ok_jobs_per_active_hour": rates.get("ok_jobs_per_active_hour"),
    }


def _fmt(value: Any) -> str:
    if value is None:
        return "absent"
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def render_text(data: dict[str, Any]) -> str:
    lines: list[str] = []
    window = data.get("window_hours")
    lines.append(f"parity scoreboard -- challenge '{data['challenge']}'")
    lines.append(f"state root: {data['state_root']}")
    lines.append(
        "window: " + (f"last {window:g}h" if window is not None else "all time")
    )
    lines.append(f"generated: {data['generated_at']}")
    lines.append("")

    contestants = data.get("contestants") or {}
    if not contestants:
        lines.append("contestants: none discovered (state absent or empty)")
        lines.append("all metrics: absent")
        return "\n".join(lines)

    cids = sorted(contestants)

    failed_kinds = sorted(
        {
            kind
            for c in contestants.values()
            if c.get("jobs")
            for kind in c["jobs"]["failed"]
        }
    )

    def _jobs_metric(c: dict[str, Any], key: str) -> Any:
        return c["jobs"][key] if c.get("jobs") is not None else None

    def _dag_metric(c: dict[str, Any], key: str) -> Any:
        return c["dag"][key] if c.get("dag") is not None else None

    def _age(c: dict[str, Any]) -> str:
        age = c.get("last_activity_age_hours")
        return f"{age:.1f}h ago" if age is not None else "absent"

    def _latency(c: dict[str, Any]) -> str:
        v = c.get("median_job_latency_seconds")
        return f"{v:.1f}s" if v is not None else "absent"

    rows: list[tuple[str, list[str]]] = []

    def add(label: str, getter: Any) -> None:
        rows.append((label, [getter(contestants[cid]) for cid in cids]))

    rows.append(("-- activity --", ["" for _ in cids]))
    add("turns", lambda c: _fmt(c.get("turns")))
    add("last activity", _age)
    add("findings", lambda c: _fmt(c.get("findings")))
    add("findings (demoted candidates)", lambda c: _fmt(c.get("findings_demoted")))
    add("breakthroughs (deduped)", lambda c: _fmt(c.get("breakthroughs")))
    rows.append(("-- capability --", ["" for _ in cids]))
    add("jobs submitted", lambda c: _fmt(_jobs_metric(c, "submitted")))
    add("jobs ok (fresh)", lambda c: _fmt(_jobs_metric(c, "ok")))
    add("jobs cache-hit", lambda c: _fmt(_jobs_metric(c, "cache_hits")))
    add("jobs unfinished", lambda c: _fmt(_jobs_metric(c, "unfinished")))
    for kind in failed_kinds:
        add(
            f"jobs failed[{kind}]",
            lambda c, kind=kind: _fmt(
                c["jobs"]["failed"].get(kind, 0)
                if c.get("jobs") is not None
                else None
            ),
        )
    add("nodes proved (full)", lambda c: _fmt(_dag_metric(c, "proved")))
    add(
        "nodes proved (modulo sorries)",
        lambda c: _fmt(_dag_metric(c, "proved_modulo")),
    )
    add("nodes accepted (as peer)", lambda c: _fmt(_dag_metric(c, "accepted")))
    rows.append(("-- speed --", ["" for _ in cids]))
    add("active hours (est)", lambda c: _fmt(c.get("active_hours")))
    add(
        "proved per active hour",
        lambda c: _fmt((c.get("rates") or {}).get("proved_per_active_hour")),
    )
    add(
        "ok jobs per active hour",
        lambda c: _fmt((c.get("rates") or {}).get("ok_jobs_per_active_hour")),
    )
    add("median job latency", _latency)

    label_width = max(len("metric"), max(len(label) for label, _ in rows))
    col_widths = [
        max(len(cid), max(len(vals[i]) for _, vals in rows))
        for i, cid in enumerate(cids)
    ]
    header = "metric".ljust(label_width) + "  " + "  ".join(
        cid.rjust(col_widths[i]) for i, cid in enumerate(cids)
    )
    lines.append(header)
    lines.append("-" * len(header))
    for label, vals in rows:
        lines.append(
            label.ljust(label_width)
            + "  "
            + "  ".join(vals[i].rjust(col_widths[i]) for i in range(len(cids)))
        )

    h2h = data.get("head_to_head")
    lines.append("")
    if h2h:
        a_id, b_id = h2h["pair"]
        lines.append(f"head-to-head ({a_id} / {b_id})")
        for name in PRIMARY_METRIC_ORDER:
            entry = h2h["metrics"].get(name, {})
            ratio = entry.get("ratio")
            if ratio is not None:
                tail = f"ratio {ratio:.2f}"
            else:
                tail = f"ratio n/a ({entry.get('note', 'absent')})"
            lines.append(
                f"  {name.ljust(24)} {_fmt(entry.get('a'))} vs "
                f"{_fmt(entry.get('b'))} -- {tail}"
            )
        lines.append(f"  summary: {h2h['summary']}")
    else:
        lines.append("head-to-head: absent (need exactly 2 contestants)")

    lines.append("")
    lines.append("notes:")
    for note in data.get("notes", []):
        lines.append(f"  - {note}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="parity",
        description="Read-only per-contestant parity scoreboard (exit 0 always).",
    )
    parser.add_argument(
        "--state-root",
        default=None,
        help="state root (default: $ORACLE_STATE_ROOT or <repo>/state, "
        "resolved like arena/utils/paths.py)",
    )
    parser.add_argument("--challenge", required=True, help="challenge name")
    parser.add_argument(
        "--json", action="store_true", help="emit full JSON instead of the table"
    )
    parser.add_argument(
        "--since-hours",
        type=float,
        default=None,
        metavar="N",
        help="only count events from the last N hours",
    )
    args = parser.parse_args(argv)

    try:
        state_root = (
            Path(args.state_root).expanduser().resolve()
            if args.state_root
            else default_state_root()
        )
        data = collect(state_root, args.challenge, args.since_hours)
        if args.json:
            print(json.dumps(data, indent=2, default=str))
        else:
            print(render_text(data))
    except Exception as exc:  # monitoring tool: never fail the caller
        print(f"parity: error: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
