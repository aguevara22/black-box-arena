from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from jsonschema import Draft202012Validator
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


class OracleConfig(BaseModel):
    """The sealed numeric oracle (`ground_truth: oracle`). The challenge ships
    `oracle.py :: query(payload)`; only the daemon's worker subprocess ever
    imports it, and contestants receive answers only, each correlated by
    request id and input hash. This is the Black Box Arena v0.2 ground truth,
    restored beside the kernel."""

    enabled: bool = False
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    description: str = ""
    timeout_seconds: int = 10


class FinishGateConfig(BaseModel):
    """Mechanical evidence-matrix gate on finish proposals in the oracle
    regime: at least `min_rows` parsed `match=yes` EVIDENCE rows, covering
    every `required_tags` entry, before a proposal can even be verified."""

    min_rows: int = Field(default=0, ge=0)
    required_tags: list[str] = Field(default_factory=list)


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
    # Oracle-regime paths (`ground_truth: oracle`): independent cross-agent
    # confirmation (token overlap with the other contestant's own entries)
    # and an optional LLM critic that judges the claim against the cited
    # oracle evidence. Ignored in the kernel regime.
    require_independent_confirmation: bool = True
    allow_self_flag_with_verifier: bool = False


class DaemonConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8787


class ContestantConfig(BaseModel):
    id: str
    advisor: str = ""
    advisor_env: str | None = None


class ChallengeConfig(BaseModel):
    challenge: ChallengeMeta
    # Which ground truth the daemon serves: the Lean kernel (proof DAG,
    # hygiene audit) or a sealed numeric oracle (predict-before-query,
    # evidence-matrix finish gate). One daemon, one toggle.
    ground_truth: Literal["kernel", "oracle"] = "kernel"
    kernel: KernelConfig = Field(default_factory=KernelConfig)
    oracle: OracleConfig = Field(default_factory=OracleConfig)
    finish_gate: FinishGateConfig | None = None
    hygiene: HygieneConfig = Field(default_factory=HygieneConfig)
    daemon: DaemonConfig = Field(default_factory=DaemonConfig)
    contestants: list[ContestantConfig] = Field(default_factory=list)
    historian: HistorianConfig = Field(default_factory=HistorianConfig)
    breakthroughs: BreakthroughsConfig = Field(default_factory=BreakthroughsConfig)


def load_config(path: Path) -> ChallengeConfig:
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    cfg = ChallengeConfig.model_validate(raw)
    if cfg.ground_truth == "oracle":
        if not cfg.oracle.enabled:
            raise ValueError("ground_truth is 'oracle' but oracle.enabled is false")
        if cfg.oracle.input_schema:
            Draft202012Validator.check_schema(cfg.oracle.input_schema)
    return cfg


def build_input_validator(schema: dict[str, Any]) -> Draft202012Validator | None:
    if not schema:
        return None
    return Draft202012Validator(schema)
