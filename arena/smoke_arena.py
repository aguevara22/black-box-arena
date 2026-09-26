#!/usr/bin/env python3
"""Deterministic end-to-end smoke test for the Lean-kernel arena."""
from __future__ import annotations

try:  # interpreter floor first, before any module that needs it
    from . import require_python  # noqa: F401
except ImportError:  # direct script execution
    import require_python  # noqa: F401

import contextlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Iterator

try:
    from .lean_workspace import ensure_toolchain
except ImportError:  # direct script execution
    from lean_workspace import ensure_toolchain


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
            f"ARENA-SMOKE: step {number} {label} "
            f"OK ({self.total - before} assertions)",
            flush=True,
        )


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    records: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(json.loads(line))
    return records


def _line_count(path: Path) -> int:
    if not path.exists():
        return 0
    return len(path.read_text(encoding="utf-8").splitlines())


def _shared_counts(shared: Path) -> dict[str, int]:
    return {
        f"{stem}.jsonl": _line_count(shared / f"{stem}.jsonl")
        for stem in ("findings", "breakthroughs", "finish", "jobs", "dag")
    }


def _run_client(
    checks: Checks,
    url: str,
    *args: str,
    expect_ok: bool = True,
    stdin_text: str | None = None,
    timeout: int = 180,
) -> dict:
    proc = subprocess.run(
        [sys.executable, str(CLIENT), *args, "--daemon", url],
        cwd=ROOT,
        text=True,
        input=stdin_text,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise SmokeFailure(
            f"client returned invalid JSON for {args}: {exc}; "
            f"stdout={proc.stdout!r}; stderr={proc.stderr!r}"
        ) from exc
    checks.check(
        bool(payload.get("ok")) is expect_ok,
        f"unexpected client payload for {args}: {payload}",
    )
    checks.check(
        (proc.returncode == 0) is expect_ok,
        f"unexpected client exit {proc.returncode} for {args}: {payload}; "
        f"stderr={proc.stderr!r}",
    )
    return payload


def _wait_for_health(daemon: subprocess.Popen[str], url: str) -> None:
    deadline = time.monotonic() + 120
    last_detail = "no probe attempted"
    while time.monotonic() < deadline:
        if daemon.poll() is not None:
            stdout, stderr = daemon.communicate()
            raise SmokeFailure(
                f"daemon exited during startup with {daemon.returncode}; "
                f"stdout={stdout[-4000:]!r}; stderr={stderr[-4000:]!r}"
            )
        try:
            probe = subprocess.run(
                [sys.executable, str(CLIENT), "health", "--daemon", url],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=5,
            )
        except subprocess.TimeoutExpired:
            last_detail = "health client timed out"
        else:
            last_detail = (
                f"exit={probe.returncode}, stdout={probe.stdout!r}, "
                f"stderr={probe.stderr!r}"
            )
            if probe.returncode == 0:
                try:
                    payload = json.loads(probe.stdout)
                except json.JSONDecodeError:
                    pass
                else:
                    if payload.get("ok") is True:
                        return
        time.sleep(0.25)
    raise SmokeFailure(
        f"daemon did not become healthy within 120s; last probe: {last_detail}"
    )


def _wait_for_record(
    path: Path,
    predicate: Callable[[dict], bool],
    *,
    timeout: float = 5.0,
) -> list[dict]:
    deadline = time.monotonic() + timeout
    records: list[dict] = []
    while time.monotonic() < deadline:
        records = _read_jsonl(path)
        if any(predicate(record) for record in records):
            return records
        time.sleep(0.05)
    return records


def _post_json_status(url: str, path: str, body: dict) -> tuple[int, dict]:
    request = urllib.request.Request(
        f"{url}{path}",
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _check_hygiene_failure(
    checks: Checks,
    url: str,
    *,
    source: str,
    decl: str,
    expected_text: str,
) -> None:
    reply = _run_client(
        checks,
        url,
        "check",
        "--contestant-id",
        "claude",
        "--mode",
        "proof",
        "--decl",
        decl,
        "--file",
        "-",
        stdin_text=source,
    )
    result = reply.get("result") or {}
    violations = result.get("violations")
    checks.check(reply.get("status") == "done", f"hygiene job was not immediate: {reply}")
    checks.check(
        result.get("failure_kind") == "hygiene_source",
        f"wrong hygiene failure for {decl}: {reply}",
    )
    checks.check(isinstance(violations, list) and bool(violations), f"missing violations: {reply}")
    checks.check(
        any(expected_text.lower() in str(item).lower() for item in violations or []),
        f"violations for {decl} did not mention {expected_text!r}: {violations}",
    )


def _stop_daemon(checks: Checks, daemon: subprocess.Popen[str]) -> None:
    global CURRENT_STEP
    previous_step = checks.current_step
    checks.current_step = "teardown"
    CURRENT_STEP = checks.current_step
    if daemon.poll() is None:
        daemon.terminate()
    try:
        stdout, stderr = daemon.communicate(timeout=15)
    except subprocess.TimeoutExpired as exc:
        daemon.kill()
        stdout, stderr = daemon.communicate(timeout=5)
        raise SmokeFailure(
            "daemon ignored SIGTERM; "
            f"stdout={stdout[-4000:]!r}; stderr={stderr[-4000:]!r}"
        ) from exc
    checks.check(daemon.poll() is not None, "daemon did not exit after SIGTERM")
    checks.check(
        daemon.returncode == 0,
        f"daemon exited with {daemon.returncode}; "
        f"stdout={stdout[-4000:]!r}; stderr={stderr[-4000:]!r}",
    )
    checks.current_step = previous_step
    CURRENT_STEP = previous_step


def run_smoke() -> int:
    checks = Checks()
    port = _free_port()
    url = f"http://127.0.0.1:{port}"
    count_history: list[tuple[int, dict[str, int]]] = []
    daemon: subprocess.Popen[str] | None = None

    with (
        tempfile.TemporaryDirectory(prefix="arena-smoke-state-") as state_temporary,
        tempfile.TemporaryDirectory(prefix="arena-smoke-lean-") as lean_temporary,
    ):
        state_root = Path(state_temporary)
        lean_root = Path(lean_temporary)
        # Lake creates this deterministic empty manifest during the first
        # build. Seed it so the daemon's pre-build and post-build frozen hash
        # sets are identical even in a brand-new temporary workspace.
        lean_challenge_root = lean_root / "smoke_min"
        lean_challenge_root.mkdir(parents=True)
        (lean_challenge_root / "lake-manifest.json").write_text(
            '{"version": "1.2.0",\n'
            ' "packagesDir": ".lake/packages",\n'
            ' "packages": [],\n'
            ' "name": "smoke_min",\n'
            ' "lakeDir": ".lake",\n'
            ' "fixedToolchain": false}\n',
            encoding="utf-8",
        )
        # First-run trap: elan's `lean` shim would download the pinned toolchain
        # silently and blow the 120 s health deadline below. Fetch it explicitly.
        ensure_toolchain(
            ROOT / "challenges" / "smoke_min",
            log_line=lambda msg: print(f"ARENA-SMOKE: {msg}", flush=True),
        )
        env = os.environ.copy()
        env["ORACLE_STATE_ROOT"] = str(state_root)
        env["ARENA_LEAN_WORKSPACE_ROOT"] = str(lean_root)
        daemon = subprocess.Popen(
            [
                sys.executable,
                str(DAEMON),
                "--challenge",
                "smoke_min",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--lease-ttl-seconds",
                "2",
            ],
            cwd=ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            with checks.step(1, "boot + identity"):
                _wait_for_health(daemon, url)
                health = _run_client(checks, url, "health")
                checks.check(health.get("challenge") == "smoke_min", f"wrong challenge: {health}")
                checks.check(health.get("kernel_enabled") is True, f"kernel is disabled: {health}")
                checks.check("4.33" in str(health.get("lean_version")), f"wrong Lean version: {health}")
                checks.check(
                    str(health.get("toolchain_fingerprint", "")).startswith("tc_"),
                    f"bad toolchain fingerprint: {health}",
                )
                checks.check(
                    health.get("contestants") == ["claude", "codex"],
                    f"wrong contestant identities: {health}",
                )
                problem = _run_client(checks, url, "problem")
                checks.check(
                    "Arena.GoalStatement" in str(problem.get("problem", "")),
                    "problem text omitted Arena.GoalStatement",
                )
                checks.check(state_root != lean_root, "state and Lean workspace roots overlap")
                checks.check(state_root.is_dir() and lean_root.is_dir(), "temporary roots are missing")
                shared = state_root / "smoke_min" / "shared"
            count_history.append((1, _shared_counts(shared)))

            eval_source = "import Defs.Basic\n#eval Arena.double 21\n"
            with checks.step(2, "eval + cache"):
                first_eval = _run_client(
                    checks,
                    url,
                    "check",
                    "--contestant-id",
                    "claude",
                    "--mode",
                    "eval",
                    "--file",
                    "-",
                    "--wait",
                    stdin_text=eval_source,
                )
                first_result = first_eval.get("result") or {}
                checks.check(first_eval.get("status") == "done", f"eval did not finish: {first_eval}")
                checks.check(first_result.get("outcome") == "ok", f"eval failed: {first_eval}")
                infos = first_result.get("infos")
                checks.check(isinstance(infos, list), f"eval omitted info messages: {first_eval}")
                checks.check(
                    any(
                        isinstance(info, dict) and str(info.get("message", "")).strip() == "42"
                        for info in infos or []
                    ),
                    f"eval did not report 42: {infos}",
                )

                jobs_path = shared / "jobs.jsonl"
                before_cache = _read_jsonl(jobs_path)
                second_eval = _run_client(
                    checks,
                    url,
                    "check",
                    "--contestant-id",
                    "claude",
                    "--mode",
                    "eval",
                    "--file",
                    "-",
                    "--wait",
                    stdin_text=eval_source,
                )
                checks.check(second_eval.get("status") == "done", f"cache job not done: {second_eval}")
                checks.check(second_eval.get("cache_hit") is True, f"cache hit missing: {second_eval}")
                second_job_id = second_eval.get("job_id")
                after_cache = _read_jsonl(jobs_path)
                new_events = after_cache[len(before_cache) :]
                checks.check(
                    len(after_cache) == len(before_cache) + 2,
                    f"cache hit appended the wrong number of events: {new_events}",
                )
                checks.check(
                    [record.get("event") for record in new_events]
                    == ["job_submitted", "job_finished"],
                    f"cache-hit lifecycle is wrong: {new_events}",
                )
                checks.check(
                    all(record.get("job_id") == second_job_id for record in new_events),
                    f"cache-hit events name another job: {new_events}",
                )
                checks.check(
                    not any(record.get("event") == "job_started" for record in new_events),
                    f"cache hit started a new Lean build: {new_events}",
                )
            count_history.append((2, _shared_counts(shared)))

            with checks.step(3, "hygiene fast-fails"):
                _check_hygiene_failure(
                    checks,
                    url,
                    source=(
                        "import Defs.Basic\n"
                        "theorem has_hole : Arena.double 1 = 2 := by\n"
                        "  sorry\n"
                    ),
                    decl="has_hole",
                    expected_text="sorry",
                )
                _check_hygiene_failure(
                    checks,
                    url,
                    source=(
                        "import Defs.Basic\n"
                        "set_option maxHeartbeats 4000000 in\n"
                        "theorem too_many : Arena.double 1 = 2 := rfl\n"
                    ),
                    decl="too_many",
                    expected_text="exceeds cap 400000",
                )
                _check_hygiene_failure(
                    checks,
                    url,
                    source=(
                        "import Defs.Basic\n"
                        "axiom fabricated : Arena.double 1 = 2\n"
                    ),
                    decl="fabricated",
                    expected_text="axiom",
                )
                _check_hygiene_failure(
                    checks,
                    url,
                    source=(
                        "import Mathlib.Tactic\n"
                        "theorem outside_allowlist : True := True.intro\n"
                    ),
                    decl="outside_allowlist",
                    expected_text="outside allowlist",
                )
            count_history.append((3, _shared_counts(shared)))

            with checks.step(4, "kernel verdicts"):
                wrong = _run_client(
                    checks,
                    url,
                    "check",
                    "--contestant-id",
                    "claude",
                    "--mode",
                    "proof",
                    "--decl",
                    "wrong",
                    "--file",
                    "-",
                    "--wait",
                    stdin_text=(
                        "import Defs.Basic\n"
                        "theorem wrong : Arena.double 1 = 3 := rfl\n"
                    ),
                )
                wrong_result = wrong.get("result") or {}
                checks.check(wrong.get("status") == "done", f"wrong proof did not finish: {wrong}")
                checks.check(
                    wrong_result.get("failure_kind") == "compile_error",
                    f"wrong proof got the wrong verdict: {wrong}",
                )
                checks.check(
                    isinstance(wrong_result.get("diagnostics"), list)
                    and bool(wrong_result.get("diagnostics")),
                    f"wrong proof has no diagnostics: {wrong}",
                )

                hypothesis = "Arena.double 1 reduces definitionally to 2"
                proved = _run_client(
                    checks,
                    url,
                    "check",
                    "--contestant-id",
                    "claude",
                    "--mode",
                    "proof",
                    "--decl",
                    "double_one",
                    "--statement",
                    "Arena.double 1 = 2",
                    "--predict",
                    "ok",
                    "--hypothesis",
                    hypothesis,
                    "--file",
                    "-",
                    "--wait",
                    stdin_text=(
                        "import Defs.Basic\n"
                        "theorem double_one : Arena.double 1 = 2 := rfl\n"
                    ),
                )
                proved_result = proved.get("result") or {}
                checks.check(proved.get("status") == "done", f"proof did not finish: {proved}")
                checks.check(proved_result.get("outcome") == "ok", f"proof failed: {proved}")
                checks.check(
                    (proved_result.get("axioms") or {}).get("_arena_fidelity") == [],
                    f"fidelity theorem has unexpected axioms: {proved}",
                )
                checks.check(
                    proved_result.get("prediction_correct") is True,
                    f"prediction was not recorded as correct: {proved}",
                )
                proved_job_id = str(proved.get("job_id", ""))
                checks.check(proved_job_id.startswith("J"), f"proof job id is missing: {proved}")

                breakthroughs_path = shared / "breakthroughs.jsonl"
                breakthroughs = _wait_for_record(
                    breakthroughs_path,
                    lambda record: record.get("promoted_via") == "predictive_match"
                    and record.get("text") == hypothesis,
                )
                checks.check(
                    any(
                        record.get("promoted_via") == "predictive_match"
                        and record.get("text") == hypothesis
                        for record in breakthroughs
                    ),
                    "predictive-match breakthrough was not appended",
                )

                cited_text = "The kernel certified the double-one lemma"
                cited = _run_client(
                    checks,
                    url,
                    "breakthrough",
                    "--contestant-id",
                    "codex",
                    "--text",
                    cited_text,
                    "--job-id",
                    proved_job_id,
                )
                checks.check(cited.get("promoted") is True, f"kernel citation was not promoted: {cited}")
                checks.check(
                    str(cited.get("via", "")).startswith("kernel_verdict:"),
                    f"wrong kernel promotion path: {cited}",
                )

                bogus_text = "This candidate cites a job that does not exist"
                bogus = _run_client(
                    checks,
                    url,
                    "breakthrough",
                    "--contestant-id",
                    "codex",
                    "--text",
                    bogus_text,
                    "--job-id",
                    "Jbogus0000000000",
                )
                checks.check(bogus.get("promoted") is False, f"bogus job was promoted: {bogus}")
                findings = _read_jsonl(shared / "findings.jsonl")
                checks.check(
                    any(
                        bogus_text in str(record.get("text", ""))
                        and "unpromoted breakthrough candidate" in str(record.get("text", ""))
                        for record in findings
                    ),
                    "bogus breakthrough candidate was not preserved in findings.jsonl",
                )
            count_history.append((4, _shared_counts(shared)))

            children = [
                {
                    "name": "double_zero",
                    "statement": "Arena.double 0 = 0",
                    "gloss": "the recursion base case",
                },
                {
                    "name": "double_succ",
                    "statement": "∀ n : Nat, Arena.double (n + 1) = Arena.double n + 2",
                    "gloss": "the recursion successor equation",
                },
            ]
            children_json = json.dumps(children, separators=(",", ":"), ensure_ascii=False)
            bad_skeleton = (
                "import Defs.Basic\n"
                "import Goal\n"
                "theorem double_zero : Arena.double 0 = 0 := sorry\n"
                "theorem double_succ : ∀ n : Nat, "
                "Arena.double (n + 1) = Arena.double n + 2 := sorry\n"
                "theorem goal_root : Arena.GoalStatement := by\n"
                "  exact double_zero\n"
            )
            good_skeleton = (
                "import Defs.Basic\n"
                "import Goal\n"
                "theorem double_zero : Arena.double 0 = 0 := sorry\n"
                "theorem double_succ : ∀ n : Nat, "
                "Arena.double (n + 1) = Arena.double n + 2 := sorry\n"
                "theorem goal_root : Arena.GoalStatement := by\n"
                "  intro a b\n"
                "  induction b with\n"
                "  | zero => simp [double_zero]\n"
                "  | succ n ih =>\n"
                "      rw [Nat.add_succ, double_succ, double_succ, ih]\n"
                "      omega\n"
            )

            with checks.step(5, "decompositions"):
                initial_dag = _run_client(checks, url, "dag")
                root_id = str(initial_dag.get("root", ""))
                initial_nodes = initial_dag.get("nodes") or []
                checks.check(root_id.startswith("n_"), f"root node id is missing: {initial_dag}")
                checks.check(
                    len(initial_nodes) == 1
                    and initial_nodes[0].get("node_id") == root_id
                    and initial_nodes[0].get("name") == "goal_root",
                    f"fresh DAG does not contain exactly goal_root: {initial_dag}",
                )

                bad = _run_client(
                    checks,
                    url,
                    "decompose",
                    "--contestant-id",
                    "claude",
                    "--node",
                    root_id,
                    "--children-json",
                    children_json,
                    "--file",
                    "-",
                    "--wait",
                    stdin_text=bad_skeleton,
                )
                bad_result = bad.get("result") or {}
                bad_decomp_id = str(bad.get("decomp_id", ""))
                checks.check(bad.get("status") == "done", f"bad skeleton did not finish: {bad}")
                checks.check(
                    bad_result.get("outcome") == "failed"
                    and bad_result.get("failure_kind") == "compile_error",
                    f"bad skeleton got the wrong verdict: {bad}",
                )
                checks.check(
                    bad_decomp_id.startswith("d_"),
                    f"bad skeleton omitted its decomposition id: {bad}",
                )
                bad_frontier = _run_client(checks, url, "dag", "--frontier")
                checks.check(
                    [node.get("node_id") for node in bad_frontier.get("frontier") or []]
                    == [root_id],
                    f"failed decomposition changed the frontier: {bad_frontier}",
                )
                dag_path = shared / "dag.jsonl"
                after_bad_events = _read_jsonl(dag_path)
                checks.check(
                    any(
                        record.get("event") == "decomposition_proposed"
                        and record.get("decomp_id") == bad_decomp_id
                        for record in after_bad_events
                    ),
                    f"bad decomposition proposal was not logged: {after_bad_events}",
                )
                checks.check(
                    not any(
                        record.get("event") == "decomposition_accepted"
                        and record.get("decomp_id") == bad_decomp_id
                        for record in after_bad_events
                    ),
                    f"failed decomposition was accepted: {after_bad_events}",
                )

                good = _run_client(
                    checks,
                    url,
                    "decompose",
                    "--contestant-id",
                    "claude",
                    "--node",
                    root_id,
                    "--children-json",
                    children_json,
                    "--file",
                    "-",
                    "--wait",
                    stdin_text=good_skeleton,
                )
                good_result = good.get("result") or {}
                good_decomp_id = str(good.get("decomp_id", ""))
                checks.check(good.get("status") == "done", f"good skeleton did not finish: {good}")
                checks.check(good_result.get("outcome") == "ok", f"good skeleton failed: {good}")
                good_job_id = good.get("job_id")
                accepted_events = _read_jsonl(dag_path)
                checks.check(
                    any(
                        record.get("event") == "decomposition_accepted"
                        and record.get("decomp_id") == good_decomp_id
                        and record.get("checked_by_job") == good_job_id
                        for record in accepted_events
                    ),
                    f"good decomposition was not accepted: {accepted_events}",
                )

                decomposed_dag = _run_client(checks, url, "dag")
                nodes_by_name = {
                    node.get("name"): node for node in decomposed_dag.get("nodes") or []
                }
                checks.check(
                    {"goal_root", "double_zero", "double_succ"} <= set(nodes_by_name),
                    f"accepted decomposition omitted nodes: {decomposed_dag}",
                )
                double_zero_id = str(nodes_by_name["double_zero"].get("node_id", ""))
                double_succ_id = str(nodes_by_name["double_succ"].get("node_id", ""))
                root_node = nodes_by_name["goal_root"]
                checks.check(root_node.get("status") == "sketch", f"root is not a sketch: {root_node}")
                checks.check(
                    root_node.get("children") == [double_zero_id, double_succ_id],
                    f"root children are wrong: {root_node}",
                )
                good_frontier = _run_client(checks, url, "dag", "--frontier")
                frontier_names = {
                    node.get("name") for node in good_frontier.get("frontier") or []
                }
                checks.check(
                    {"double_zero", "double_succ"} <= frontier_names,
                    f"child nodes are absent from the frontier: {good_frontier}",
                )
            count_history.append((5, _shared_counts(shared)))

            with checks.step(6, "leases"):
                claude_claim = _run_client(
                    checks,
                    url,
                    "claim",
                    "--contestant-id",
                    "claude",
                    "--node",
                    double_zero_id,
                )
                claude_lease_id = str(claude_claim.get("lease_id", ""))
                checks.check(
                    claude_lease_id.startswith("L_"),
                    f"claude claim omitted a lease id: {claude_claim}",
                )
                blocked_claim = _run_client(
                    checks,
                    url,
                    "claim",
                    "--contestant-id",
                    "codex",
                    "--node",
                    double_zero_id,
                    expect_ok=False,
                )
                checks.check(
                    "leased by claude" in str(blocked_claim.get("error", "")).lower(),
                    f"foreign claim had the wrong error: {blocked_claim}",
                )

                time.sleep(3)
                codex_claim = _run_client(
                    checks,
                    url,
                    "claim",
                    "--contestant-id",
                    "codex",
                    "--node",
                    double_zero_id,
                )
                codex_lease_id = str(codex_claim.get("lease_id", ""))
                checks.check(
                    codex_lease_id.startswith("L_") and codex_lease_id != claude_lease_id,
                    f"codex did not receive a fresh lease after expiry: {codex_claim}",
                )
                lease_events = _read_jsonl(dag_path)
                expired_positions = [
                    index
                    for index, record in enumerate(lease_events)
                    if record.get("event") == "lease_released"
                    and record.get("node_id") == double_zero_id
                    and record.get("lease_id") == claude_lease_id
                    and record.get("reason") == "expired"
                ]
                codex_claim_positions = [
                    index
                    for index, record in enumerate(lease_events)
                    if record.get("event") == "lease_claimed"
                    and record.get("node_id") == double_zero_id
                    and record.get("contestant_id") == "codex"
                    and record.get("lease_id") == codex_lease_id
                ]
                checks.check(bool(expired_positions), f"expired release was not logged: {lease_events}")
                checks.check(
                    bool(codex_claim_positions)
                    and expired_positions[-1] < codex_claim_positions[-1],
                    f"expiry was not logged before codex's claim: {lease_events}",
                )

                _run_client(
                    checks,
                    url,
                    "release",
                    "--contestant-id",
                    "codex",
                    "--node",
                    double_zero_id,
                )
                claude_reclaim = _run_client(
                    checks,
                    url,
                    "claim",
                    "--contestant-id",
                    "claude",
                    "--node",
                    double_zero_id,
                )
                checks.check(
                    str(claude_reclaim.get("lease_id", "")).startswith("L_"),
                    f"claude could not reclaim the released node: {claude_reclaim}",
                )
            count_history.append((6, _shared_counts(shared)))

            with checks.step(7, "leaf proofs + predictive"):
                leaf_hypothesis = "Arena.double zero closes by definitional reduction"
                breakthroughs_before = _line_count(shared / "breakthroughs.jsonl")
                zero_proof = _run_client(
                    checks,
                    url,
                    "check",
                    "--contestant-id",
                    "claude",
                    "--mode",
                    "proof",
                    "--node",
                    double_zero_id,
                    "--predict",
                    "ok",
                    "--hypothesis",
                    leaf_hypothesis,
                    "--file",
                    "-",
                    "--wait",
                    stdin_text=(
                        "import Defs.Basic\n"
                        "theorem double_zero : Arena.double 0 = 0 := rfl\n"
                    ),
                )
                zero_result = zero_proof.get("result") or {}
                checks.check(zero_result.get("outcome") == "ok", f"double_zero failed: {zero_proof}")
                checks.check(
                    zero_result.get("prediction_correct") is True,
                    f"double_zero prediction was not correct: {zero_proof}",
                )
                leaf_breakthroughs = _wait_for_record(
                    shared / "breakthroughs.jsonl",
                    lambda record: record.get("text") == leaf_hypothesis
                    and record.get("promoted_via") == "predictive_match",
                )
                checks.check(
                    len(leaf_breakthroughs) > breakthroughs_before
                    and any(
                        record.get("text") == leaf_hypothesis
                        and record.get("promoted_via") == "predictive_match"
                        for record in leaf_breakthroughs[breakthroughs_before:]
                    ),
                    "double_zero predictive breakthrough was not gained",
                )

                succ_proof = _run_client(
                    checks,
                    url,
                    "check",
                    "--contestant-id",
                    "codex",
                    "--mode",
                    "proof",
                    "--node",
                    double_succ_id,
                    "--file",
                    "-",
                    "--wait",
                    stdin_text=(
                        "import Defs.Basic\n"
                        "theorem double_succ : ∀ n : Nat, "
                        "Arena.double (n + 1) = Arena.double n + 2 := fun n => rfl\n"
                    ),
                )
                succ_result = succ_proof.get("result") or {}
                checks.check(succ_result.get("outcome") == "ok", f"double_succ failed: {succ_proof}")

                proved_dag = _run_client(checks, url, "dag")
                proved_nodes = {
                    node.get("node_id"): node for node in proved_dag.get("nodes") or []
                }
                checks.check(
                    proved_nodes[double_zero_id].get("status") == "proved"
                    and proved_nodes[double_succ_id].get("status") == "proved",
                    f"leaf statuses are not proved: {proved_dag}",
                )
                proof_dir = state_root / "smoke_min" / "proofs"
                checks.check(
                    (proof_dir / f"{double_zero_id}.lean").is_file(),
                    f"double_zero proof source is missing under {proof_dir}",
                )
                checks.check(
                    (proof_dir / f"{double_succ_id}.lean").is_file(),
                    f"double_succ proof source is missing under {proof_dir}",
                )

                self_accept = _run_client(
                    checks,
                    url,
                    "accept",
                    "--contestant-id",
                    "claude",
                    "--node",
                    double_zero_id,
                    "--reason",
                    "The stored theorem matches the node statement.",
                    expect_ok=False,
                )
                checks.check(
                    "other than its prover" in str(self_accept.get("error", "")),
                    f"self-accept had the wrong error: {self_accept}",
                )
                zero_accepted = _run_client(
                    checks,
                    url,
                    "accept",
                    "--contestant-id",
                    "codex",
                    "--node",
                    double_zero_id,
                    "--reason",
                    "The declaration and node both state the recursion base case.",
                )
                succ_accepted = _run_client(
                    checks,
                    url,
                    "accept",
                    "--contestant-id",
                    "claude",
                    "--node",
                    double_succ_id,
                    "--reason",
                    "The declaration and node both state the successor equation.",
                )
                checks.check(
                    (zero_accepted.get("node") or {}).get("status") == "accepted"
                    and (succ_accepted.get("node") or {}).get("status") == "accepted",
                    f"cross-acceptance did not settle both leaves: {zero_accepted}, {succ_accepted}",
                )
            count_history.append((7, _shared_counts(shared)))

            with checks.step(8, "premature finish"):
                finish_path = shared / "finish.jsonl"
                finish_before = _line_count(finish_path)
                premature = _run_client(
                    checks,
                    url,
                    "finish",
                    "--contestant-id",
                    "claude",
                    "--text",
                    "The leaf lemmas are complete, but the root is not yet proved.",
                    expect_ok=False,
                )
                checks.check(
                    "not complete" in str(premature.get("error", "")).lower(),
                    f"premature finish had the wrong error: {premature}",
                )
                status, rejected = _post_json_status(
                    url,
                    "/finish",
                    {
                        "contestant_id": "claude",
                        "text": "Confirm the incomplete DAG is rejected with HTTP 409.",
                    },
                )
                checks.check(status == 409, f"premature finish returned HTTP {status}: {rejected}")
                checks.check(
                    rejected.get("ok") is False
                    and "not complete" in str(rejected.get("error", "")).lower(),
                    f"HTTP 409 payload did not explain incompleteness: {rejected}",
                )
                checks.check(
                    _line_count(finish_path) == finish_before,
                    "premature finish changed finish.jsonl",
                )
            count_history.append((8, _shared_counts(shared)))

            root_source = (
                "import Defs.Basic\n"
                "import Goal\n"
                "theorem goal_root : Arena.GoalStatement := by\n"
                "  intro a b\n"
                "  induction b with\n"
                "  | zero => simp [double_zero]\n"
                "  | succ n ih =>\n"
                "      rw [Nat.add_succ, double_succ, double_succ, ih]\n"
                "      omega\n"
            )
            with checks.step(9, "root + finish"):
                root_proof = _run_client(
                    checks,
                    url,
                    "check",
                    "--contestant-id",
                    "claude",
                    "--mode",
                    "proof",
                    "--node",
                    root_id,
                    "--file",
                    "-",
                    "--wait",
                    stdin_text=root_source,
                )
                root_result = root_proof.get("result") or {}
                checks.check(root_result.get("outcome") == "ok", f"root proof failed: {root_proof}")
                axiom_map = root_result.get("axioms") or {}
                root_axioms = {
                    str(axiom)
                    for axioms in axiom_map.values()
                    if isinstance(axioms, list)
                    for axiom in axioms
                }
                allowed_axioms = {"propext", "Classical.choice", "Quot.sound"}
                checks.check(
                    isinstance(axiom_map, dict)
                    and bool(axiom_map)
                    and root_axioms <= allowed_axioms,
                    f"root proof used unexpected axioms: {axiom_map}",
                )
                checks.check(
                    root_result.get("proved_modulo", []) == [],
                    f"root proof still depends on stubs: {root_result}",
                )

                finished = _run_client(
                    checks,
                    url,
                    "finish",
                    "--contestant-id",
                    "claude",
                    "--text",
                    "The accepted leaf equations close the induction proof of GoalStatement.",
                    "--wait",
                )
                finish_result = finished.get("result") or {}
                proposal_id = str(finish_result.get("finish_proposal_id", ""))
                finish_job_id = str(finished.get("job_id", ""))
                checks.check(finished.get("status") == "done", f"finish job did not finish: {finished}")
                checks.check(finish_result.get("outcome") == "ok", f"final assembly failed: {finished}")
                checks.check(
                    proposal_id and finish_result.get("fidelity_ok") is True,
                    f"finish result omitted its verified proposal: {finished}",
                )

                self_verify = _run_client(
                    checks,
                    url,
                    "verify",
                    "--contestant-id",
                    "claude",
                    "--proposal-id",
                    proposal_id,
                    "--agree",
                    "--reason",
                    "The final kernel job is green.",
                    expect_ok=False,
                )
                checks.check(
                    "other contestant" in str(self_verify.get("error", "")).lower(),
                    f"self-verification had the wrong error: {self_verify}",
                )
                verified = _run_client(
                    checks,
                    url,
                    "verify",
                    "--contestant-id",
                    "codex",
                    "--proposal-id",
                    proposal_id,
                    "--agree",
                    "--reason",
                    "The frozen statement and mechanically assembled proof agree.",
                )
                checks.check(verified.get("solved") is True, f"peer verify did not solve: {verified}")

                solved_path = state_root / "smoke_min" / "SOLVED"
                checks.check(solved_path.is_file(), f"SOLVED file is missing: {solved_path}")
                solved = json.loads(solved_path.read_text(encoding="utf-8"))
                proposal = solved.get("proposal") or {}
                hygiene_report = proposal.get("hygiene_report") or {}
                checks.check(
                    proposal.get("job_id") == finish_job_id
                    and proposal.get("id") == proposal_id,
                    f"SOLVED references the wrong finish proposal: {solved}",
                )
                checks.check(
                    hygiene_report.get("fidelity_ok") is True,
                    f"SOLVED proposal lacks a successful fidelity report: {solved}",
                )
                final_health = _run_client(checks, url, "health")
                checks.check(final_health.get("solved") is True, f"health is not solved: {final_health}")
            count_history.append((9, _shared_counts(shared)))

            busy_source = (
                "#eval Id.run do\n"
                "  let mut x : Nat := 0\n"
                "  for i in [0:2000000000] do\n"
                "    x := x + i\n"
                "  pure x\n"
            )
            with checks.step(10, "integrity + timeout"):
                expected_files = {
                    "findings.jsonl",
                    "breakthroughs.jsonl",
                    "finish.jsonl",
                    "jobs.jsonl",
                }
                checks.check(
                    all((shared / name).is_file() for name in expected_files),
                    f"shared JSONL layout is incomplete under {shared}",
                )
                checks.check(len(count_history) == 9, "did not capture counts after every prior step")
                for (earlier_step, earlier), (later_step, later) in zip(
                    count_history, count_history[1:]
                ):
                    for name, prior_count in earlier.items():
                        checks.check(
                            later[name] >= prior_count,
                            f"{name} decreased from step {earlier_step} to {later_step}: "
                            f"{prior_count} -> {later[name]}",
                        )

                timed_out = _run_client(
                    checks,
                    url,
                    "check",
                    "--contestant-id",
                    "claude",
                    "--mode",
                    "eval",
                    "--timeout",
                    "5",
                    "--file",
                    "-",
                    "--wait",
                    stdin_text=busy_source,
                    timeout=45,
                )
                timeout_result = timed_out.get("result") or {}
                checks.check(timed_out.get("status") == "done", f"timeout job not done: {timed_out}")
                checks.check(
                    timeout_result.get("failure_kind") == "timeout",
                    f"busy loop got the wrong verdict: {timed_out}",
                )
                post_timeout_health = _run_client(checks, url, "health")
                checks.check(
                    post_timeout_health.get("challenge") == "smoke_min"
                    and post_timeout_health.get("kernel_enabled") is True,
                    f"daemon unhealthy after timeout: {post_timeout_health}",
                )

                pgrep = subprocess.run(
                    ["pgrep", "-f", r"[l]ean.*Candidates/J"],
                    cwd=ROOT,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=5,
                )
                checks.check(
                    pgrep.returncode in (0, 1),
                    f"pgrep failed with {pgrep.returncode}: {pgrep.stderr!r}",
                )
                checks.check(
                    pgrep.returncode == 1 and not pgrep.stdout.strip(),
                    f"Lean candidate process survived timeout: {pgrep.stdout!r}",
                )

                step_ten_counts = _shared_counts(shared)
                for name, prior_count in count_history[-1][1].items():
                    checks.check(
                        step_ten_counts[name] >= prior_count,
                        f"{name} decreased during step 10: "
                        f"{prior_count} -> {step_ten_counts[name]}",
                    )
                count_history.append((10, step_ten_counts))
        finally:
            _stop_daemon(checks, daemon)

    checks.check(not state_root.exists(), "throwaway state root was not removed")
    checks.check(not lean_root.exists(), "throwaway Lean workspace root was not removed")
    print(f"ARENA-SMOKE: ALL OK ({checks.total} assertions)", flush=True)
    return 0


def main() -> int:
    try:
        return run_smoke()
    except Exception as exc:  # noqa: BLE001
        print(
            f"ARENA-SMOKE: FAIL {CURRENT_STEP}: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
