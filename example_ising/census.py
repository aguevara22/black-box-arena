"""Complete triangle coupling-box census with code-hash provenance."""

from itertools import product
from pathlib import Path
import time

from gauntlet.provenance import write_artifact

from . import genericity, oracle_enum


ARTIFACT = Path(__file__).with_name("artifacts") / "census_triangle_L3.json"
EDGES = ((0, 1), (0, 2), (1, 2))


def signature(cfg):
    """Return signs of all eight states in the pinned state order."""
    genericity.check_generic(cfg)
    n, couplings, fields = cfg
    signs = []
    for state in range(1 << n):
        spins = []
        for vertex in range(n):
            spins.append(1 if state & (1 << vertex) == 0 else -1)
        energy = 0
        for (left, right), value in couplings.items():
            energy -= value * spins[left] * spins[right]
        for vertex in range(n):
            energy -= fields[vertex] * spins[vertex]
        signs.append(1 if energy > 0 else -1)
    return tuple(signs)


def signature_string(cfg):
    """Encode a sign tuple as a deterministic dictionary key."""
    return ",".join(str(value) for value in signature(cfg))


def code_files():
    """Return the producer files whose hashes guard the artifact."""
    return [
        str(Path(__file__)),
        str(Path(oracle_enum.__file__)),
        str(Path(genericity.__file__)),
    ]


def build_census(L=3):
    """Build and write the complete generic triangle census for L=3."""
    if L != 3:
        raise ValueError("this census is fixed at L=3")
    started = time.perf_counter()
    values = tuple(value for value in range(-L, L + 1) if value)
    entries = {}
    for j01, j02, j12 in product(values, repeat=3):
        couplings = {(0, 1): j01, (0, 2): j02, (1, 2): j12}
        cfg = (3, couplings, (0, 0, 0))
        try:
            genericity.check_generic(cfg)
        except genericity.NonGenericError:
            continue
        key = signature_string(cfg)
        signed = oracle_enum.signed_count(cfg)
        if key not in entries:
            entries[key] = {
                "signed": signed,
                "count": 0,
                "rep": [j01, j02, j12],
            }
        assert entries[key]["signed"] == signed
        entries[key]["count"] += 1
    payload = {key: entries[key] for key in sorted(entries)}
    runtime_s = time.perf_counter() - started
    write_artifact(
        ARTIFACT,
        payload,
        code_files(),
        {"L": 3, "graph": "triangle", "runtime_s": runtime_s},
    )
    return payload


if __name__ == "__main__":
    build_census()
    print(f"CENSUS BUILT {ARTIFACT}")

