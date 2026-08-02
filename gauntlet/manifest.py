"""Claim-ladder manifests and declared independence pairs."""

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path


STATUS_ORDER = ["conjectured", "numeric", "exact", "proved_small", "proved"]


@dataclass
class Claim:
    id: str
    statement: str
    status: str
    status_note: str = ""
    proof_ref: str = ""
    gates: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)


@dataclass
class IndependencePair:
    module_a: str
    module_b: str
    forbidden_shared: list[str]
    rationale: str


@dataclass
class Manifest:
    instance: str
    version: str
    description: str
    claims: list[Claim]
    independence: list[IndependencePair]

    def validate(self) -> list[str]:
        """Return every structural problem without stopping at the first."""
        problems = []
        ids = [claim.id for claim in self.claims]
        seen = set()
        for claim_id in ids:
            if claim_id in seen:
                problems.append(f"duplicate claim id: {claim_id}")
            seen.add(claim_id)
        known = set(ids)
        for claim in self.claims:
            for dependency in claim.depends_on:
                if dependency not in known:
                    problems.append(
                        f"claim {claim.id} depends on unknown claim {dependency}"
                    )
            if claim.status not in STATUS_ORDER:
                problems.append(
                    f"claim {claim.id} has unknown status {claim.status!r}"
                )
            if claim.status.startswith("proved") and not claim.proof_ref:
                problems.append(f"proved claim {claim.id} has no proof_ref")
            if not claim.gates:
                problems.append(f"claim {claim.id} has no gates")
        return problems

    def to_json(self, path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(asdict(self), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
