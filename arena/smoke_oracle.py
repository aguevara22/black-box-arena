#!/usr/bin/env python3
"""Deterministic end-to-end retest of the ORACLE regime using only localhost
subprocesses: boots the daemon on challenges/ising_lift (ground_truth:
oracle), drives two scripted contestants through the whole funnel — sealed
queries with predict-before-query promotion, findings, breakthrough promotion
by cross-confirmation, the mechanical evidence-matrix finish gate, peer
verification, SOLVED — against a throwaway state root, and asserts the state
layout afterwards. No Lean toolchain needed. The kernel-regime counterpart is
smoke_arena.py."""
from __future__ import annotations

import contextlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Iterator


ROOT = Path(__file__).resolve().parent.parent
CLIENT = ROOT / "arena" / "client.py"
DAEMON = ROOT / "arena" / "daemon.py"
CURRENT_STEP = "startup"


class SmokeFailure(AssertionError):
    pass


class Checks:
    def __init__(self) -> None:
        self.total = 0
        self.current_step = "startup"

    def check(self, condition: bool, message: str) -> None:
        self.total += 1
        if not condition:
            raise SmokeFailure(message)

    @contextlib.contextmanager
    def step(self, number: int, label: str) -> Iterator[None]:
        global CURRENT_STEP
        self.current_step = f"{number} ({label})"
        CURRENT_STEP = self.current_step
        before = self.total
        yield
        print(
            f"ORACLE-SMOKE: step {number} {label} "
            f"OK ({self.total - before} assertions)",
            flush=True,
        )


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _read_jsonl(path: Path) -> list[dict]:
    records: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(json.loads(line))
    return records


def _line_count(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines())


def _shared_counts(shared: Path) -> dict[str, int]:
    return {
        f"{stem}.{suffix}": _line_count(shared / f"{stem}.{suffix}")
        for stem in ("findings", "breakthroughs", "finish", "oracle_log")
        for suffix in ("jsonl", "md")
    }


def _run_client(
    checks: Checks,
    url: str,
    *args: str,
    expect_ok: bool = True,
    stdin_text: str | None = None,
) -> dict:
    proc = subprocess.run(
        [sys.executable, str(CLIENT), *args, "--daemon", url],
        cwd=ROOT,
        text=True,
        input=stdin_text,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=20,
    )
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise SmokeFailure(
            f"client returned invalid JSON: {exc}; stdout={proc.stdout!r}; "
            f"stderr={proc.stderr!r}"
        ) from exc
    checks.check(
        bool(payload.get("ok")) is expect_ok,
        f"unexpected client payload for {args}: {payload}",
    )
    checks.check(
        (proc.returncode == 0) is expect_ok,
        f"unexpected client exit {proc.returncode} for {args}: {payload}",
    )
    return payload


def _oracle_value(payload: dict) -> int | str:
    output = payload["result"]["output"]
    if output["status"] == "wall":
        return "wall"
    if output["status"] != "ok":
        raise SmokeFailure(f"unexpected oracle output: {output}")
    return int(output["coefficient"])


def _evidence_line(case: dict, value: int | str) -> str:
    compact = json.dumps(case["input"], separators=(",", ":"), sort_keys=True)
    tags = ",".join(case["tags"])
    return (
        f"EVIDENCE: input={compact} | tags={tags} | oracle={value} "
        f"| proposed={value} | match=yes"
    )


