"""Subprocess script that imports challenge oracles and answers queries.

This is the ONLY process that imports `challenges/<name>/oracle.py`. The daemon
never imports it. Communication is JSON-line over stdin/stdout.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import signal
import sys
import traceback
from pathlib import Path
from typing import Any

try:
    from .oracle_protocol import (
        ORACLE_PROTOCOL_VERSION,
        oracle_input_hash,
        response_metadata,
    )
except ImportError:  # direct script execution
    from oracle_protocol import (
        ORACLE_PROTOCOL_VERSION,
        oracle_input_hash,
        response_metadata,
    )

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class _Timeout(Exception):
    pass


def _timeout_handler(signum, frame):  # noqa: ARG001
    raise _Timeout()


def _load_oracle(challenge: str):
    oracle_path = PROJECT_ROOT / "challenges" / challenge / "oracle.py"
    if not oracle_path.exists():
        raise FileNotFoundError(f"oracle not found: {oracle_path}")
    spec = importlib.util.spec_from_file_location(
        f"_oracle_{challenge}", oracle_path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load oracle spec from {oracle_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not hasattr(module, "query"):
        raise AttributeError("oracle.py must define query(...)")
    return module.query


def _handle_one(query_fn, challenge: str, request: dict[str, Any], default_timeout: int) -> dict[str, Any]:
    payload = request.get("input")
    request_id = request.get("request_id") if isinstance(request.get("request_id"), str) else None
    actual_hash = oracle_input_hash(challenge, payload)
    meta = response_metadata(
        challenge=challenge,
        request_id=request_id,
        input_hash=actual_hash,
    )

    if request.get("protocol_version") != ORACLE_PROTOCOL_VERSION:
        return {
            **meta,
            "status": "error",
            "reason": "protocol_mismatch",
            "detail": "unsupported oracle protocol version",
        }
    if not request_id:
        return {
            **meta,
            "status": "error",
            "reason": "protocol_mismatch",
            "detail": "missing request_id",
        }
    if request.get("challenge") != challenge:
        return {
            **meta,
            "status": "error",
            "reason": "protocol_mismatch",
            "detail": "challenge mismatch",
        }
    if request.get("input_hash") != actual_hash:
        return {
            **meta,
            "status": "error",
            "reason": "protocol_mismatch",
            "detail": "input_hash mismatch",
        }

    timeout = int(request.get("timeout") or default_timeout)
    signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(timeout)
    try:
        if isinstance(payload, dict):
            output = query_fn(payload)
        elif isinstance(payload, list):
            output = query_fn(payload)
        else:
            output = query_fn(payload)
    except _Timeout:
        return {**meta, "status": "error", "reason": "timeout", "detail": f"exceeded {timeout}s"}
    except Exception as exc:  # noqa: BLE001
        return {
            **meta,
            "status": "error",
            "reason": "exception",
            "detail": f"{type(exc).__name__}: {exc}",
            "trace_summary": traceback.format_exc(limit=3),
        }
    finally:
        signal.alarm(0)
    return {**meta, "status": "ok", "output": output}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--challenge", required=True)
    ap.add_argument("--default-timeout", type=int, default=10)
    args = ap.parse_args()

    try:
        query_fn = _load_oracle(args.challenge)
    except Exception as exc:  # noqa: BLE001
        sys.stdout.write(
            json.dumps(
                {
                    "status": "fatal",
                    "protocol_version": ORACLE_PROTOCOL_VERSION,
                    "challenge": args.challenge,
                    "detail": f"load_error: {exc}",
                }
            )
            + "\n"
        )
        sys.stdout.flush()
        return

    sys.stdout.write(
        json.dumps(
            {
                "status": "ready",
                "protocol_version": ORACLE_PROTOCOL_VERSION,
                "challenge": args.challenge,
            }
        )
        + "\n"
    )
    sys.stdout.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            response = {
                "protocol_version": ORACLE_PROTOCOL_VERSION,
                "challenge": args.challenge,
                "request_id": None,
                "input_hash": None,
                "status": "error",
                "reason": "schema_violation",
                "detail": f"invalid JSON request: {exc}",
            }
        else:
            response = _handle_one(query_fn, args.challenge, request, args.default_timeout)
        sys.stdout.write(json.dumps(response, default=str) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
