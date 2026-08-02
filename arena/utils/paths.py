from __future__ import annotations

import os
from pathlib import Path

ARENA_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATE_ROOT = Path(
    os.environ.get("ORACLE_STATE_ROOT", str(PROJECT_ROOT / "state"))
).expanduser().resolve()


def challenge_dir(name: str) -> Path:
    return PROJECT_ROOT / "challenges" / name


def state_dir(name: str) -> Path:
    return STATE_ROOT / name


def shared_dir(name: str) -> Path:
    return state_dir(name) / "shared"


def contestant_state_dir(challenge: str, contestant_id: str) -> Path:
    return state_dir(challenge) / "contestants" / contestant_id


def oracle_path(challenge: str) -> Path:
    return challenge_dir(challenge) / "oracle.py"


def problem_path(challenge: str) -> Path:
    return challenge_dir(challenge) / "problem.md"


def config_path(challenge: str) -> Path:
    return challenge_dir(challenge) / "config.yaml"


def ensure_state_layout(challenge: str) -> None:
    shared = shared_dir(challenge)
    shared.mkdir(parents=True, exist_ok=True)
    for fname in (
        "findings.md",
        "breakthroughs.md",
        "finish.md",
        "oracle_log.md",
        "digest.md",
        "findings.jsonl",
        "breakthroughs.jsonl",
        "finish.jsonl",
        "oracle_log.jsonl",
    ):
        f = shared / fname
        if not f.exists():
            f.touch()
