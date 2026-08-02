"""Policy data and platform-stable per-gate seed derivation."""

from dataclasses import asdict, dataclass, field
import hashlib


@dataclass
class Policy:
    tol: float = 1e-8
    time_budget_s: float | None = None
    samples: dict[str, int] = field(default_factory=dict)
    quick_factor: float = 0.3
    size_ladder: list = field(default_factory=list)
    precision_stages: list = field(default_factory=list)
    notes: str = ""

    def n_samples(self, key: str, quick: bool) -> int:
        factor = self.quick_factor if quick else 1
        return max(1, int(round(self.samples[key] * factor)))

    def snapshot(self) -> dict:
        return asdict(self)


def derive_seed(root_seed: int, gate_id: str) -> int:
    digest = hashlib.sha256(f"{root_seed}:{gate_id}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], byteorder="big")
