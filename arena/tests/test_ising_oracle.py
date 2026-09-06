from __future__ import annotations

import pytest

from challenges.ising_lift.oracle import query


PINNED = [
    (
        {"n": 2, "edges": [[0, 1, 1]], "fields": [0, 0]},
        {"status": "ok", "coefficient": 0},
    ),
    (
        {
            "n": 3,
            "edges": [[0, 1, -1], [0, 2, -1], [1, 2, -1]],
            "fields": [0, 0, 0],
        },
        {"status": "ok", "coefficient": -4},
    ),
    (
        {
            "n": 3,
            "edges": [[0, 1, 1], [0, 2, 1], [1, 2, -1]],
            "fields": [0, 0, 0],
        },
        {"status": "ok", "coefficient": -4},
    ),
    (
        {
            "n": 3,
            "edges": [[0, 1, 1], [0, 2, 1], [1, 2, 1]],
            "fields": [0, 0, 0],
        },
        {"status": "ok", "coefficient": 4},
    ),
    (
        {"n": 2, "edges": [], "fields": [1, 2]},
        {"status": "ok", "coefficient": 0},
    ),
    (
        {"n": 2, "edges": [[0, 1, 1]], "fields": [1, 0]},
        {"status": "wall"},
    ),
    (
        {
            "n": 4,
            "edges": [[0, 1, 1], [2, 3, -2]],
            "fields": [0, 0, 0, 0],
        },
        {"status": "ok", "coefficient": 0},
    ),
]


@pytest.mark.parametrize(("payload", "expected"), PINNED)
def test_pinned_values(payload: dict, expected: dict) -> None:
    assert query(payload) == expected


@pytest.mark.parametrize(
    "payload",
    [
        {"n": 0, "edges": [], "fields": []},
        {"n": 2, "edges": [[1, 0, 1]], "fields": [0, 0]},
        {"n": 2, "edges": [[0, 1, 0]], "fields": [0, 0]},
        {"n": 2, "edges": [[0, 1, 1], [0, 1, 2]], "fields": [0, 0]},
        {"n": 2, "edges": [], "fields": [0]},
        {"n": True, "edges": [], "fields": [0]},
    ],
)
def test_malformed_values(payload: dict) -> None:
    assert query(payload) == {"status": "malformed"}
