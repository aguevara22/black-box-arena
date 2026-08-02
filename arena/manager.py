#!/usr/bin/env python3
"""Read-only operational dashboard for daemon-backed contestant sessions.

This process observes existing state files and GET /health only. It never calls
the oracle, never posts to the daemon, and never writes challenge state.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aiohttp import web

try:
    from .config import load_config
    from .utils import paths
except ImportError:  # direct script execution
    from config import load_config
    from utils import paths


DEFAULT_DAEMON_URL = "http://127.0.0.1:8787"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _age_seconds(dt: datetime | None, now: datetime | None = None) -> float | None:
    if dt is None:
        return None
    return max(0.0, ((now or _now()) - dt).total_seconds())


def _read_text(path: Path, default: str = "") -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return default


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
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
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(rec, dict):
                    out.append(rec)
    except OSError:
        return []
    return out


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _tail_lines(path: Path, max_lines: int = 400) -> list[str]:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return []
    return [line.rstrip("\n") for line in lines[-max_lines:]]


def _root_path(root: Path | str | None = None) -> Path:
    return Path(root) if root is not None else paths.PROJECT_ROOT


def _state_root(root: Path) -> Path:
    if root.resolve() == paths.PROJECT_ROOT.resolve():
        return paths.STATE_ROOT
    return root / "state"


def _challenge_state(root: Path, challenge: str) -> Path:
    return _state_root(root) / challenge


def _shared(root: Path, challenge: str) -> Path:
    return _challenge_state(root, challenge) / "shared"


def _contestants_dir(root: Path, challenge: str) -> Path:
    return _challenge_state(root, challenge) / "contestants"


def _config_path(root: Path, challenge: str) -> Path:
    return root / "challenges" / challenge / "config.yaml"


def _safe_load_config(root: Path, challenge: str) -> Any | None:
    cfg_path = _config_path(root, challenge)
    if not cfg_path.exists():
        return None
    try:
        return load_config(cfg_path)
    except Exception:
        return None


def _configured_contestants(root: Path, challenge: str) -> list[str]:
    cfg = _safe_load_config(root, challenge)
    if cfg is not None and cfg.contestants:
        return [c.id for c in cfg.contestants]
    cdir = _contestants_dir(root, challenge)
    if not cdir.exists():
        return []
    return sorted(p.name for p in cdir.iterdir() if p.is_dir())


def _advisor_to_contestant(root: Path, challenge: str) -> dict[str, str]:
    cfg = _safe_load_config(root, challenge)
    if cfg is None:
        return {}
    out: dict[str, str] = {}
    for contestant in cfg.contestants:
        if contestant.advisor:
            out[contestant.advisor.lower()] = contestant.id
    return out


def _advisor_from_model(model: str) -> str:
    m = (model or "").lower()
    if "gemini" in m:
        return "gemini"
    if "gpt" in m or "openai" in m:
        return "openai"
    return "unknown"


def _fetch_health(daemon_url: str, timeout: float = 1.5) -> dict[str, Any]:
    url = daemon_url.rstrip("/") + "/health"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        return {"ok": False, "error": f"connection failed: {exc.reason}"}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return data if isinstance(data, dict) else {"ok": False, "error": "invalid health response"}


LOG_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})\s+"
    r"\[(?P<level>[A-Z]+)\]\s+(?P<logger>[^:]+):\s*(?P<message>.*)$"
)


def collect_logs(
    challenge: str,
    *,
    root: Path | str | None = None,
    max_entries: int = 120,
) -> dict[str, Any]:
    project = _root_path(root)
    candidates = [
        _challenge_state(project, challenge) / "daemon.log",
        _state_root(project) / "daemon.launchd.err.log",
        _state_root(project) / "daemon.launchd.out.log",
    ]
    entries: list[dict[str, Any]] = []
    seen: set[tuple[str | None, str, str, str]] = set()
    for path in candidates:
        for line in _tail_lines(path, 500):
            match = LOG_RE.match(line)
            if not match:
                continue
            level = match.group("level").lower()
            if level not in {"warning", "error", "critical"}:
                continue
            dt = _parse_dt(match.group("ts"))
            message = match.group("message")
            logger = match.group("logger")
            key = (_iso(dt), level, logger, message)
            if key in seen:
                continue
            seen.add(key)
            entries.append(
                {
                    "ts": _iso(dt),
                    "level": level,
                    "logger": logger,
                    "message": message,
                    "source": str(path.relative_to(project)) if path.is_relative_to(project) else str(path),
                }
            )
    entries.sort(key=lambda e: e.get("ts") or "")
    counts = {
        "warning": sum(1 for entry in entries if entry.get("level") == "warning"),
        "error": sum(1 for entry in entries if entry.get("level") in {"error", "critical"}),
    }
    return {"counts": counts, "entries": entries[-max_entries:]}


def _bucket_start(dt: datetime, bucket_seconds: int) -> datetime:
    stamp = int(dt.timestamp())
    return datetime.fromtimestamp(stamp - (stamp % bucket_seconds), tz=timezone.utc)


def _increment_bucket(
    buckets: dict[str, dict[str, float]],
    dt: datetime | None,
    field: str,
    amount: float = 1.0,
    *,
    bucket_seconds: int,
) -> None:
    if dt is None:
        return
    key = _iso(_bucket_start(dt, bucket_seconds))
    if key is None:
        return
    buckets[key][field] = buckets[key].get(field, 0.0) + amount


def _series_from_buckets(buckets: dict[str, dict[str, float]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for ts in sorted(buckets):
        row: dict[str, Any] = {"ts": ts}
        for key, value in sorted(buckets[ts].items()):
            row[key] = int(value) if float(value).is_integer() else value
        rows.append(row)
    return rows


def _oracle_result_status(rec: dict[str, Any]) -> str:
    result = rec.get("result")
    if not isinstance(result, dict):
        return "missing"
    outer = result.get("status")
    if outer != "ok":
        return str(outer or "error")
    output = result.get("output")
    if isinstance(output, dict):
        return str(output.get("status") or "ok")
    return "ok"


def _contestant_id(rec: dict[str, Any]) -> str:
    cid = rec.get("contestant_id")
    return cid if isinstance(cid, str) and cid else "unknown"


def collect_series(
    challenge: str,
    *,
    root: Path | str | None = None,
    bucket_minutes: int = 5,
) -> dict[str, Any]:
    project = _root_path(root)
    bucket_seconds = max(60, int(bucket_minutes) * 60)
    buckets: dict[str, dict[str, float]] = defaultdict(dict)
    turn_times: dict[str, list[datetime]] = defaultdict(list)

    for contestant in _configured_contestants(project, challenge):
        turns = _read_jsonl(_contestants_dir(project, challenge) / contestant / "turns.jsonl")
        for turn in turns:
            dt = _parse_dt(turn.get("ts"))
            if dt is not None:
                turn_times[contestant].append(dt)
            _increment_bucket(buckets, dt, f"turns_{contestant}", bucket_seconds=bucket_seconds)
            _increment_bucket(buckets, dt, "turns_total", bucket_seconds=bucket_seconds)

    for name, field in (
        ("findings.jsonl", "findings"),
        ("breakthroughs.jsonl", "breakthroughs"),
    ):
        for rec in _read_jsonl(_shared(project, challenge) / name):
            dt = _parse_dt(rec.get("ts"))
            contestant = _contestant_id(rec)
            _increment_bucket(buckets, dt, field, bucket_seconds=bucket_seconds)
            _increment_bucket(buckets, dt, f"{field}_{contestant}", bucket_seconds=bucket_seconds)

    for rec in _read_jsonl(_shared(project, challenge) / "finish.jsonl"):
        phase = rec.get("phase")
        agree = rec.get("agree")
        dt = _parse_dt(rec.get("ts"))
        contestant = _contestant_id(rec)
        _increment_bucket(buckets, dt, "finish_records", bucket_seconds=bucket_seconds)
        _increment_bucket(buckets, dt, f"finish_records_{contestant}", bucket_seconds=bucket_seconds)
        if phase == "proposed":
            _increment_bucket(buckets, dt, "finish_proposed", bucket_seconds=bucket_seconds)
            _increment_bucket(buckets, dt, f"finish_proposed_{contestant}", bucket_seconds=bucket_seconds)
        elif phase == "verified" and agree is True:
            _increment_bucket(buckets, dt, "finish_agreed", bucket_seconds=bucket_seconds)
            _increment_bucket(buckets, dt, f"finish_agreed_{contestant}", bucket_seconds=bucket_seconds)
        elif phase == "verified" and agree is False:
            _increment_bucket(buckets, dt, "finish_rejected", bucket_seconds=bucket_seconds)
            _increment_bucket(buckets, dt, f"finish_rejected_{contestant}", bucket_seconds=bucket_seconds)

    for rec in _read_jsonl(_shared(project, challenge) / "oracle_log.jsonl"):
        dt = _parse_dt(rec.get("ts") or rec.get("executed_at") or rec.get("predicted_at"))
        phase = rec.get("phase")
        contestant = _contestant_id(rec)
        if phase == "predicted":
            _increment_bucket(buckets, dt, "oracle_predictions", bucket_seconds=bucket_seconds)
            _increment_bucket(buckets, dt, f"oracle_predictions_{contestant}", bucket_seconds=bucket_seconds)
            continue
        if phase != "executed":
            continue
        _increment_bucket(buckets, dt, "oracle_calls", bucket_seconds=bucket_seconds)
        _increment_bucket(buckets, dt, f"oracle_calls_{contestant}", bucket_seconds=bucket_seconds)
        if rec.get("cache_hit"):
            _increment_bucket(buckets, dt, "oracle_cache_hits", bucket_seconds=bucket_seconds)
            _increment_bucket(buckets, dt, f"oracle_cache_hits_{contestant}", bucket_seconds=bucket_seconds)
        pred = rec.get("prediction_correct")
        if pred is True:
            _increment_bucket(buckets, dt, "oracle_predictions_correct", bucket_seconds=bucket_seconds)
            _increment_bucket(
                buckets, dt, f"oracle_predictions_correct_{contestant}", bucket_seconds=bucket_seconds
            )
        elif pred is False:
            _increment_bucket(buckets, dt, "oracle_predictions_incorrect", bucket_seconds=bucket_seconds)
            _increment_bucket(
                buckets, dt, f"oracle_predictions_incorrect_{contestant}", bucket_seconds=bucket_seconds
            )
        status = _oracle_result_status(rec)
        if status not in {"ok", "wall", "malformed"}:
            _increment_bucket(buckets, dt, "oracle_errors", bucket_seconds=bucket_seconds)
            _increment_bucket(buckets, dt, f"oracle_errors_{contestant}", bucket_seconds=bucket_seconds)
        elif status in {"wall", "malformed"}:
            _increment_bucket(buckets, dt, f"oracle_{status}", bucket_seconds=bucket_seconds)
            _increment_bucket(buckets, dt, f"oracle_{status}_{contestant}", bucket_seconds=bucket_seconds)

    for entry in collect_logs(challenge, root=project, max_entries=1000)["entries"]:
        level = entry.get("level")
        dt = _parse_dt(entry.get("ts"))
        if level == "warning":
            _increment_bucket(buckets, dt, "log_warnings", bucket_seconds=bucket_seconds)
        elif level in {"error", "critical"}:
            _increment_bucket(buckets, dt, "log_errors", bucket_seconds=bucket_seconds)

    bucket_keys = sorted(buckets)
    for contestant, times in turn_times.items():
        ordered = sorted(times)
        pos = 0
        last: datetime | None = None
        for key in bucket_keys:
            bucket_dt = _parse_dt(key)
            if bucket_dt is None:
                continue
            while pos < len(ordered) and ordered[pos] <= bucket_dt:
                last = ordered[pos]
                pos += 1
            if last is not None:
                buckets[key][f"idle_minutes_{contestant}"] = max(0.0, (bucket_dt - last).total_seconds() / 60.0)

    return {"bucket_minutes": bucket_seconds // 60, "series": _series_from_buckets(buckets)}


def _last_dt(records: list[dict[str, Any]]) -> datetime | None:
    found: list[datetime] = []
    for rec in records:
        dt = _parse_dt(rec.get("ts"))
        if dt is not None:
            found.append(dt)
    return max(found) if found else None


def collect_summary(
    challenge: str,
    *,
    root: Path | str | None = None,
    daemon_url: str = DEFAULT_DAEMON_URL,
) -> dict[str, Any]:
    project = _root_path(root)
    now = _now()
    health = _fetch_health(daemon_url)
    contestants: dict[str, Any] = {}
    for contestant in _configured_contestants(project, challenge):
        cpath = _contestants_dir(project, challenge) / contestant
        turns = _read_jsonl(cpath / "turns.jsonl")
        last = turns[-1] if turns else None
        last_dt = _parse_dt(last.get("ts")) if isinstance(last, dict) else None
        oracle_calls_from_turns = sum(
            len(t.get("oracle_calls") or []) for t in turns if isinstance(t.get("oracle_calls"), list)
        )
        contestants[contestant] = {
            "turns_total": len(turns),
            "oracle_calls_reported_in_turns": oracle_calls_from_turns,
            "last_activity_ts": _iso(last_dt),
            "idle_seconds": _age_seconds(last_dt, now),
            "direction": _read_text(cpath / "direction.md").strip(),
            "journal_bytes": len(_read_text(cpath / "journal.md")),
        }

    def ensure_contestant(contestant: str) -> dict[str, Any]:
        if contestant not in contestants:
            contestants[contestant] = {
                "turns_total": 0,
                "oracle_calls_reported_in_turns": 0,
                "last_activity_ts": None,
                "idle_seconds": None,
                "direction": "",
                "journal_bytes": 0,
            }
        metrics = contestants[contestant]
        for key, default in {
            "oracle_calls_total": 0,
            "oracle_predictions_total": 0,
            "oracle_cache_hits": 0,
            "oracle_errors": 0,
            "oracle_status_counts": {},
            "prediction_checked": 0,
            "prediction_correct": 0,
            "prediction_incorrect": 0,
            "prediction_accuracy": None,
            "findings_count": 0,
            "breakthroughs_count": 0,
            "finish_proposals_count": 0,
            "finish_rejections_count": 0,
            "finish_agreements_count": 0,
            "finish_verifications_count": 0,
        }.items():
            metrics.setdefault(key, default.copy() if isinstance(default, dict) else default)
        return metrics

    for contestant in list(contestants):
        ensure_contestant(contestant)

    shared = _shared(project, challenge)
    oracle_log = _read_jsonl(shared / "oracle_log.jsonl")
    executed = [r for r in oracle_log if r.get("phase") == "executed"]
    predictions = [r for r in oracle_log if r.get("phase") == "predicted"]
    cache_hits = [r for r in executed if r.get("cache_hit")]
    pred_checked = [r for r in executed if r.get("prediction_correct") is not None]
    pred_correct = [r for r in pred_checked if r.get("prediction_correct") is True]
    status_counts: dict[str, int] = defaultdict(int)
    by_contestant: dict[str, int] = defaultdict(int)
    predictions_by_contestant: dict[str, int] = defaultdict(int)
    for rec in executed:
        status = _oracle_result_status(rec)
        status_counts[status] += 1
        contestant = _contestant_id(rec)
        by_contestant[contestant] += 1
        metrics = ensure_contestant(contestant)
        metrics["oracle_calls_total"] += 1
        if rec.get("cache_hit"):
            metrics["oracle_cache_hits"] += 1
        if status not in {"ok", "wall", "malformed"}:
            metrics["oracle_errors"] += 1
        metrics["oracle_status_counts"][status] = metrics["oracle_status_counts"].get(status, 0) + 1
        pred = rec.get("prediction_correct")
        if pred is not None:
            metrics["prediction_checked"] += 1
            if pred is True:
                metrics["prediction_correct"] += 1
            else:
                metrics["prediction_incorrect"] += 1

    for rec in predictions:
        contestant = _contestant_id(rec)
        predictions_by_contestant[contestant] += 1
        ensure_contestant(contestant)["oracle_predictions_total"] += 1

    findings = _read_jsonl(shared / "findings.jsonl")
    breakthroughs = _read_jsonl(shared / "breakthroughs.jsonl")
    finishes = _read_jsonl(shared / "finish.jsonl")
    for rec in findings:
        ensure_contestant(_contestant_id(rec))["findings_count"] += 1
    for rec in breakthroughs:
        ensure_contestant(_contestant_id(rec))["breakthroughs_count"] += 1
    for rec in finishes:
        metrics = ensure_contestant(_contestant_id(rec))
        phase = rec.get("phase")
        agree = rec.get("agree")
        if phase == "proposed":
            metrics["finish_proposals_count"] += 1
        elif phase == "verified":
            metrics["finish_verifications_count"] += 1
            if agree is True:
                metrics["finish_agreements_count"] += 1
            elif agree is False:
                metrics["finish_rejections_count"] += 1
    for metrics in contestants.values():
        checked = metrics.get("prediction_checked") or 0
        if checked:
            metrics["prediction_accuracy"] = metrics.get("prediction_correct", 0) / checked

    proposed = [r for r in finishes if r.get("phase") == "proposed"]
    rejected = [r for r in finishes if r.get("phase") == "verified" and r.get("agree") is False]
    agreed = [r for r in finishes if r.get("phase") == "verified" and r.get("agree") is True]
    logs = collect_logs(challenge, root=project)
    round_path = _challenge_state(project, challenge) / "round_counter.txt"
    try:
        rounds_done = int(round_path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        rounds_done = None

    return {
        "challenge": challenge,
        "generated_at": _iso(now),
        "daemon": {
            "url": daemon_url,
            "alive": bool(health.get("ok")),
            "health": health,
        },
        "solved_flag_present": (_challenge_state(project, challenge) / "SOLVED").exists(),
        "rounds_done": rounds_done,
        "contestants": contestants,
        "shared": {
            "oracle_calls_total": len(executed),
            "oracle_predictions_total": len(predictions),
            "oracle_unique_queries": len({r.get("input_hash") for r in executed if r.get("input_hash")}),
            "oracle_cache_hits": len(cache_hits),
            "oracle_status_counts": dict(sorted(status_counts.items())),
            "oracle_calls_by_contestant": dict(sorted(by_contestant.items())),
            "oracle_predictions_by_contestant": dict(sorted(predictions_by_contestant.items())),
            "prediction_checked": len(pred_checked),
            "prediction_correct": len(pred_correct),
            "prediction_accuracy": (len(pred_correct) / len(pred_checked)) if pred_checked else None,
            "findings_count": len(findings),
            "breakthroughs_count": len(breakthroughs),
            "finish_proposals_count": len(proposed),
            "finish_rejections_count": len(rejected),
            "finish_agreements_count": len(agreed),
            "latest_finding_ts": _iso(_last_dt(findings)),
            "latest_breakthrough_ts": _iso(_last_dt(breakthroughs)),
            "latest_finish_ts": _iso(_last_dt(finishes)),
        },
        "logs": logs["counts"],
    }


def collect_spend(
    challenge: str,
    *,
    root: Path | str | None = None,
) -> dict[str, Any]:
    project = _root_path(root)
    ledger = _read_json(_state_root(project) / "advisor_spend.json")
    calls = ledger.get("calls") if isinstance(ledger.get("calls"), list) else []
    advisor_map = _advisor_to_contestant(project, challenge)
    normalized: list[dict[str, Any]] = []
    totals_by_contestant: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    totals_by_model: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    cumulative = 0.0

    for raw in calls:
        if not isinstance(raw, dict):
            continue
        model = str(raw.get("model") or "unknown")
        advisor = _advisor_from_model(model)
        contestant = advisor_map.get(advisor, "unknown")
        input_tokens = int(raw.get("input_tokens") or 0)
        cached_tokens = int(raw.get("cached_tokens") or 0)
        output_tokens = int(raw.get("output_tokens") or 0)
        reasoning_tokens = int(raw.get("reasoning_tokens") or 0)
        cost = float(raw.get("cost_usd") or 0.0)
        cumulative += cost
        rec = {
            "ts": raw.get("ts"),
            "model": model,
            "advisor": advisor,
            "contestant_id": contestant,
            "input_tokens": input_tokens,
            "cached_tokens": cached_tokens,
            "output_tokens": output_tokens,
            "reasoning_tokens": reasoning_tokens,
            "total_tokens": input_tokens + output_tokens,
            "cost_usd": cost,
            "cumulative_cost_usd": round(cumulative, 6),
        }
        normalized.append(rec)
        for key, value in (
            ("calls", 1),
            ("input_tokens", input_tokens),
            ("cached_tokens", cached_tokens),
            ("output_tokens", output_tokens),
            ("reasoning_tokens", reasoning_tokens),
            ("total_tokens", input_tokens + output_tokens),
            ("cost_usd", cost),
        ):
            totals_by_contestant[contestant][key] += value
            totals_by_model[model][key] += value

    return {
        "cap_usd": float(ledger.get("cap_usd") or 0.0),
        "total_usd": float(ledger.get("total_usd") or 0.0),
        "pending": ledger.get("pending") if isinstance(ledger.get("pending"), list) else [],
        "calls": normalized,
        "totals_by_contestant": {
            k: {kk: (round(vv, 6) if kk == "cost_usd" else int(vv)) for kk, vv in v.items()}
            for k, v in sorted(totals_by_contestant.items())
        },
        "totals_by_model": {
            k: {kk: (round(vv, 6) if kk == "cost_usd" else int(vv)) for kk, vv in v.items()}
            for k, v in sorted(totals_by_model.items())
        },
    }


def collect_all(
    challenge: str,
    *,
    root: Path | str | None = None,
    daemon_url: str = DEFAULT_DAEMON_URL,
    bucket_minutes: int = 5,
) -> dict[str, Any]:
    return {
        "summary": collect_summary(challenge, root=root, daemon_url=daemon_url),
        "series": collect_series(challenge, root=root, bucket_minutes=bucket_minutes),
        "spend": collect_spend(challenge, root=root),
        "logs": collect_logs(challenge, root=root),
    }


INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Passive Manager Dashboard</title>
  <style>
    :root {
      --bg: #f5f7fa;
      --panel: #ffffff;
      --line: #d9e0e7;
      --text: #17202a;
      --muted: #5d6b79;
      --blue: #2667ff;
      --green: #00875a;
      --amber: #b56a00;
      --red: #c9372c;
      --violet: #7a4cc2;
      --cyan: #007c89;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      letter-spacing: 0;
    }
    header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 18px 24px;
      border-bottom: 1px solid var(--line);
      background: #ffffff;
      position: sticky;
      top: 0;
      z-index: 5;
    }
    h1 { margin: 0; font-size: 20px; font-weight: 700; }
    .subtle { color: var(--muted); font-size: 13px; }
    main { padding: 20px 24px 32px; max-width: 1500px; margin: 0 auto; }
    .cards {
      display: grid;
      grid-template-columns: repeat(5, minmax(150px, 1fr));
      gap: 12px;
      margin-bottom: 18px;
    }
    .card, .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
    }
    .card { padding: 14px; min-height: 92px; }
    .label { color: var(--muted); font-size: 12px; font-weight: 650; text-transform: uppercase; }
    .value { font-size: 25px; font-weight: 750; margin-top: 8px; overflow-wrap: anywhere; }
    .grid { display: grid; grid-template-columns: repeat(2, minmax(320px, 1fr)); gap: 14px; }
    .panel { padding: 14px; min-height: 300px; }
    .panel h2 { margin: 0 0 10px; font-size: 15px; }
    canvas { width: 100%; height: 230px; display: block; }
    table { width: 100%; border-collapse: collapse; font-size: 13px; }
    th, td { padding: 8px 6px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }
    th { color: var(--muted); font-size: 12px; font-weight: 700; }
    .wide { grid-column: 1 / -1; }
    .ok { color: var(--green); }
    .bad { color: var(--red); }
    .warn { color: var(--amber); }
    .mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
    @media (max-width: 980px) {
      .cards, .grid { grid-template-columns: 1fr; }
      header { align-items: flex-start; flex-direction: column; }
    }
  </style>
</head>
<body>
  <header>
    <div>
      <h1>Passive Manager Dashboard</h1>
      <div id="stamp" class="subtle">Loading</div>
    </div>
    <div id="daemon" class="subtle mono"></div>
  </header>
  <main>
    <section class="cards" id="cards"></section>
    <section class="grid">
      <div class="panel"><h2>Agent Activity</h2><canvas id="activity"></canvas></div>
      <div class="panel"><h2>Idle Time</h2><canvas id="idle"></canvas></div>
      <div class="panel"><h2>Oracle Calls</h2><canvas id="oracle"></canvas></div>
      <div class="panel"><h2>Warnings And Errors</h2><canvas id="logs"></canvas></div>
      <div class="panel"><h2>Advisor Tokens</h2><canvas id="tokens"></canvas></div>
      <div class="panel"><h2>Advisor Spend</h2><canvas id="spend"></canvas></div>
      <div class="panel"><h2>Prediction Accuracy</h2><canvas id="accuracy"></canvas></div>
      <div class="panel"><h2>Research Outputs</h2><canvas id="outputs"></canvas></div>
      <div class="panel wide"><h2>Contestants</h2><div id="contestants"></div></div>
      <div class="panel wide"><h2>Recent Warnings And Errors</h2><div id="logTable"></div></div>
    </section>
  </main>
  <script>
    const COLORS = ["#2667ff", "#00875a", "#b56a00", "#c9372c", "#7a4cc2", "#007c89"];

    function fmtAge(seconds) {
      if (seconds === null || seconds === undefined) return "unknown";
      if (seconds < 60) return Math.round(seconds) + "s";
      if (seconds < 3600) return Math.floor(seconds / 60) + "m";
      return Math.floor(seconds / 3600) + "h " + Math.floor((seconds % 3600) / 60) + "m";
    }

    function byId(id) { return document.getElementById(id); }

    async function fetchJson(url) {
      const res = await fetch(url, { cache: "no-store" });
      if (!res.ok) throw new Error(url + " -> " + res.status);
      return await res.json();
    }

    function drawLines(canvas, rows, fields, labels, colors) {
      const ctx = canvas.getContext("2d");
      const dpr = window.devicePixelRatio || 1;
      const rect = canvas.getBoundingClientRect();
      canvas.width = Math.max(1, Math.floor(rect.width * dpr));
      canvas.height = Math.max(1, Math.floor(rect.height * dpr));
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      const w = rect.width, h = rect.height, pad = 32;
      ctx.clearRect(0, 0, w, h);
      ctx.strokeStyle = "#d9e0e7";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(pad, 8);
      ctx.lineTo(pad, h - pad);
      ctx.lineTo(w - 8, h - pad);
      ctx.stroke();
      const values = [];
      for (const r of rows) for (const f of fields) values.push(Number(r[f] || 0));
      const maxY = Math.max(1, ...values);
      ctx.fillStyle = "#5d6b79";
      ctx.font = "12px system-ui";
      ctx.fillText(String(Math.ceil(maxY)), 6, 16);
      ctx.fillText("0", 14, h - pad + 4);
      fields.forEach((field, idx) => {
        ctx.strokeStyle = colors[idx % colors.length];
        ctx.lineWidth = 2;
        ctx.beginPath();
        rows.forEach((row, i) => {
          const x = pad + (rows.length <= 1 ? 0 : i * (w - pad - 12) / (rows.length - 1));
          const y = h - pad - (Number(row[field] || 0) / maxY) * (h - pad - 14);
          if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
        });
        ctx.stroke();
      });
      let lx = pad + 8;
      labels.forEach((label, idx) => {
        ctx.fillStyle = colors[idx % colors.length];
        ctx.fillRect(lx, h - 18, 10, 10);
        ctx.fillStyle = "#17202a";
        ctx.fillText(label, lx + 14, h - 9);
        lx += Math.min(180, 20 + label.length * 7);
      });
    }

    function seriesFields(contestants, prefix) {
      return contestants.map(c => `${prefix}_${c}`);
    }

    function seriesLabels(contestants, label) {
      return contestants.map(c => `${c} ${label}`);
    }

    function rowsWithSpendCalls(calls, contestants) {
      const cumulative = {};
      for (const id of contestants) cumulative[id] = 0;
      return calls.map(c => {
        const row = { ts: c.ts };
        const id = contestants.includes(c.contestant_id) ? c.contestant_id : "unknown";
        if (!(id in cumulative)) cumulative[id] = 0;
        cumulative[id] += Number(c.cost_usd || 0);
        for (const contestant of contestants) {
          row[`total_tokens_${contestant}`] = contestant === id ? Number(c.total_tokens || 0) : 0;
          row[`reasoning_tokens_${contestant}`] = contestant === id ? Number(c.reasoning_tokens || 0) : 0;
          row[`cumulative_cost_usd_${contestant}`] = cumulative[contestant] || 0;
        }
        return row;
      });
    }

    function renderCards(summary, spend) {
      const shared = summary.shared || {};
      const warnings = (summary.logs && summary.logs.warning) || 0;
      const errors = (summary.logs && summary.logs.error) || 0;
      const cards = [
        ["Daemon", summary.daemon && summary.daemon.alive ? "alive" : "down", summary.daemon && summary.daemon.alive ? "ok" : "bad"],
        ["Solved", summary.solved_flag_present ? "yes" : "no", summary.solved_flag_present ? "ok" : ""],
        ["Rounds", summary.rounds_done ?? "unknown", ""],
        ["Oracle Calls", shared.oracle_calls_total || 0, ""],
        ["Warnings/Errors", warnings + "/" + errors, errors ? "bad" : (warnings ? "warn" : "ok")]
      ];
      byId("cards").innerHTML = cards.map(([label, value, cls]) =>
        `<div class="card"><div class="label">${label}</div><div class="value ${cls}">${value}</div></div>`
      ).join("");
      byId("stamp").textContent = "Updated " + new Date(summary.generated_at).toLocaleString() +
        " | Advisor spend $" + Number(spend.total_usd || 0).toFixed(2);
      byId("daemon").textContent = summary.daemon ? summary.daemon.url : "";
    }

    function renderContestants(summary, spend) {
      const spendByAgent = spend.totals_by_contestant || {};
      const rows = Object.entries(summary.contestants || {}).map(([id, c]) => {
        const s = spendByAgent[id] || {};
        const accuracy = c.prediction_accuracy === null || c.prediction_accuracy === undefined
          ? "n/a"
          : Math.round(c.prediction_accuracy * 1000) / 10 + "%";
        return `
        <tr>
          <td class="mono">${id}</td>
          <td>${c.turns_total || 0}</td>
          <td>${fmtAge(c.idle_seconds)}</td>
          <td>${c.oracle_calls_total || 0}</td>
          <td>${c.oracle_predictions_total || 0}</td>
          <td>${accuracy}</td>
          <td>${c.oracle_cache_hits || 0}</td>
          <td>${c.oracle_errors || 0}</td>
          <td>${c.findings_count || 0}</td>
          <td>${c.breakthroughs_count || 0}</td>
          <td>${c.finish_proposals_count || 0}/${c.finish_rejections_count || 0}/${c.finish_agreements_count || 0}</td>
          <td>${s.total_tokens || 0}</td>
          <td>${s.reasoning_tokens || 0}</td>
          <td>$${Number(s.cost_usd || 0).toFixed(2)}</td>
          <td>${(c.direction || "").slice(0, 260)}</td>
        </tr>
      `;
      }).join("");
      byId("contestants").innerHTML = `<table><thead><tr><th>Agent</th><th>Turns</th><th>Idle</th><th>Oracle</th><th>Pred</th><th>Acc</th><th>Cache</th><th>Err</th><th>Find</th><th>Break</th><th>Finish P/R/A</th><th>Tokens</th><th>Reason</th><th>Cost</th><th>Direction</th></tr></thead><tbody>${rows}</tbody></table>`;
    }

    function renderLogs(logs) {
      const entries = (logs.entries || []).slice(-20).reverse();
      const rows = entries.map(e => `
        <tr>
          <td class="mono">${e.ts ? new Date(e.ts).toLocaleString() : ""}</td>
          <td class="${e.level === "warning" ? "warn" : "bad"}">${e.level}</td>
          <td>${e.message || ""}<div class="subtle mono">${e.source || ""}</div></td>
        </tr>
      `).join("");
      byId("logTable").innerHTML = `<table><thead><tr><th>Time</th><th>Level</th><th>Message</th></tr></thead><tbody>${rows}</tbody></table>`;
    }

    function idleRows(summary) {
      const row = { ts: summary.generated_at };
      for (const [id, c] of Object.entries(summary.contestants || {})) {
        row["idle_" + id] = Math.round((c.idle_seconds || 0) / 60);
      }
      return [row];
    }

    async function refresh() {
      const [summary, series, spend, logs] = await Promise.all([
        fetchJson("/api/summary"),
        fetchJson("/api/series"),
        fetchJson("/api/spend"),
        fetchJson("/api/logs")
      ]);
      renderCards(summary, spend);
      renderContestants(summary, spend);
      renderLogs(logs);
      const rows = series.series || [];
      const contestants = Object.keys(summary.contestants || {});
      drawLines(byId("activity"), rows, contestants.map(c => "turns_" + c), contestants, COLORS);
      drawLines(byId("idle"), rows, contestants.map(c => "idle_minutes_" + c), contestants.map(c => c + " idle minutes"), COLORS);
      drawLines(byId("oracle"), rows, seriesFields(contestants, "oracle_calls"), seriesLabels(contestants, "oracle calls"), COLORS);
      drawLines(byId("logs"), rows, ["log_warnings", "log_errors"], ["warnings", "errors"], [COLORS[2], COLORS[3]]);
      const spendRows = rowsWithSpendCalls(spend.calls || [], contestants);
      drawLines(byId("tokens"), spendRows, seriesFields(contestants, "total_tokens"), seriesLabels(contestants, "tokens"), COLORS);
      drawLines(byId("spend"), spendRows, seriesFields(contestants, "cumulative_cost_usd"), seriesLabels(contestants, "cumulative USD"), COLORS);
      drawLines(
        byId("accuracy"),
        rows,
        contestants.flatMap(c => [`oracle_predictions_correct_${c}`, `oracle_predictions_incorrect_${c}`]),
        contestants.flatMap(c => [`${c} correct`, `${c} incorrect`]),
        [COLORS[1], COLORS[3], COLORS[0], COLORS[2]]
      );
      drawLines(
        byId("outputs"),
        rows,
        contestants.flatMap(c => [`findings_${c}`, `breakthroughs_${c}`, `finish_proposed_${c}`, `finish_rejected_${c}`]),
        contestants.flatMap(c => [`${c} findings`, `${c} breaks`, `${c} finish`, `${c} reject`]),
        COLORS
      );
    }

    refresh().catch(err => {
      byId("stamp").textContent = String(err);
    });
    setInterval(() => refresh().catch(console.error), 5000);
    window.addEventListener("resize", () => refresh().catch(console.error));
  </script>
</body>
</html>
"""


