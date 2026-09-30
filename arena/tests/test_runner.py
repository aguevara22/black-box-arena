"""The runner: outcome classification, backoff, driver commands, and one real
round against a live daemon with a scripted stand-in for the agent."""
from __future__ import annotations

import json
import os
import shlex
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from arena import runner

ROOT = Path(__file__).resolve().parents[2]
CLIENT = ROOT / "arena" / "client.py"
DAEMON = ROOT / "arena" / "daemon.py"
RUNNER = ROOT / "arena" / "runner.py"


def test_outcomes_are_classified_from_exit_output_and_the_record():
    assert runner.classify_outcome(0, "did a tick", True, False) == "ok"
    assert runner.classify_outcome(0, "did nothing", False, False) == "idle"
    assert runner.classify_outcome(1, "Traceback ...", False, False) == "crash"
    assert runner.classify_outcome(1, "You've hit your usage limit; resets at 3pm", False, False) == "limit"
    assert runner.classify_outcome(0, "Error: 429 Too Many Requests", False, False) == "limit"
    assert runner.classify_outcome(1, "Please run `codex login`", False, False) == "auth"
    assert runner.classify_outcome(1, "Could not resolve authentication method", False, False) == "auth"
    assert runner.classify_outcome(None, "", False, True) == "timeout"
    # a recorded turn beats a stray "limit" word in the transcript
    assert runner.classify_outcome(0, "note: the quota of examples is 5", True, False) == "ok"


def test_backoff_grows_and_caps():
    assert [runner.backoff_seconds("limit", n) for n in (1, 2, 5, 9)] == [300, 600, 3600, 3600]
    assert [runner.backoff_seconds("crash", n) for n in (1, 3, 5, 8)] == [60, 240, 900, 900]


def test_prompts_name_the_seat_the_daemon_and_the_sealed_file():
    first = runner.build_prompt("codex", "http://127.0.0.1:1", "my_case", 1, True)
    assert "CONTESTANT_ID=codex" in first and "http://127.0.0.1:1" in first
    assert "challenges/my_case/oracle.py" in first and "CONTESTANT.md" in first
    later = runner.build_prompt("codex", "http://127.0.0.1:1", "my_case", 7, False)
    assert "Round 7" in later and "one tick" in later


def test_claude_driver_pins_then_resumes_one_session(tmp_path):
    drv = runner.ClaudeDriver("auto", ["--verbose"])
    seat = runner.Seat("claude", drv, "c", "http://x", tmp_path)
    argv = drv.argv(seat, "P1", tmp_path / "p")
    assert argv[:3] == ["claude", "-p", "P1"] and "--session-id" in argv and "--verbose" in argv
    sid = argv[argv.index("--session-id") + 1]
    drv.after_run(seat, 'noise\n{"type":"result","session_id":"%s","result":"ok"}\n' % sid)
    argv2 = drv.argv(seat, "P2", tmp_path / "p")
    assert argv2[:4] == ["claude", "-p", "--resume", sid] and argv2[4] == "P2"


def test_codex_driver_execs_then_resumes_the_thread(tmp_path):
    drv = runner.CodexDriver("workspace-write", ["-m", "some-model"])
    seat = runner.Seat("codex", drv, "c", "http://x", tmp_path)
    argv = drv.argv(seat, "P1", tmp_path / "p")
    assert argv[:2] == ["codex", "exec"] and "-s" in argv and "--json" in argv and argv[-1] == "P1"
    drv.after_run(seat, '{"type":"thread.started","thread_id":"t-123"}\n{"type":"turn.completed"}\n')
    argv2 = drv.argv(seat, "P2", tmp_path / "p")
    assert argv2[:4] == ["codex", "exec", "resume", "t-123"] and argv2[-1] == "P2"
    assert runner.extract_codex_thread_id("garbage\n") is None


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def live_daemon(tmp_path):
    port = _free_port()
    url = f"http://127.0.0.1:{port}"
    env = os.environ.copy()
    env["ORACLE_STATE_ROOT"] = str(tmp_path / "state")
    proc = subprocess.Popen(
        [sys.executable, str(DAEMON), "--challenge", "ising_lift", "--port", str(port)],
        cwd=ROOT, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            health = subprocess.run([sys.executable, str(CLIENT), "health", "--daemon", url],
                                    cwd=ROOT, text=True, capture_output=True)
            if health.returncode == 0:
                break
            time.sleep(0.2)
        else:
            raise AssertionError("daemon did not become healthy")
        yield url, env
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def _run_runner(env, url, template, extra=()):
    return subprocess.run(
        [sys.executable, str(RUNNER), "--challenge", "ising_lift", "--daemon", url,
         "--seat", f"claude=command:{template}", "--once", "--pause", "0", "--round-timeout", "60", *extra],
        cwd=ROOT, env=env, text=True, capture_output=True, timeout=120,
    )


def test_one_round_with_a_scripted_agent_records_a_turn(live_daemon, tmp_path):
    url, env = live_daemon
    fake = tmp_path / "fake_agent.py"
    fake.write_text(
        "import subprocess, sys\n"
        "seat, url = sys.argv[1], sys.argv[2]\n"
        f"subprocess.run([sys.executable, {str(CLIENT)!r}, 'turn', '--contestant-id', seat, '--daemon', url,\n"
        "                '--record', '{\"reply_text\": \"fake tick\"}'], check=True)\n"
        "print('tick done')\n",
        encoding="utf-8",
    )
    template = f"{shlex.quote(sys.executable)} {shlex.quote(str(fake))} {{seat}} {{url}}"
    proc = _run_runner(env, url, template)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    gauge = json.loads((tmp_path / "state" / "ising_lift" / "runner" / "claude.json").read_text())
    assert gauge["last_outcome"] == "ok" and gauge["round"] == 1 and gauge["last_turn_ts"]
    assert "round 1 ok" in proc.stdout
    log_text = (tmp_path / "state" / "ising_lift" / "runner" / "claude.log").read_text()
    assert "tick done" in log_text


def test_one_round_where_the_agent_does_nothing_is_idle(live_daemon, tmp_path):
    url, env = live_daemon
    proc = _run_runner(env, url, "true")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    gauge = json.loads((tmp_path / "state" / "ising_lift" / "runner" / "claude.json").read_text())
    assert gauge["last_outcome"] == "idle" and gauge["consecutive_idle"] == 1


def test_agents_get_a_closed_stdin_so_they_cannot_wait_on_it(live_daemon, tmp_path):
    # Found live: `codex exec` printed "Reading additional input from stdin..." and
    # hung until the round timeout because the runner's stdin was inherited.
    url, env = live_daemon
    started = time.monotonic()
    proc = _run_runner(env, url, "cat")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert time.monotonic() - started < 30
    gauge = json.loads((tmp_path / "state" / "ising_lift" / "runner" / "claude.json").read_text())
    assert gauge["last_outcome"] == "idle"


def test_dry_run_prints_the_first_commands(tmp_path):
    env = os.environ.copy()
    env["ORACLE_STATE_ROOT"] = str(tmp_path)
    proc = subprocess.run(
        [sys.executable, str(RUNNER), "--challenge", "ising_lift", "--dry-run"],
        cwd=ROOT, env=env, text=True, capture_output=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.strip().splitlines()
    assert lines[0].startswith("claude -p ") and "--session-id" in lines[0]
    assert lines[1].startswith("codex exec ") and "--json" in lines[1]
