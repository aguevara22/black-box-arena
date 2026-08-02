from __future__ import annotations

import hashlib
import json
from typing import Any

ORACLE_PROTOCOL_VERSION = 2
ORACLE_CACHE_VERSION = "request-correlated-v2"


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str)


def short_hash(obj: Any) -> str:
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()[:16]


def oracle_input_hash(challenge: str, input_payload: Any) -> str:
    return short_hash({"challenge": challenge, "input": input_payload})


def response_metadata(
    *,
    challenge: str,
    request_id: str | None,
    input_hash: str | None,
) -> dict[str, Any]:
    return {
        "protocol_version": ORACLE_PROTOCOL_VERSION,
        "challenge": challenge,
        "request_id": request_id,
        "input_hash": input_hash,
    }