def create_app(args: argparse.Namespace) -> web.Application:
    root = Path(args.root).resolve() if args.root else paths.PROJECT_ROOT

    async def index(_: web.Request) -> web.Response:
        return web.Response(text=INDEX_HTML, content_type="text/html")

    async def summary(_: web.Request) -> web.Response:
        return web.json_response(
            collect_summary(args.challenge, root=root, daemon_url=args.daemon),
            dumps=lambda obj: json.dumps(obj, default=str),
        )

    async def series(request: web.Request) -> web.Response:
        try:
            bucket = int(request.query.get("bucket_minutes", args.bucket_minutes))
        except ValueError:
            bucket = args.bucket_minutes
        return web.json_response(
            collect_series(args.challenge, root=root, bucket_minutes=bucket),
            dumps=lambda obj: json.dumps(obj, default=str),
        )

    async def spend(_: web.Request) -> web.Response:
        return web.json_response(collect_spend(args.challenge, root=root), dumps=lambda obj: json.dumps(obj, default=str))

    async def logs(_: web.Request) -> web.Response:
        return web.json_response(collect_logs(args.challenge, root=root), dumps=lambda obj: json.dumps(obj, default=str))

    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/api/summary", summary)
    app.router.add_get("/api/series", series)
    app.router.add_get("/api/spend", spend)
    app.router.add_get("/api/logs", logs)
    return app


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="read-only passive manager dashboard")
    ap.add_argument("--challenge", default="ising_lift")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8790)
    ap.add_argument("--daemon", default=DEFAULT_DAEMON_URL)
    ap.add_argument("--bucket-minutes", type=int, default=5)
    ap.add_argument("--root", default=None, help="project root override for tests")
    ap.add_argument("--once", action="store_true", help="print one JSON snapshot and exit")
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.once:
        snap = collect_all(
            args.challenge,
            root=(Path(args.root).resolve() if args.root else paths.PROJECT_ROOT),
            daemon_url=args.daemon,
            bucket_minutes=args.bucket_minutes,
        )
        print(json.dumps(snap, indent=2, default=str))
        return 0

    app = create_app(args)
    print(f"manager dashboard: http://{args.host}:{args.port}", file=sys.stderr)
    web.run_app(app, host=args.host, port=args.port, print=None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
