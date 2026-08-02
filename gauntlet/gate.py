"""Gate/check data structures and immediate greppable output."""

from dataclasses import dataclass, field
import random
from typing import Callable

from .policy import Policy


VERDICTS = {"PASS", "FAIL", "SKIP"}


@dataclass
class Check:
    name: str
    config: str
    verdict: str
    criterion: str
    measured: str = ""
    runtime_s: float = 0.0
    extra: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.verdict not in VERDICTS:
            raise ValueError(f"invalid check verdict: {self.verdict!r}")


class GateContext:
    def __init__(
        self,
        policy: Policy,
        quick: bool,
        seed: int,
        rng: random.Random,
        gate_id: str,
    ) -> None:
        self.policy = policy
        self.quick = quick
        self.seed = seed
        self.rng = rng
        self.gate_id = gate_id
        self.checks: list[Check] = []

    def check(
        self,
        name,
        config,
        verdict_or_bool,
        criterion,
        measured="",
        extra=None,
        runtime_s=0.0,
    ) -> Check:
        if isinstance(verdict_or_bool, bool):
            verdict = "PASS" if verdict_or_bool else "FAIL"
        else:
            verdict = verdict_or_bool
        check = Check(
            name=str(name),
            config=str(config),
            verdict=verdict,
            criterion=str(criterion),
            measured=str(measured),
            runtime_s=float(runtime_s),
            extra={} if extra is None else dict(extra),
        )
        self.checks.append(check)
        print(
            f"[{self.gate_id:<11}] {check.verdict:<4} "
            f"{check.name} {check.config} {check.measured}",
            flush=True,
        )
        return check

    def skip(self, name, config, reason) -> Check:
        return self.check(
            name=name,
            config=config,
            verdict_or_bool="SKIP",
            criterion="skipped",
            measured=reason,
        )


@dataclass
class Gate:
    id: str
    title: str
    claim_ids: list[str]
    fn: Callable[[GateContext], None]
    policy: Policy
    tags: list = field(default_factory=list)


@dataclass
class GateResult:
    gate_id: str
    title: str
    claim_ids: list[str]
    checks: list[Check]
    runtime_s: float
    seed: int
    policy_snapshot: dict

    @property
    def n_pass(self) -> int:
        return sum(check.verdict == "PASS" for check in self.checks)

    @property
    def n_fail(self) -> int:
        return sum(check.verdict == "FAIL" for check in self.checks)

    @property
    def n_skip(self) -> int:
        return sum(check.verdict == "SKIP" for check in self.checks)

    @property
    def verdict(self) -> str:
        if self.n_fail:
            return "FAIL"
        if not self.n_pass:
            return "SKIP"
        return "PASS"
