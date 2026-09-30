#!/usr/bin/env python3
"""Runner: drives the contestant seats from the command line, one bounded
round at a time, and restarts what stops.

Why this exists. A contestant that lives in an IDE session idles when its
turn ends, dies silently on a rate limit or a lost context, and nothing but a
human restarts it. The runner keeps the clock outside the agents: each round
is one headless invocation of the agent's own CLI with a short kickoff
("do exactly one tick, record it, stop"); the agent's memory between rounds is
the daemon's shared state, not its context.

Liveness is measured at the daemon's record, never at the agent's word: a
round counts only if a new entry appeared in that seat's `turns.jsonl`.

Drivers (per seat, `--seat <id>=<driver>`):
  claude            `claude -p ...` with a fixed session id, resumed each round
  codex             `codex exec ...`, then `codex exec resume <thread> ...`
  command:<tmpl>    any shell command; placeholders {prompt} {prompt_file}
                    {seat} {url} {round} {session}

Outcomes of a round and what the runner does:
  ok       a new turn was recorded          -> pause, next round
  idle     the agent ran but recorded none  -> strike; after --max-idle-rounds, fresh session
  limit    provider rate/usage limit        -> wait 5, 10, 20, 40, 60 min, retry
  crash    nonzero exit / other failure     -> wait 1, 2, 4, 8, 15 min; fresh session after 5
  timeout  round exceeded --round-timeout   -> as crash
  auth     credentials problem              -> seat stops; the operator must act

The runner writes only under state/<challenge>/runner/ (its gauges, prompts
and logs). It never touches the daemon's files. It exits 0 when SOLVED
appears, 2 when every seat has stopped for the operator.
"""
from __future__ import annotations

try:  # interpreter floor first, before any module that needs it
    from . import require_python  # noqa: F401
except ImportError:  # direct script execution
    import require_python  # noqa: F401

import argparse
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from .utils import paths
except ImportError:  # direct script execution
    from utils import paths

ROOT = Path(__file__).resolve().parent.parent

LIMIT_RE = re.compile(
    r"rate.?limit|usage limit|too many requests|\b429\b|quota|overloaded|"
    r"capacity|hit your (session |usage |weekly )?limit|limit reached|resets? (at|in) |"
    r"insufficient_quota|retry after",
    re.IGNORECASE,
)
AUTH_RE = re.compile(
    r"not logged in|please (run )?(`)?(codex|claude) login|could not resolve authentication|"
    r"authentication (failed|error|required)|unauthori[sz]ed|invalid (api )?key|\b401\b|"
    r"credit balance|billing (issue|problem)",
    re.IGNORECASE,
)
LIMIT_BACKOFF = [300, 600, 1200, 2400, 3600]
CRASH_BACKOFF = [60, 120, 240, 480, 900]
FRESH_SESSION_AFTER_CRASHES = 5


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


# ---------------------------------------------------------------- prompts

def build_prompt(seat: str, url: str, challenge: str, round_no: int, first: bool) -> str:
    if first:
        return (
            f"You are contestant `{seat}` in the Black Box Arena challenge `{challenge}`. "
            f"CONTESTANT_ID={seat} and ORACLE_DAEMON_URL={url} are set in your environment. "
            "Read CONTESTANT.md and follow it exactly. Do exactly ONE tick now: snapshot, think, "
            "predict then query (or one kernel job), post what you found, and record the turn with "
            "`python3 arena/client.py turn`. Then stop and reply with one line summarising the tick. "
            "Use only arena/client.py. Never edit anything under state/, never touch the Lean "
            f"workspace, and do not open challenges/{challenge}/oracle.py. "
            f"This is round {round_no} of your session."
        )
    return (
        f"Round {round_no}. Same rules: re-read CONTESTANT.md if you need to, do exactly one tick, "
        "record it with `python3 arena/client.py turn`, then stop with a one-line summary."
    )


# ---------------------------------------------------------------- drivers

class Driver:
    name = "driver"

    def argv(self, seat: "Seat", prompt: str, prompt_file: Path) -> list[str] | str:
        raise NotImplementedError

    def after_run(self, seat: "Seat", output: str) -> None:
        """Let the driver harvest a session/thread id from the output."""


