"""Driver that talks to oracle_worker.py over JSON-line IPC.

The daemon imports this module, but it does NOT import the challenge oracle.
Only the sealed oracle subprocess does. Communication is JSON-line bidirectional.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
import uuid
from typing import Any

try:
    from .oracle_protocol import (
        ORACLE_PROTOCOL_VERSION,
        oracle_input_hash,
        response_metadata,
    )
    from .utils import paths
except ImportError:  # direct script/module execution
    from oracle_protocol import (
        ORACLE_PROTOCOL_VERSION,
        oracle_input_hash,
        response_metadata,
    )
    from utils import paths

log = logging.getLogger("daemon.oracle")


def validate_protocol_response(
    response: Any,
    *,
    challenge: str,
    request_id: str,
    input_hash: str,
) -> str | None:
    if not isinstance(response, dict):
        return "response is not an object"
    if response.get("protocol_version") != ORACLE_PROTOCOL_VERSION:
        return "protocol_version mismatch"
    if response.get("challenge") != challenge:
        return "challenge mismatch"
    if response.get("request_id") != request_id:
        return "request_id mismatch"
    if response.get("input_hash") != input_hash:
        return "input_hash mismatch"
    return None


class SealedOracleRunner:
    def __init__(self, challenge: str, default_timeout: int = 10):
        self.challenge = challenge
        self.default_timeout = default_timeout
        self._proc: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()
        self._project_root = paths.PROJECT_ROOT
        self._arena_root = paths.ARENA_ROOT

    async def start(self) -> None:
        if self._proc is not None:
            return
        log.info("starting sealed oracle subprocess for challenge=%s", self.challenge)
        self._proc = await asyncio.create_subprocess_exec(
            sys.executable,
            str(self._arena_root / "oracle_worker.py"),
            "--challenge",
            self.challenge,
            "--default-timeout",
            str(self.default_timeout),
            cwd=str(self._project_root),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert self._proc.stdout is not None
        ready_line = await self._proc.stdout.readline()
        if not ready_line:
            raise RuntimeError(
                f"oracle subprocess died on startup. stderr: {await self._read_stderr()}"
            )
        ready = json.loads(ready_line.decode("utf-8").strip())
        if ready.get("status") != "ready":
            raise RuntimeError(f"oracle init failed: {ready}")
        if ready.get("protocol_version") != ORACLE_PROTOCOL_VERSION:
            raise RuntimeError(f"oracle protocol mismatch on startup: {ready}")
        if ready.get("challenge") != self.challenge:
            raise RuntimeError(f"oracle challenge mismatch on startup: {ready}")

    async def stop(self) -> None:
        if self._proc is None:
            return
        try:
            if self._proc.stdin is not None and not self._proc.stdin.is_closing():
                self._proc.stdin.close()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                self._proc.terminate()
                await self._proc.wait()
        finally:
            self._proc = None

    async def _read_stderr(self) -> str:
        if self._proc is None or self._proc.stderr is None:
            return ""
        try:
            data = await asyncio.wait_for(self._proc.stderr.read(8192), timeout=1)
            return data.decode("utf-8", errors="replace")
        except asyncio.TimeoutError:
            return "<stderr read timed out>"

    async def _restart_after_bad_response(self, reason: str) -> None:
        log.warning("restarting sealed oracle subprocess after %s", reason)
        await self.stop()
        await self.start()

    def _error(
        self,
        *,
        reason: str,
        detail: str,
        request_id: str,
        input_hash: str,
        **extra: Any,
    ) -> dict[str, Any]:
        return {
            **response_metadata(
                challenge=self.challenge,
                request_id=request_id,
                input_hash=input_hash,
            ),
            "status": "error",
            "reason": reason,
            "detail": detail,
            **extra,
        }

    async def query(self, input_payload: Any, timeout: int | None = None) -> dict[str, Any]:
        request_timeout = timeout or self.default_timeout
        request_id = uuid.uuid4().hex
        input_hash = oracle_input_hash(self.challenge, input_payload)
        request = {
            "protocol_version": ORACLE_PROTOCOL_VERSION,
            "challenge": self.challenge,
            "request_id": request_id,
            "input_hash": input_hash,
            "input": input_payload,
            "timeout": request_timeout,
        }
        wire = json.dumps(request, default=str) + "\n"

        async with self._lock:
            if self._proc is None:
                await self.start()
            assert self._proc is not None
            assert self._proc.stdin is not None
            assert self._proc.stdout is not None

            try:
                self._proc.stdin.write(wire.encode("utf-8"))
                await self._proc.stdin.drain()
            except (BrokenPipeError, ConnectionResetError) as exc:
                stderr = await self._read_stderr()
                await self._restart_after_bad_response("transport_exception")
                return self._error(
                    reason="transport_exception",
                    detail=f"subprocess pipe closed: {exc}",
                    request_id=request_id,
                    input_hash=input_hash,
                    stderr=stderr,
                )
            try:
                line = await asyncio.wait_for(
                    self._proc.stdout.readline(),
                    timeout=request_timeout + 5,
                )
            except asyncio.TimeoutError:
                await self._restart_after_bad_response("transport_timeout")
                return self._error(
                    reason="transport_timeout",
                    detail="no response from oracle subprocess",
                    request_id=request_id,
                    input_hash=input_hash,
                )
            except asyncio.CancelledError:
                await self._restart_after_bad_response("cancelled")
                raise
            if not line:
                stderr = await self._read_stderr()
                await self._restart_after_bad_response("transport_exception")
                return self._error(
                    reason="transport_exception",
                    detail="oracle subprocess closed stdout",
                    request_id=request_id,
                    input_hash=input_hash,
                    stderr=stderr,
                )
            try:
                response = json.loads(line.decode("utf-8").strip())
            except json.JSONDecodeError as exc:
                await self._restart_after_bad_response("invalid_json")
                return self._error(
                    reason="protocol_mismatch",
                    detail=f"invalid JSON from oracle: {exc}",
                    request_id=request_id,
                    input_hash=input_hash,
                    raw=line.decode("utf-8", errors="replace"),
                )

            protocol_error = validate_protocol_response(
                response,
                challenge=self.challenge,
                request_id=request_id,
                input_hash=input_hash,
            )
            if protocol_error:
                await self._restart_after_bad_response(protocol_error)
                received = response if isinstance(response, dict) else {"raw": response}
                return self._error(
                    reason="protocol_mismatch",
                    detail=protocol_error,
                    request_id=request_id,
                    input_hash=input_hash,
                    received=received,
                )

            if response.get("status") == "error" and response.get("reason") in {
                "timeout",
                "protocol_mismatch",
            }:
                await self._restart_after_bad_response(str(response.get("reason")))
            return response
