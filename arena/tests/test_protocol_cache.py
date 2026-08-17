from __future__ import annotations

import pytest

from arena.protocol import check_cache_key, toolchain_fingerprint


BASE_CACHE_INPUT = {
    "challenge": "smoke_min",
    "fingerprint": "tc_original",
    "mode": "proof",
    "target": "double_one",
    "source_sha256": "source-original",
    "flags": {"max_heartbeats": 400000},
}


def test_check_cache_key_is_equal_for_identical_inputs() -> None:
    assert check_cache_key(**BASE_CACHE_INPUT) == check_cache_key(**BASE_CACHE_INPUT)


@pytest.mark.parametrize(
    ("field", "changed_value"),
    [
        ("fingerprint", "tc_changed"),
        ("mode", "skeleton"),
        ("target", "another_decl"),
        ("source_sha256", "source-changed"),
        ("flags", {"max_heartbeats": 399999}),
    ],
)
def test_check_cache_key_changes_with_each_verdict_input(
    field: str, changed_value: object
) -> None:
    changed = dict(BASE_CACHE_INPUT)
    changed[field] = changed_value
    assert check_cache_key(**changed) != check_cache_key(**BASE_CACHE_INPUT)


def test_toolchain_fingerprint_changes_with_lean_version() -> None:
    hashes = {"Defs/Basic.lean": "defs-hash", "Goal.lean": "goal-hash"}
    assert toolchain_fingerprint("Lean 4.33.0", hashes) != toolchain_fingerprint(
        "Lean 4.34.0", hashes
    )


@pytest.mark.parametrize("changed_file", ["Defs/Basic.lean", "Goal.lean"])
def test_toolchain_fingerprint_changes_when_any_file_hash_changes(
    changed_file: str,
) -> None:
    original = {"Defs/Basic.lean": "defs-hash", "Goal.lean": "goal-hash"}
    changed = dict(original)
    changed[changed_file] += "-changed"
    assert toolchain_fingerprint("Lean 4.33.0", changed) != toolchain_fingerprint(
        "Lean 4.33.0", original
    )


def test_toolchain_fingerprint_is_stable_under_dict_insertion_order() -> None:
    first = {"Defs/Basic.lean": "defs-hash", "Goal.lean": "goal-hash"}
    reversed_order = {"Goal.lean": "goal-hash", "Defs/Basic.lean": "defs-hash"}
    assert toolchain_fingerprint("Lean 4.33.0", first) == toolchain_fingerprint(
        "Lean 4.33.0", reversed_order
    )