class ClaudeDriver(Driver):
    name = "claude"

    def __init__(self, permission_mode: str = "auto", extra: list[str] | None = None):
        self.permission_mode = permission_mode
        self.extra = extra or []

    def argv(self, seat: "Seat", prompt: str, prompt_file: Path) -> list[str]:
        if not seat.session:
            seat.session = str(uuid.uuid4())
            seat.session_is_new = True
        base = ["claude", "-p"]
        if seat.session_is_new:
            base += [prompt, "--session-id", seat.session]
        else:
            base += ["--resume", seat.session, prompt]
        return base + ["--output-format", "json", "--permission-mode", self.permission_mode, *self.extra]

    def after_run(self, seat: "Seat", output: str) -> None:
        # `--output-format json` ends with one JSON object carrying session_id.
        for line in reversed(output.strip().splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                sid = payload.get("session_id")
                if isinstance(sid, str) and sid:
                    seat.session = sid
                break
        seat.session_is_new = False


class CodexDriver(Driver):
    name = "codex"

    def __init__(self, sandbox: str = "workspace-write", extra: list[str] | None = None):
        self.sandbox = sandbox
        self.extra = extra or []

    def argv(self, seat: "Seat", prompt: str, prompt_file: Path) -> list[str]:
        last = str(seat.runner_dir / f"{seat.id}.last.txt")
        if seat.session:
            return ["codex", "exec", "resume", seat.session, "--skip-git-repo-check", "--json",
                    "-o", last, *self.extra, prompt]
        return ["codex", "exec", "-C", str(ROOT), "-s", self.sandbox, "--skip-git-repo-check",
                "--json", "-o", last, *self.extra, prompt]

    def after_run(self, seat: "Seat", output: str) -> None:
        tid = extract_codex_thread_id(output)
        if tid:
            seat.session = tid
        seat.session_is_new = False


def extract_codex_thread_id(output: str) -> str | None:
    """`codex exec --json` emits one event per line; the first is thread.started."""
    for line in output.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        tid = event.get("thread_id") or (event.get("thread") or {}).get("id")
        if isinstance(tid, str) and tid:
            return tid
    return None


class CommandDriver(Driver):
    name = "command"

    def __init__(self, template: str):
        self.template = template

    def argv(self, seat: "Seat", prompt: str, prompt_file: Path) -> str:
        return self.template.format(
            prompt=shlex.quote(prompt),
            prompt_file=shlex.quote(str(prompt_file)),
            seat=shlex.quote(seat.id),
            url=shlex.quote(seat.url),
            round=seat.round,
            session=shlex.quote(seat.session or ""),
        )


def make_driver(spec: str, args: argparse.Namespace) -> Driver:
    if spec == "claude":
        return ClaudeDriver(args.claude_permission_mode, shlex.split(args.claude_args))
    if spec == "codex":
        return CodexDriver(args.codex_sandbox, shlex.split(args.codex_args))
    if spec.startswith("command:"):
        return CommandDriver(spec[len("command:"):])
    raise SystemExit(f"runner: unknown driver {spec!r}; use claude, codex or command:<template>")


# ---------------------------------------------------------------- outcomes

def classify_outcome(returncode: int | None, output: str, new_turn: bool, timed_out: bool) -> str:
    if timed_out:
        return "timeout"
    tail = output[-20000:]
    if AUTH_RE.search(tail):
        return "auth"
    if LIMIT_RE.search(tail) and not new_turn:
        return "limit"
    if returncode not in (0, None):
        return "crash"
    return "ok" if new_turn else "idle"


def backoff_seconds(outcome: str, consecutive: int) -> int:
    table = LIMIT_BACKOFF if outcome == "limit" else CRASH_BACKOFF
    return table[min(max(consecutive, 1), len(table)) - 1]


# ---------------------------------------------------------------- seats

class Seat:
    def __init__(self, seat_id: str, driver: Driver, challenge: str, url: str, runner_dir: Path):
        self.id = seat_id
        self.driver = driver
        self.challenge = challenge
        self.url = url
        self.runner_dir = runner_dir
        self.round = 0
        self.session: str | None = None
        self.session_is_new = True
        self.consecutive_idle = 0
        self.consecutive_failures = 0
        self.consecutive_limits = 0
        self.status = "starting"
        self.last_outcome: str | None = None
        self.last_error: str = ""
        self.last_round_start: str | None = None
        self.last_round_end: str | None = None
        self.next_attempt_at: str | None = None
        self.stopped_for_operator = False

    # -- the daemon's record, read-only
    def turns_file(self) -> Path:
        return paths.contestant_state_dir(self.challenge, self.id) / "turns.jsonl"

    def last_turn_ts(self) -> str | None:
        f = self.turns_file()
        if not f.exists():
            return None
        last = None
        with f.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    last = line
        if not last:
            return None
        try:
            return json.loads(last).get("ts")
        except json.JSONDecodeError:
            return None

    def fresh_session(self, why: str) -> None:
        log(f"[{self.id}] fresh session: {why}")
        self.session = None
        self.session_is_new = True
        self.consecutive_idle = 0
        self.consecutive_failures = 0

    def gauge(self) -> dict[str, Any]:
        return {
            "seat": self.id,
            "driver": self.driver.name,
            "status": self.status,
            "round": self.round,
            "session": self.session,
            "last_round_start": self.last_round_start,
            "last_round_end": self.last_round_end,
            "last_turn_ts": self.last_turn_ts(),
            "last_outcome": self.last_outcome,
            "consecutive_idle": self.consecutive_idle,
            "consecutive_failures": self.consecutive_failures,
            "consecutive_limits": self.consecutive_limits,
            "next_attempt_at": self.next_attempt_at,
            "last_error": self.last_error[-600:],
            "updated": _now_iso(),
        }

    def write_gauge(self) -> None:
        self.runner_dir.mkdir(parents=True, exist_ok=True)
        target = self.runner_dir / f"{self.id}.json"
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.gauge(), indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, target)


