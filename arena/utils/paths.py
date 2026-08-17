from __future__ import annotations

import os
from pathlib import Path

ARENA_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATE_ROOT = Path(
    os.environ.get("ORACLE_STATE_ROOT", str(PROJECT_ROOT / "state"))
).expanduser().resolve()

# Lean workspaces (mutable .lake build trees) must live OUTSIDE the repo:
# the repo may sit in an iCloud folder whose sync/eviction breaks builds.
DEFAULT_WORKSPACE_ROOT = Path.home() / ".cache" / "arena-lean"


def challenge_dir(name: str) -> Path:
    return PROJECT_ROOT / "challenges" / name


def state_dir(name: str) -> Path:
    return STATE_ROOT / name


def shared_dir(name: str) -> Path:
    return state_dir(name) / "shared"


def proofs_dir(name: str) -> Path:
    return state_dir(name) / "proofs"


def contestant_state_dir(challenge: str, contestant_id: str) -> Path:
    return state_dir(challenge) / "contestants" / contestant_id


def problem_path(challenge: str) -> Path:
    return challenge_dir(challenge) / "problem.md"


def config_path(challenge: str) -> Path:
    return challenge_dir(challenge) / "config.yaml"


def goal_path(challenge: str) -> Path:
    return challenge_dir(challenge) / "Goal.lean"


def defs_dir(challenge: str) -> Path:
    return challenge_dir(challenge) / "Defs"


def calibration_dir(challenge: str) -> Path:
    return challenge_dir(challenge) / "Calibration"


def workspace_root(configured: str | None = None) -> Path:
    env = os.environ.get("ARENA_LEAN_WORKSPACE_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    if configured:
        return Path(configured).expanduser().resolve()
    return DEFAULT_WORKSPACE_ROOT


def workspace_dir(challenge: str, configured: str | None = None) -> Path:
    return workspace_root(configured) / challenge


def ensure_state_layout(challenge: str) -> None:
    shared = shared_dir(challenge)
    shared.mkdir(parents=True, exist_ok=True)
    proofs_dir(challenge).mkdir(parents=True, exist_ok=True)
    for fname in (
        "findings.md",
        "breakthroughs.md",
        "finish.md",
        "jobs.md",
        "dag.md",
        "digest.md",
        "findings.jsonl",
        "breakthroughs.jsonl",
        "finish.jsonl",
        "jobs.jsonl",
        "dag.jsonl",
    ):
        f = shared / fname
        if not f.exists():
            f.touch()
