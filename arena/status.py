"""Read-only status reporter for a running (or stopped) daemon challenge."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from typing import Any

try:
    from .utils import jsonl, paths
except ImportError:  # direct script execution
    from utils import jsonl, paths


def _iso_to_dt(s: str) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _fmt_age(ts: str | None) -> str:
    if not ts:
        return "—"
    dt = _iso_to_dt(ts)
    if not dt:
        return ts
    delta = datetime.now(timezone.utc) - dt
    secs = int(delta.total_seconds())
    if secs < 60:
        return f"{secs}s ago"
    if secs < 3600:
        return f"{secs // 60}m {secs % 60}s ago"
    return f"{secs // 3600}h {(secs % 3600) // 60}m ago"


def collect(challenge: str) -> dict[str, Any]:
    state = paths.state_dir(challenge)
    shared = paths.shared_dir(challenge)

    contestants: dict[str, dict[str, Any]] = {}
    contestants_dir = state / "contestants"
    if contestants_dir.exists():
        for wd in sorted(contestants_dir.iterdir()):
            if not wd.is_dir():
                continue
            wid = wd.name
            turns = jsonl.read_all(wd / "turns.jsonl")
            n_oracle_calls = sum(len(t.get("oracle_calls", []) or []) for t in turns)

            last_turn = turns[-1] if turns else None
            last_ts = last_turn.get("ts") if last_turn else None

            direction_path = wd / "direction.md"
            direction = direction_path.read_text(encoding="utf-8").strip() if direction_path.exists() else ""

            contestants[wid] = {
                "turns_total": len(turns),
                "oracle_calls": n_oracle_calls,
                "last_activity_ts": last_ts,
                "last_activity_age": _fmt_age(last_ts),
                "current_direction": direction[:400],
            }

    breakthroughs = jsonl.read_all(shared / "breakthroughs.jsonl")
    findings = jsonl.read_all(shared / "findings.jsonl")
    finish_records = jsonl.read_all(shared / "finish.jsonl")
    oracle_log = jsonl.read_all(shared / "oracle_log.jsonl")
    executed = [r for r in oracle_log if r.get("phase") == "executed"]
    cache_hits = [r for r in executed if r.get("cache_hit")]

    round_counter_path = state / "round_counter.txt"
    rounds_done = 0
    if round_counter_path.exists():
        try:
            rounds_done = int(round_counter_path.read_text().strip())
        except ValueError:
            pass

    return {
        "challenge": challenge,
        "rounds_done": rounds_done,
        "contestants": contestants,
        "shared": {
            "breakthroughs_count": len(breakthroughs),
            "findings_count": len(findings),
            "finish_records_count": len(finish_records),
            "oracle_calls_total": len(executed),
            "oracle_unique_queries": len({r.get("input_hash") for r in executed if r.get("input_hash")}),
            "oracle_cache_hits": len(cache_hits),
            "latest_breakthrough": breakthroughs[-1] if breakthroughs else None,
            "latest_finish_record": finish_records[-1] if finish_records else None,
        },
        "solved_flag_present": (state / "SOLVED").exists(),
    }


def print_human(snap: dict[str, Any]) -> None:
    print(f"\nchallenge: {snap['challenge']} | rounds: {snap['rounds_done']}")
    print("contestant  turns  oracle  last activity  direction")
    for wid, w in snap["contestants"].items():
        print(
            f"{wid:<10} {w['turns_total']:<6} {w['oracle_calls']:<7} "
            f"{w['last_activity_age']:<14} {w['current_direction'][:80] or '-'}"
        )

    sh = snap["shared"]
    print(
        f"\nshared: breakthroughs={sh['breakthroughs_count']} "
        f"findings={sh['findings_count']} "
        f"finish_records={sh['finish_records_count']} "
        f"oracle_calls={sh['oracle_calls_total']} unique={sh['oracle_unique_queries']} "
        f"cache_hits={sh['oracle_cache_hits']}"
    )

    if sh["latest_breakthrough"]:
        bt = sh["latest_breakthrough"]
        print(
            f"\nlatest breakthrough by {bt.get('contestant_id')} "
            f"via {bt.get('promoted_via','?')} ({_fmt_age(bt.get('ts'))}):"
        )
        print(f"  {bt.get('text','')[:600]}")

    if sh["latest_finish_record"]:
        fr = sh["latest_finish_record"]
        phase = fr.get("phase", "?")
        who = fr.get("contestant_id", "?")
        print(
            f"\nlatest finish record {phase} by {who} "
            f"({_fmt_age(fr.get('ts'))}):"
        )
        print(f"  {(fr.get('text') or fr.get('reason') or '')[:600]}")

    print(f"\nSOLVED flag: {'YES' if snap['solved_flag_present'] else 'no'}\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--challenge", required=True)
    ap.add_argument("--json", action="store_true", help="emit JSON instead of formatted text")
    args = ap.parse_args()

    snap = collect(args.challenge)

    if args.json:
        print(json.dumps(snap, indent=2, default=str))
    else:
        print_human(snap)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