def run_smoke() -> int:
    checks = Checks()
    port = _free_port()
    url = f"http://127.0.0.1:{port}"
    count_history: list[dict[str, int]] = []
    daemon: subprocess.Popen[str] | None = None

    with tempfile.TemporaryDirectory(prefix="arena-smoke-") as temporary:
        state_root = Path(temporary)
        env = os.environ.copy()
        env["ORACLE_STATE_ROOT"] = str(state_root)
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
            with checks.step(1, "health + problem"):
                health: dict | None = None
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    if daemon.poll() is not None:
                        stderr = daemon.stderr.read() if daemon.stderr else ""
                        raise SmokeFailure(f"daemon exited during startup: {stderr}")
                    probe = subprocess.run(
                        [
                            sys.executable,
                            str(CLIENT),
                            "health",
                            "--daemon",
                            url,
                        ],
                        cwd=ROOT,
                        text=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        timeout=5,
                    )
                    if probe.returncode == 0:
                        health = json.loads(probe.stdout)
                        break
                    time.sleep(0.1)
                checks.check(health is not None, "daemon did not become healthy")
                checks.check(health["challenge"] == "ising_lift", "wrong challenge")
                checks.check(health["oracle_enabled"] is True, "oracle is not enabled")
                checks.check(health["solved"] is False, "fresh state is already solved")
                problem = _run_client(checks, url, "problem")
                checks.check(
                    "Weighted-Graph Quantum Lift" in problem["problem"],
                    "problem text was not served",
                )
                checks.check(problem["oracle_enabled"] is True, "problem hid oracle mode")
                lowered = problem["problem"].lower()
                checks.check(
                    all(word not in lowered for word in ("ising", "spin", "energy")),
                    "contestant problem reveals the hidden interpretation",
                )
                shared = state_root / "ising_lift" / "shared"
                count_history.append(_shared_counts(shared))

            pinned = [
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
            with checks.step(2, "pinned oracle rows"):
                for index, (input_payload, expected) in enumerate(pinned, start=1):
                    reply = _run_client(
                        checks,
                        url,
                        "oracle",
                        "--contestant-id",
                        "claude",
                        "--input",
                        json.dumps(input_payload, separators=(",", ":")),
                    )
                    result = reply["result"]
                    checks.check(result["status"] == "ok", f"row {index} transport failed")
                    checks.check(result["output"] == expected, f"row {index} mismatch")
                    checks.check(
                        isinstance(result.get("request_id"), str),
                        f"row {index} lacks request id",
                    )
                    checks.check(
                        isinstance(result.get("input_hash"), str),
                        f"row {index} lacks input hash",
                    )
                    value = (
                        "wall"
                        if expected["status"] == "wall"
                        else expected["coefficient"]
                    )
                    print(
                        f"ORACLE-SMOKE: pinned row {index} -> "
                        f"{expected['status']}, {value}",
                        flush=True,
                    )
                count_history.append(_shared_counts(shared))

            with checks.step(3, "predictive promotion"):
                frustrated = {
                    "n": 3,
                    "edges": [[1, 2, -1], [0, 2, -1], [0, 1, -1]],
                    "fields": [0, 0, 0],
                }
                hypothesis = (
                    "Odd frustrated cycles produce a negative coefficient under "
                    "the proposed extraction"
                )
                predicted = _run_client(
                    checks,
                    url,
                    "oracle",
                    "--contestant-id",
                    "claude",
                    "--input",
                    json.dumps(frustrated, separators=(",", ":")),
                    "--predict",
                    '{"status":"ok","coefficient":-4}',
                    "--hypothesis",
                    hypothesis,
                )
                checks.check(predicted["prediction_correct"] is True, "prediction missed")
                checks.check(
                    predicted["promoted_breakthrough"] is True,
                    "prediction did not promote",
                )
                breakthroughs = _read_jsonl(shared / "breakthroughs.jsonl")
                checks.check(
                    any(
                        record.get("promoted_via") == "predictive_match"
                        and record.get("text") == hypothesis
                        for record in breakthroughs
                    ),
                    "predictive promotion is absent from shared state",
                )
                count_history.append(_shared_counts(shared))

            with checks.step(4, "independent confirmation"):
                claim = (
                    "A vertex sign substitution preserves the coefficient when "
                    "every incident coupling and that vertex field change sign."
                )
                finding = _run_client(
                    checks,
                    url,
                    "finding",
                    "--contestant-id",
                    "codex",
                    "--text",
                    claim,
                )
                checks.check(bool(finding.get("id")), "finding id missing")
                promoted = _run_client(
                    checks,
                    url,
                    "breakthrough",
                    "--contestant-id",
                    "claude",
                    "--text",
                    claim,
                )
                checks.check(promoted["promoted"] is True, "cross-confirmation failed")
                checks.check(
                    str(promoted["via"]).startswith("independent_confirmation:codex:"),
                    f"wrong promotion path: {promoted['via']}",
                )
                breakthroughs = _read_jsonl(shared / "breakthroughs.jsonl")
                checks.check(
                    any(
                        str(record.get("promoted_via", "")).startswith(
                            "independent_confirmation:"
                        )
                        for record in breakthroughs
                    ),
                    "confirmed promotion is absent from shared state",
                )
                codex_turn = _run_client(
                    checks,
                    url,
                    "turn",
                    "--contestant-id",
                    "codex",
                    "--record",
                    '{"reply_text":"posted independent finding","oracle_calls":[]}',
                )
                claude_turn = _run_client(
                    checks,
                    url,
                    "turn",
                    "--contestant-id",
                    "claude",
                    "--record",
                    '{"reply_text":"posted confirmed claim","oracle_calls":[]}',
                )
                checks.check(codex_turn["round"] == 1, "first turn did not advance round")
                checks.check(claude_turn["round"] == 2, "second turn did not advance round")
                count_history.append(_shared_counts(shared))

            with checks.step(5, "negative finish"):
                before_finish = _line_count(shared / "finish.jsonl")
                rejected = _run_client(
                    checks,
                    url,
                    "finish",
                    "--contestant-id",
                    "codex",
                    "--text",
                    "A complete algorithm statement without checked rows.",
                    expect_ok=False,
                )
                checks.check(
                    "finish_gate" in rejected["error"],
                    f"unclear finish rejection: {rejected['error']}",
                )
                checks.check(
                    _line_count(shared / "finish.jsonl") == before_finish,
                    "rejected finish entered verification",
                )
                print(
                    f"ORACLE-SMOKE: negative rejection: {rejected['error']}",
                    flush=True,
                )
                count_history.append(_shared_counts(shared))

            matrix = [
                {
                    "input": {"n": 2, "edges": [[0, 1, 1]], "fields": [0, 0]},
                    "tags": ["n2", "nofields"],
                },
                {
                    "input": {"n": 2, "edges": [], "fields": [1, 2]},
                    "tags": ["n2", "fields", "disconnected"],
                },
                {
                    "input": {"n": 2, "edges": [[0, 1, 1]], "fields": [1, 0]},
                    "tags": ["n2", "fields", "wall_checked"],
                },
                {
                    "input": {
                        "n": 3,
                        "edges": [[0, 1, -1], [0, 2, -1], [1, 2, -1]],
                        "fields": [0, 0, 0],
                    },
                    "tags": ["n3", "nofields", "frustrated", "leak_pair"],
                },
                {
                    "input": {
                        "n": 3,
                        "edges": [[0, 1, 1], [0, 2, 1], [1, 2, -1]],
                        "fields": [0, 0, 0],
                    },
                    "tags": ["n3", "nofields", "leak_pair"],
                },
                {
                    "input": {
                        "n": 3,
                        "edges": [[0, 1, 1], [0, 2, 1], [1, 2, 1]],
                        "fields": [0, 0, 0],
                    },
                    "tags": ["n3", "nofields"],
                },
                {
                    "input": {
                        "n": 4,
                        "edges": [[0, 1, 1], [2, 3, -2]],
                        "fields": [0, 0, 0, 0],
                    },
                    "tags": ["n4", "nofields", "disconnected"],
                },
                {
                    "input": {"n": 4, "edges": [], "fields": [1, 2, 4, 8]},
                    "tags": ["n4", "fields", "disconnected"],
                },
                {
                    "input": {
                        "n": 4,
                        "edges": [[0, 1, 2]],
                        "fields": [1, 2, 4, 8],
                    },
                    "tags": ["n4", "fields", "disconnected"],
                },
                {
                    "input": {
                        "n": 5,
                        "edges": [],
                        "fields": [1, 2, 4, 8, 16],
                    },
                    "tags": ["n5", "fields", "disconnected"],
                },
                {
                    "input": {
                        "n": 5,
                        "edges": [[0, 1, 1]],
                        "fields": [0, 0, 0, 0, 0],
                    },
                    "tags": ["n5", "nofields", "disconnected"],
                },
                {
                    "input": {
                        "n": 5,
                        "edges": [[0, 1, -1], [0, 2, -1], [1, 2, -1]],
                        "fields": [0, 0, 0, 0, 0],
                    },
                    "tags": ["n5", "nofields", "frustrated", "disconnected"],
                },
            ]
            with checks.step(6, "gated real finish"):
                evidence: list[str] = []
                covered: set[str] = set()
                for case in matrix:
                    reply = _run_client(
                        checks,
                        url,
                        "oracle",
                        "--contestant-id",
                        "codex",
                        "--input",
                        json.dumps(case["input"], separators=(",", ":")),
                    )
                    checks.check(
                        reply["result"]["status"] == "ok",
                        f"evidence query transport failed: {reply}",
                    )
                    value = _oracle_value(reply)
                    evidence.append(_evidence_line(case, value))
                    covered.update(case["tags"])
                required = {
                    "n2",
                    "n3",
                    "n4",
                    "n5",
                    "fields",
                    "nofields",
                    "frustrated",
                    "leak_pair",
                    "wall_checked",
                    "disconnected",
                }
                checks.check(len(evidence) >= 12, "evidence matrix is too short")
                checks.check(required <= covered, "evidence tags are incomplete")
                finish_text = (
                    "Algorithm: enumerate signed vertex assignments, collect the "
                    "graded terms into a Laurent coefficient table, and apply the "
                    "stated extraction.\n"
                    + "\n".join(evidence)
                )
                accepted = _run_client(
                    checks,
                    url,
                    "finish",
                    "--contestant-id",
                    "codex",
                    "--text-file",
                    "-",
                    stdin_text=finish_text,
                )
                checks.check(accepted["needs_verification"] is True, "finish skipped peer review")
                checks.check(bool(accepted.get("id")), "finish id missing")
                proposal_id = accepted["id"]
                checks.check(
                    _line_count(shared / "finish.jsonl") == before_finish + 1,
                    "accepted finish was not appended once",
                )
                count_history.append(_shared_counts(shared))

            with checks.step(7, "peer verification + SOLVED"):
                verified = _run_client(
                    checks,
                    url,
                    "verify",
                    "--contestant-id",
                    "claude",
                    "--proposal-id",
                    proposal_id,
                    "--agree",
                    "--reason",
                    "Checked the algorithm, extraction argument, and all evidence rows.",
                )
                checks.check(verified["solved"] is True, "peer agreement did not solve")
                final_health = _run_client(checks, url, "health")
                checks.check(final_health["solved"] is True, "health did not report solved")
                checks.check(
                    (state_root / "ising_lift" / "SOLVED").is_file(),
                    "SOLVED marker is missing",
                )
                count_history.append(_shared_counts(shared))

            with checks.step(8, "state layout audit"):
                for stem in ("findings", "breakthroughs", "finish", "oracle_log"):
                    for suffix in ("jsonl", "md"):
                        path = shared / f"{stem}.{suffix}"
                        checks.check(path.is_file(), f"missing shared file {path.name}")
                    for record in _read_jsonl(shared / f"{stem}.jsonl"):
                        checks.check(isinstance(record, dict), f"bad {stem} record")

                for earlier, later in zip(count_history, count_history[1:]):
                    for name, prior_count in earlier.items():
                        checks.check(
                            later[name] >= prior_count,
                            f"append-only line count decreased for {name}",
                        )

                for contestant in ("claude", "codex"):
                    contestant_dir = (
                        state_root / "ising_lift" / "contestants" / contestant
                    )
                    for name in ("journal.md", "direction.md", "turns.jsonl"):
                        checks.check(
                            (contestant_dir / name).is_file(),
                            f"missing {contestant}/{name}",
                        )
                    checks.check(
                        len(_read_jsonl(contestant_dir / "turns.jsonl")) >= 1,
                        f"missing {contestant} turn record",
                    )
                round_value = int(
                    (state_root / "ising_lift" / "round_counter.txt").read_text()
                )
                checks.check(round_value >= 2, "round counter did not advance")
                checks.check(
                    len(_read_jsonl(shared / "breakthroughs.jsonl")) >= 2,
                    "promotion records are incomplete",
                )
                checks.check(
                    len(_read_jsonl(shared / "finish.jsonl")) == 2,
                    "finish proposal/verification record count is wrong",
                )
        finally:
            global CURRENT_STEP
            checks.current_step = "9 (teardown)"
            CURRENT_STEP = checks.current_step
            if daemon.poll() is None:
                daemon.terminate()
                try:
                    daemon.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    daemon.kill()
                    daemon.wait(timeout=5)
            checks.check(daemon.poll() is not None, "daemon did not terminate")

    with checks.step(9, "teardown"):
        checks.check(not state_root.exists(), "throwaway state root was not removed")

    print(f"ORACLE-SMOKE: ALL OK ({checks.total} assertions)", flush=True)
    return 0


def main() -> int:
    try:
        return run_smoke()
    except Exception as exc:  # noqa: BLE001
        print(
            f"ORACLE-SMOKE: FAIL {CURRENT_STEP}: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
