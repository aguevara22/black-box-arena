"""Self-contained exhaustive oracle for the weighted-graph demo."""
from __future__ import annotations

from itertools import product
from typing import Any


def _integer(value: Any) -> bool:
    return type(value) is int


def query(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"status": "malformed"}
    if set(payload) != {"n", "edges", "fields"}:
        return {"status": "malformed"}

    n = payload.get("n")
    edges = payload.get("edges")
    fields = payload.get("fields")
    if not _integer(n) or not 1 <= n <= 16:
        return {"status": "malformed"}
    if not isinstance(edges, list) or not isinstance(fields, list):
        return {"status": "malformed"}
    if len(fields) != n or any(not _integer(field) for field in fields):
        return {"status": "malformed"}

    checked_edges: list[tuple[int, int, int]] = []
    seen: set[tuple[int, int]] = set()
    for edge in edges:
        if not isinstance(edge, list) or len(edge) != 3:
            return {"status": "malformed"}
        i, j, coupling = edge
        if not all(_integer(value) for value in edge):
            return {"status": "malformed"}
        if not 0 <= i < j < n or coupling == 0 or (i, j) in seen:
            return {"status": "malformed"}
        seen.add((i, j))
        checked_edges.append((i, j, coupling))

    positive = 0
    negative = 0
    for state in product((-1, 1), repeat=n):
        value = -sum(
            coupling * state[i] * state[j]
            for i, j, coupling in checked_edges
        )
        value -= sum(field * state[index] for index, field in enumerate(fields))
        if value == 0:
            return {"status": "wall"}
        if value > 0:
            positive += 1
        else:
            negative += 1
    return {"status": "ok", "coefficient": positive - negative}
