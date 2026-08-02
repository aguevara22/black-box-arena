from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator
from pydantic import BaseModel, Field


class ChallengeMeta(BaseModel):
    name: str
    display_name: str | None = None


class OracleConfig(BaseModel):
    enabled: bool = True
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    description: str = ""
    timeout_seconds: int = 10


class StoppingConfig(BaseModel):
    max_rounds: int | None = None
    external_solved_signal: str | None = None
    idle_minutes_to_halt: int | None = None


class HistorianConfig(BaseModel):
    enabled: bool = True
    every_n_rounds: int = 50


class BreakthroughsConfig(BaseModel):
    require_independent_confirmation: bool = True
    allow_self_flag_with_verifier: bool = True
    allow_predictive_promotion: bool = True


class FinishGateConfig(BaseModel):
    min_rows: int = Field(default=0, ge=0)
    required_tags: list[str] = Field(default_factory=list)


class DaemonConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8787


class ContestantConfig(BaseModel):
    id: str
    advisor: str = ""
    advisor_env: str | None = None


class ChallengeConfig(BaseModel):
    challenge: ChallengeMeta
    oracle: OracleConfig = Field(default_factory=OracleConfig)
    daemon: DaemonConfig = Field(default_factory=DaemonConfig)
    contestants: list[ContestantConfig] = Field(default_factory=list)
    stopping: StoppingConfig = Field(default_factory=StoppingConfig)
    historian: HistorianConfig = Field(default_factory=HistorianConfig)
    breakthroughs: BreakthroughsConfig = Field(default_factory=BreakthroughsConfig)
    finish_gate: FinishGateConfig | None = None


def load_config(path: Path) -> ChallengeConfig:
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    cfg = ChallengeConfig.model_validate(raw)
    if cfg.oracle.enabled and cfg.oracle.input_schema:
        Draft202012Validator.check_schema(cfg.oracle.input_schema)
    return cfg


def build_input_validator(schema: dict[str, Any]) -> Draft202012Validator | None:
    if not schema:
        return None
    return Draft202012Validator(schema)
