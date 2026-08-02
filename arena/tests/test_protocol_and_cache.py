from __future__ import annotations

import asyncio

from arena.oracle_protocol import (
    ORACLE_CACHE_VERSION,
    ORACLE_PROTOCOL_VERSION,
    oracle_input_hash,
)
from arena.oracle_runner import SealedOracleRunner, validate_protocol_response
from arena.state_manager import StateManager
from arena.utils import jsonl, paths


def test_sealed_subprocess_round_trip() -> None:
    async def run() -> None:
        runner = SealedOracleRunner("ising_lift")
        try:
            result = await runner.query(
                {"n": 3, "edges": [[0, 1, 1], [0, 2, 1], [1, 2, 1]], "fields": [0, 0, 0]}
            )
            assert result["status"] == "ok"
            assert result["output"] == {"status": "ok", "coefficient": 4}
            assert result["protocol_version"] == ORACLE_PROTOCOL_VERSION
            assert result["challenge"] == "ising_lift"
            assert isinstance(result["request_id"], str)
            assert result["input_hash"] == oracle_input_hash(
                "ising_lift",
                {"n": 3, "edges": [[0, 1, 1], [0, 2, 1], [1, 2, 1]], "fields": [0, 0, 0]},
            )
        finally:
            await runner.stop()

    asyncio.run(run())


def test_protocol_validation_rejects_wrong_request() -> None:
    payload = {"n": 1, "edges": [], "fields": [2]}
    input_hash = oracle_input_hash("ising_lift", payload)
    response = {
        "status": "ok",
        "output": {"status": "ok", "coefficient": 0},
        "protocol_version": ORACLE_PROTOCOL_VERSION,
        "challenge": "ising_lift",
        "request_id": "older",
        "input_hash": input_hash,
    }
    assert (
        validate_protocol_response(
            response,
            challenge="ising_lift",
            request_id="current",
            input_hash=input_hash,
        )
        == "request_id mismatch"
    )


def test_cache_accepts_only_current_correlated_records(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(paths, "STATE_ROOT", tmp_path)
    state = StateManager("ising_lift")
    payload = {"n": 2, "edges": [], "fields": [1, 2]}
    input_hash = oracle_input_hash("ising_lift", payload)

    jsonl.append(
        state.shared / "oracle_log.jsonl",
        {
            "phase": "executed",
            "input": payload,
            "input_hash": input_hash,
            "result": {"status": "ok", "output": {"status": "ok", "coefficient": 99}},
        },
    )
    assert state.oracle_cache_lookup(payload) is None

    async def record() -> None:
        await state.record_oracle_result(
            "codex",
            payload,
            {
                "status": "ok",
                "output": {"status": "ok", "coefficient": 0},
                "protocol_version": ORACLE_PROTOCOL_VERSION,
                "challenge": "ising_lift",
                "request_id": "current",
                "input_hash": input_hash,
            },
        )

    asyncio.run(record())
    cached = state.oracle_cache_lookup(payload)
    assert cached is not None
    assert cached["output"]["coefficient"] == 0
    records = jsonl.read_all(state.shared / "oracle_log.jsonl")
    assert records[-1]["cache_version"] == ORACLE_CACHE_VERSION
    assert records[-1]["oracle_protocol_verified"] is True
