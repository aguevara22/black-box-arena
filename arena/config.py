from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class ChallengeMeta(BaseModel):
    name: str
    display_name: str | None = None


class KernelConfig(BaseModel):
    """The Lean checker. Replaces the sealed oracle: not secret, but the
    daemon's kernel verdict is the only admissible evidence."""

    enabled: bool = True
    uses_mathlib: bool = False
    description: str = ""
    timeout_seconds: int = 120
    max_timeout_seconds: int = 600
    max_concurrent_builds: int = 1
    max_source_bytes: int = 262144
    lease_ttl_seconds: int = 1800
    workspace_root: str | None = None


class HygieneConfig(BaseModel):
    """Mechanical honesty gate. Source-scan limits are enforced by the daemon
    before any build; the kernel axiom audit is the authoritative verdict."""

    allowed_axioms: list[str] = Field(
        default_factory=lambda: ["propext", "Classical.choice", "Quot.sound"]
    )
    # Import roots contestants may use (prefix match on the first component).
    import_allowlist: list[str] = Field(default_factory=lambda: ["Defs", "Goal"])
    max_heartbeats: int = 400000
    max_rec_depth: int = 1024
    # Name of the goal Prop definition inside Goal.lean, e.g. Arena.GoalStatement.
    goal_def: str = "Arena.GoalStatement"
    # Optional freeze: sha256 of Goal.lean recorded at challenge-authoring time.
    # Empty string disables the check; when set, the daemon refuses to start on
    # mismatch.
    goal_sha256: str = ""


class HistorianConfig(BaseModel):
    enabled: bool = True
    every_n_rounds: int = 50


class BreakthroughsConfig(BaseModel):
    # Promotion paths are kernel-verdict (cite a successful job) and
    # predictive match (pre-registered predict came true). The legacy
    # token-overlap and LLM-critic paths are gone.
    allow_predictive_promotion: bool = True


class DaemonConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8787


class ContestantConfig(BaseModel):
    id: str
    advisor: str = ""
    advisor_env: str | None = None


class ChallengeConfig(BaseModel):
    challenge: ChallengeMeta
    kernel: KernelConfig = Field(default_factory=KernelConfig)
    hygiene: HygieneConfig = Field(default_factory=HygieneConfig)
    daemon: DaemonConfig = Field(default_factory=DaemonConfig)
    contestants: list[ContestantConfig] = Field(default_factory=list)
    historian: HistorianConfig = Field(default_factory=HistorianConfig)
    breakthroughs: BreakthroughsConfig = Field(default_factory=BreakthroughsConfig)


def load_config(path: Path) -> ChallengeConfig:
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return ChallengeConfig.model_validate(raw)