_log_lock = threading.Lock()


def log(message: str) -> None:
    with _log_lock:
        print(f"{_now_iso()} runner {message}", flush=True)


def solved_path(challenge: str) -> Path:
    return paths.state_dir(challenge) / "SOLVED"


def run_round(seat: Seat, args: argparse.Namespace, stop: threading.Event) -> str:
    seat.round += 1
    first = seat.session is None or seat.session_is_new
    prompt = build_prompt(seat.id, seat.url, seat.challenge, seat.round, first)
    seat.runner_dir.mkdir(parents=True, exist_ok=True)
    prompt_file = seat.runner_dir / f"{seat.id}.prompt.txt"
    prompt_file.write_text(prompt + "\n", encoding="utf-8")
    command = seat.driver.argv(seat, prompt, prompt_file)

    env = os.environ.copy()
    env["ORACLE_DAEMON_URL"] = seat.url
    env["CONTESTANT_ID"] = seat.id
    before = seat.last_turn_ts()
    seat.last_round_start = _now_iso()
    seat.status = "in_round"
    seat.write_gauge()
    shown = command if isinstance(command, str) else " ".join(shlex.quote(c) for c in command)
    log(f"[{seat.id}] round {seat.round} start: {shown[:200]}")

    timed_out = False
    output = ""
    returncode: int | None = None
    try:
        proc = subprocess.Popen(
            command,
            shell=isinstance(command, str),
            cwd=ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except FileNotFoundError as exc:
        output = f"runner: cannot start {shown[:80]}: {exc}"
        returncode = 127
    else:
        try:
            output, _ = proc.communicate(timeout=args.round_timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                output, _ = proc.communicate(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                output, _ = proc.communicate()
        returncode = proc.returncode
        seat.driver.after_run(seat, output or "")

    output = output or ""
    after = seat.last_turn_ts()
    new_turn = after is not None and after != before and (before is None or after > before)
    outcome = classify_outcome(returncode, output, new_turn, timed_out)
    seat.last_round_end = _now_iso()
    seat.last_outcome = outcome
    seat.last_error = "" if outcome == "ok" else output[-600:]

    with (seat.runner_dir / f"{seat.id}.log").open("a", encoding="utf-8") as fh:
        fh.write(f"\n===== round {seat.round} {seat.last_round_start} -> {seat.last_round_end} "
                 f"exit={returncode} outcome={outcome} new_turn={new_turn}\n")
        fh.write(output[-200000:])
        if not output.endswith("\n"):
            fh.write("\n")
    log(f"[{seat.id}] round {seat.round} {outcome} (exit={returncode}, new_turn={new_turn}, "
        f"{len(output)} bytes)")
    return outcome


def seat_loop(seat: Seat, args: argparse.Namespace, stop: threading.Event) -> None:
    while not stop.is_set():
        if solved_path(seat.challenge).exists():
            seat.status = "solved"
            seat.write_gauge()
            log(f"[{seat.id}] SOLVED present; seat done")
            return
        if args.rounds and seat.round >= args.rounds:
            seat.status = "rounds_exhausted"
            seat.write_gauge()
            return

        outcome = run_round(seat, args, stop)
        wait = args.pause
        if outcome == "ok":
            seat.consecutive_idle = seat.consecutive_failures = seat.consecutive_limits = 0
            seat.status = "alive"
        elif outcome == "idle":
            seat.consecutive_idle += 1
            seat.status = "idle"
            if seat.consecutive_idle >= args.max_idle_rounds:
                seat.fresh_session(f"{seat.consecutive_idle} rounds without a recorded turn")
        elif outcome == "limit":
            seat.consecutive_limits += 1
            wait = backoff_seconds("limit", seat.consecutive_limits)
            seat.status = "waiting_on_limit"
        elif outcome == "auth":
            seat.status = "needs_operator"
            seat.stopped_for_operator = True
            seat.write_gauge()
            log(f"[{seat.id}] STOPPED: credentials problem; fix it and restart the runner. "
                f"Last output: {seat.last_error[-300:]!r}")
            return
        else:  # crash, timeout
            seat.consecutive_failures += 1
            wait = backoff_seconds("crash", seat.consecutive_failures)
            seat.status = "retrying"
            if seat.consecutive_failures >= FRESH_SESSION_AFTER_CRASHES:
                seat.fresh_session(f"{seat.consecutive_failures} consecutive failures")

        seat.next_attempt_at = datetime.fromtimestamp(time.time() + wait, timezone.utc).isoformat(
            timespec="seconds").replace("+00:00", "Z")
        seat.write_gauge()
        if stop.wait(wait):
            return
        seat.next_attempt_at = None


# ---------------------------------------------------------------- main

def parse_seats(specs: list[str], args: argparse.Namespace, challenge: str, url: str,
                runner_dir: Path) -> list[Seat]:
    seats = []
    for spec in specs:
        if "=" not in spec:
            raise SystemExit(f"runner: --seat needs <id>=<driver>, got {spec!r}")
        seat_id, driver_spec = spec.split("=", 1)
        seats.append(Seat(seat_id.strip(), make_driver(driver_spec.strip(), args), challenge, url, runner_dir))
    return seats


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--challenge", required=True)
    ap.add_argument("--daemon", default=os.environ.get("ORACLE_DAEMON_URL", "http://127.0.0.1:8787"))
    ap.add_argument("--seat", action="append", default=None,
                    help="<id>=<claude|codex|command:TEMPLATE>; default: claude=claude codex=codex")
    ap.add_argument("--rounds", type=int, default=0, help="rounds per seat, 0 = until SOLVED")
    ap.add_argument("--once", action="store_true", help="one round per seat, then exit")
    ap.add_argument("--pause", type=float, default=10.0, help="seconds between rounds")
    ap.add_argument("--round-timeout", type=int, default=1800)
    ap.add_argument("--max-idle-rounds", type=int, default=3)
    ap.add_argument("--claude-permission-mode", default="auto")
    ap.add_argument("--claude-args", default="", help="extra arguments for `claude -p`")
    ap.add_argument("--codex-sandbox", default="workspace-write")
    ap.add_argument("--codex-args", default="", help="extra arguments for `codex exec`")
    ap.add_argument("--dry-run", action="store_true", help="print each seat's first command and exit")
    args = ap.parse_args(argv)
    if args.once:
        args.rounds = 1

    runner_dir = paths.state_dir(args.challenge) / "runner"
    seats = parse_seats(args.seat or ["claude=claude", "codex=codex"], args, args.challenge,
                        args.daemon, runner_dir)

    if args.dry_run:
        for seat in seats:
            command = seat.driver.argv(seat, build_prompt(seat.id, seat.url, seat.challenge, 1, True),
                                       runner_dir / f"{seat.id}.prompt.txt")
            print(command if isinstance(command, str) else " ".join(shlex.quote(c) for c in command))
        return 0

    for seat in seats:
        if isinstance(seat.driver, (ClaudeDriver, CodexDriver)) and shutil.which(seat.driver.name) is None:
            raise SystemExit(f"runner: `{seat.driver.name}` is not on PATH for seat {seat.id}")

    stop = threading.Event()

    def _stop(signum, frame):  # noqa: ARG001
        log(f"signal {signum}: stopping")
        stop.set()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    log(f"challenge={args.challenge} daemon={args.daemon} seats="
        + ", ".join(f"{s.id}({s.driver.name})" for s in seats))
    threads = [threading.Thread(target=seat_loop, args=(s, args, stop), name=s.id, daemon=True) for s in seats]
    for t in threads:
        t.start()
    while any(t.is_alive() for t in threads):
        for t in threads:
            t.join(timeout=1.0)
    for seat in seats:
        seat.write_gauge()
    if solved_path(args.challenge).exists():
        log("SOLVED: contest over")
        return 0
    if seats and all(s.stopped_for_operator for s in seats):
        log("every seat stopped for the operator")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
