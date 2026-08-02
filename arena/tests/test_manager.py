from __future__ import annotations

import json
from pathlib import Path

from arena import manager


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _jsonl(path: Path, records: list[dict]) -> None:
    _write(path, "\n".join(json.dumps(record) for record in records) + "\n")


def test_manager_collects_without_writing(tmp_path) -> None:
    _write(
        tmp_path / "challenges" / "demo" / "config.yaml",
        "challenge:\n  name: demo\noracle:\n  enabled: false\ncontestants:\n"
        "  - { id: claude, advisor: openai }\n  - { id: codex, advisor: gemini }\n",
    )
    shared = tmp_path / "state" / "demo" / "shared"
    _jsonl(
        shared / "oracle_log.jsonl",
        [
            {
                "ts": "2026-07-24T10:00:00+00:00",
                "phase": "executed",
                "contestant_id": "codex",
                "input_hash": "one",
                "cache_hit": False,
                "prediction_correct": True,
                "result": {
                    "status": "ok",
                    "output": {"status": "ok", "coefficient": 0},
                },
            }
        ],
    )
    _jsonl(
        shared / "findings.jsonl",
        [{"ts": "2026-07-24T10:01:00+00:00", "contestant_id": "codex", "text": "finding"}],
    )
    _jsonl(
        shared / "breakthroughs.jsonl",
        [{"ts": "2026-07-24T10:02:00+00:00", "contestant_id": "claude", "text": "claim"}],
    )
    _write(tmp_path / "state" / "demo" / "round_counter.txt", "3")
    files = [path for path in (tmp_path / "state").rglob("*") if path.is_file()]
    before = {path: path.read_bytes() for path in files}

    summary = manager.collect_summary(
        "demo", root=tmp_path, daemon_url="http://127.0.0.1:1"
    )
    assert summary["rounds_done"] == 3
    assert summary["shared"]["oracle_calls_total"] == 1
    assert summary["shared"]["prediction_correct"] == 1
    assert summary["shared"]["findings_count"] == 1
    assert summary["shared"]["breakthroughs_count"] == 1
    assert {path: path.read_bytes() for path in files} == before
