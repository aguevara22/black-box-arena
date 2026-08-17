"""Shared hashing / cache-key contract between daemon, kernel runner, and jobs.

The successor of oracle_protocol.py. Nothing here is secret; the point is
correlation and cache correctness: every check job is content-addressed by a
key that includes the toolchain fingerprint, so a toolchain or frozen-file
bump invalidates every cached verdict at once (the arena's "trust epoch",
made mechanical).
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

CHECK_PROTOCOL_VERSION = 1
CHECK_CACHE_VERSION = "lean-check-v1"

JOB_MODES = ("eval", "proof", "skeleton", "final")


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str)


def short_hash(obj: Any) -> str:
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()[:16]


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def toolchain_fingerprint(lean_version: str, file_hashes: dict[str, str]) -> str:
    """Fingerprint of everything a verdict depends on besides the submitted
    source: the lean version string and the sha256 of every frozen file
    (lean-toolchain, lake-manifest.json, Defs/**, Goal.lean, Calibration/**).

    file_hashes keys are workspace-relative POSIX paths, so two files with
    the same basename in different directories cannot collide.
    """
    payload = {"lean_version": lean_version, "files": dict(sorted(file_hashes.items()))}
    return "tc_" + hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()[:24]


def check_cache_key(
    *,
    challenge: str,
    fingerprint: str,
    mode: str,
    target: str | None,
    source_sha256: str,
    flags: dict[str, Any] | None = None,
) -> str:
    """Content address of a check job. `target` is the node id for
    proof/skeleton jobs, the goal decl for final jobs, None for eval.
    Full-width sha256: cache keys are compared, never displayed."""
    payload = {
        "v": CHECK_CACHE_VERSION,
        "challenge": challenge,
        "toolchain": fingerprint,
        "mode": mode,
        "target": target,
        "source": source_sha256,
        "flags": flags or {},
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
