from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CLIENT = ROOT / "arena" / "client.py"
DAEMON = ROOT / "arena" / "daemon.py"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _client(url: str, *args: str) -> tuple[subprocess.CompletedProcess[str], dict]:
    proc = subprocess.run(
        [sys.executable, str(CLIENT), *args, "--daemon", url],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=15,
    )
    payload = json.loads(proc.stdout)
    return proc, payload


def test_daemon_client_flow_uses_throwaway_state(tmp_path) -> None:
    port = _free_port()
    url = f"http://127.0.0.1:{port}"
    env = os.environ.copy()
    env["ORACLE_STATE_ROOT"] = str(tmp_path)
    daemon = subprocess.Popen(
        [
            sys.executable,
            str(DAEMON),
            "--challenge",
            "ising_lift",
            "--port",
            str(port),
        ],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            proc, health = _client(url, "health")
            if proc.returncode == 0:
                break
            time.sleep(0.1)
        else:
            raise AssertionError("daemon did not become healthy")

        assert health["oracle_enabled"] is True
        _, problem = _client(url, "problem")
        assert "Weighted-Graph Quantum Lift" in problem["problem"]

        _, answer = _client(
            url,
            "oracle",
            "--contestant-id",
            "codex",
            "--input",
            '{"n":2,"edges":[],"fields":[1,2]}',
        )
        assert answer["result"]["output"] == {"status": "ok", "coefficient": 0}

        rejected_proc, rejected = _client(
            url,
            "finish",
            "--contestant-id",
            "codex",
            "--text",
            "algorithm without checked rows",
        )
        assert rejected_proc.returncode == 1
        assert "finish_gate" in rejected["error"]
        assert not (tmp_path / "ising_lift" / "shared" / "finish.jsonl").read_text()
    finally:
        daemon.terminate()
        try:
            daemon.wait(timeout=5)
        except subprocess.TimeoutExpired:
            daemon.kill()
            daemon.wait(timeout=5)
