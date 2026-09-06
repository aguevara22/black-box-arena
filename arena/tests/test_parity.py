from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from arena import parity

CHALLENGE = "parity_test"
NOW = datetime.now(timezone.utc)


def _iso(offset_minutes: float) -> str:
    return (NOW - timedelta(minutes=offset_minutes)).isoformat()


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )


def _build_state(tmp_path: Path) -> Path:
    """Asymmetric two-contestant tree: alpha busy, beta quiet."""
    shared = tmp_path / CHALLENGE / "shared"
    contestants = tmp_path / CHALLENGE / "contestants"

    _write_jsonl(
        contestants / "alpha" / "turns.jsonl",
        [
            {"contestant_id": "alpha", "ts": _iso(120)},
            {"contestant_id": "alpha", "ts": _iso(60)},
            {"contestant_id": "alpha", "ts": _iso(10)},
        ],
    )
    _write_jsonl(
        contestants / "beta" / "turns.jsonl",
        [{"contestant_id": "beta", "ts": _iso(90)}],
    )

    _write_jsonl(
        shared / "jobs.jsonl",
        [
            # J1 alpha: full lifecycle, fresh ok, 60s latency -> one job.
            {
                "ts": _iso(50),
                "event": "job_submitted",
                "job_id": "J1",
                "contestant_id": "alpha",
                "mode": "proof",
                "node_id": "n_1",
                "cache_key": "ck1",
                "source_sha256": "s1",
                "predict": "ok",
                "timeout": 600,
            },
            {
                "ts": _iso(49.5),
                "event": "job_started",
                "job_id": "J1",
                "contestant_id": "alpha",
            },
            {
                "ts": _iso(49),
                "event": "job_finished",
                "job_id": "J1",
                "contestant_id": "alpha",
                "mode": "proof",
                "node_id": "n_1",
                "cache_key": "ck1",
                "source_sha256": "s1",
                "result": {"outcome": "ok", "duration_s": 55.0},
            },
            # J2 alpha: cache-hit replay (no job_started) -> cache hit, not ok.
            {
                "ts": _iso(40),
                "event": "job_submitted",
                "job_id": "J2",
                "contestant_id": "alpha",
                "mode": "proof",
                "node_id": "n_1",
                "cache_key": "ck1",
                "source_sha256": "s1",
            },
            {
                "ts": _iso(40),
                "event": "job_finished",
                "job_id": "J2",
                "contestant_id": "alpha",
                "mode": "proof",
                "node_id": "n_1",
                "cache_key": "ck1",
                "source_sha256": "s1",
                "result": {"outcome": "ok", "cache_hit": True},
            },
            # J3 beta: full lifecycle, fresh compile_error failure.
            {
                "ts": _iso(30),
                "event": "job_submitted",
                "job_id": "J3",
                "contestant_id": "beta",
                "mode": "proof",
                "node_id": "n_1",
                "cache_key": "ck3",
                "source_sha256": "s3",
            },
            {
                "ts": _iso(29),
                "event": "job_started",
                "job_id": "J3",
                "contestant_id": "beta",
            },
            {
                "ts": _iso(28),
                "event": "job_finished",
                "job_id": "J3",
                "contestant_id": "beta",
                "mode": "proof",
                "node_id": "n_1",
                "cache_key": "ck3",
                "source_sha256": "s3",
                "result": {"outcome": "failed", "failure_kind": "compile_error"},
            },
            # J4 beta: submitted, never finished (daemon-restart casualty).
            {
                "ts": _iso(20),
                "event": "job_submitted",
                "job_id": "J4",
                "contestant_id": "beta",
                "mode": "proof",
                "node_id": "n_2",
                "cache_key": "ck4",
                "source_sha256": "s4",
            },
        ],
    )

    _write_jsonl(
        shared / "dag.jsonl",
        [
            {
                "ts": _iso(200),
                "event": "node_proposed",
                "actor": "daemon",
                "node_id": "n_1",
                "name": "root",
                "statement": "True",
                "is_root": True,
            },
            # beta proves first ...
            {
                "ts": _iso(80),
                "event": "status_changed",
                "actor": "beta",
                "contestant_id": "beta",
                "node_id": "n_1",
                "from": "open",
                "to": "proved",
                "job_id": "J0",
                "proved_modulo": [],
            },
            # ... alpha supersedes (last-writer-wins moves the credit) ...
            {
                "ts": _iso(48),
                "event": "status_changed",
                "actor": "alpha",
                "contestant_id": "alpha",
                "node_id": "n_1",
                "from": "proved",
                "to": "proved",
                "job_id": "J1",
                "proved_modulo": [],
            },
            # ... and re-proves the same node again (must not count twice).
            {
                "ts": _iso(39),
                "event": "status_changed",
                "actor": "alpha",
                "contestant_id": "alpha",
                "node_id": "n_1",
                "from": "proved",
                "to": "proved",
                "job_id": "J2",
                "proved_modulo": [],
            },
            {
                "ts": _iso(15),
                "event": "node_accepted",
                "actor": "beta",
                "node_id": "n_1",
                "reason": "fidelity checked",
            },
        ],
    )

    _write_jsonl(
        shared / "findings.jsonl",
        [
            {
                "ts": _iso(70),
                "contestant_id": "alpha",
                "text": "a real finding",
                "id": "f1",
            },
            {
                "ts": _iso(65),
                "contestant_id": "alpha",
                "text": "[unpromoted breakthrough candidate kernel] demoted",
                "id": "f2",
            },
        ],
    )

    _write_jsonl(
        shared / "breakthroughs.jsonl",
        [
            {
                "ts": _iso(45),
                "contestant_id": "alpha",
                "text": "big step",
                "promoted_via": "kernel_verdict:J1",
                "source_id": "J1",
                "id": "b1",
            },
            # cache-hit re-promotion of the same content: must dedupe.
            {
                "ts": _iso(38),
                "contestant_id": "alpha",
                "text": "big step",
                "promoted_via": "kernel_verdict:J1",
                "source_id": "J1",
                "id": "b2",
            },
            # same claim re-cited via a cache-hit resubmission: the fresh
            # job id changes the kernel_verdict tag but not the claim.
            {
                "ts": _iso(37),
                "contestant_id": "alpha",
                "text": "big step",
                "promoted_via": "kernel_verdict:J2",
                "source_id": "J1",
                "id": "b3",
            },
        ],
    )

    return tmp_path


def _run_json(tmp_path: Path, capsys, extra: list[str] | None = None) -> dict:
    argv = [
        "--state-root",
        str(tmp_path),
        "--challenge",
        CHALLENGE,
        "--json",
    ] + (extra or [])
    assert parity.main(argv) == 0
    return json.loads(capsys.readouterr().out)


def test_job_lifecycle_counts_once_and_cache_hit_is_not_fresh_ok(
    tmp_path, capsys
) -> None:
    data = _run_json(_build_state(tmp_path), capsys)
    alpha_jobs = data["contestants"]["alpha"]["jobs"]
    beta_jobs = data["contestants"]["beta"]["jobs"]

    # J1 (3 lifecycle events) + J2 (2 events) = 2 jobs, not 5.
    assert alpha_jobs["submitted"] == 2
    assert alpha_jobs["ok"] == 1
    assert alpha_jobs["cache_hits"] == 1
    assert alpha_jobs["failed_total"] == 0

    assert beta_jobs["submitted"] == 2
    assert beta_jobs["ok"] == 0
    assert beta_jobs["failed"] == {"compile_error": 1}
    assert beta_jobs["unfinished"] == 1


def test_superseded_proof_credit_moves_and_never_double_counts(
    tmp_path, capsys
) -> None:
    data = _run_json(_build_state(tmp_path), capsys)
    alpha_dag = data["contestants"]["alpha"]["dag"]
    beta_dag = data["contestants"]["beta"]["dag"]

    # alpha superseded beta on n_1 and re-proved it twice: one node, once.
    assert alpha_dag["proved"] == 1
    assert alpha_dag["proved_modulo"] == 0
    # beta's stale credit is cleared by last-writer-wins replay semantics.
    assert beta_dag["proved"] == 0
    # acceptance is credited to the accepting peer, never the prover.
    assert beta_dag["accepted"] == 1
    assert alpha_dag["accepted"] == 0


def test_asymmetric_activity_turns_findings_breakthroughs(
    tmp_path, capsys
) -> None:
    data = _run_json(_build_state(tmp_path), capsys)
    alpha = data["contestants"]["alpha"]
    beta = data["contestants"]["beta"]

    assert alpha["turns"] == 3
    assert beta["turns"] == 1
    # demoted breakthrough candidates do not inflate the findings headline.
    assert alpha["findings"] == 1
    assert alpha["findings_demoted"] == 1
    assert beta["findings"] == 0
    # re-promoted identical breakthrough is deduped.
    assert alpha["breakthroughs"] == 1
    assert beta["breakthroughs"] == 0
    assert alpha["last_activity_age_hours"] is not None
    assert alpha["median_job_latency_seconds"] == 60.0
    # cache hit (no kernel worker) contributes no latency sample for beta? J3 did run.
    assert beta["median_job_latency_seconds"] == 120.0


def test_head_to_head_ratios_guard_division_by_zero(tmp_path, capsys) -> None:
    data = _run_json(_build_state(tmp_path), capsys)
    h2h = data["head_to_head"]
    assert h2h["pair"] == ["alpha", "beta"]

    turns = h2h["metrics"]["turns"]
    assert turns["a"] == 3 and turns["b"] == 1
    assert turns["ratio"] == 3.0

    proved = h2h["metrics"]["nodes_proved"]
    assert proved["a"] == 1 and proved["b"] == 0
    assert proved["ratio"] is None
    assert proved["note"] == "zero denominator"

    assert "summary" in h2h and "verdict" in h2h["summary"].lower()


def test_since_hours_window_filters_old_events(tmp_path, capsys) -> None:
    _build_state(tmp_path)
    data = _run_json(tmp_path, capsys, extra=["--since-hours", "0.5"])
    alpha = data["contestants"]["alpha"]
    # only the turn 10 minutes ago is inside the 0.5h window.
    assert alpha["turns"] == 1
    # last activity ignores the window (it is an age, not a windowed count).
    assert alpha["last_activity_ts"] is not None


def test_since_hours_zero_is_an_empty_window_not_all_time(
    tmp_path, capsys
) -> None:
    _build_state(tmp_path)
    data = _run_json(tmp_path, capsys, extra=["--since-hours", "0"])
    alpha = data["contestants"]["alpha"]
    assert data["window_hours"] == 0.0
    assert alpha["turns"] == 0
    assert alpha["breakthroughs"] == 0
    assert alpha["jobs"]["submitted"] == 0
    assert alpha["dag"]["proved"] == 0
    # last activity still reported: it is an age, not a windowed count.
    assert alpha["last_activity_ts"] is not None


def test_empty_state_is_all_absent_and_exits_zero(tmp_path, capsys) -> None:
    rc = parity.main(["--state-root", str(tmp_path), "--challenge", "ghost"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "none discovered" in out
    assert "absent" in out


def test_empty_state_json_and_missing_files_read_as_null(
    tmp_path, capsys
) -> None:
    data = _run_json(tmp_path, capsys)
    assert data["contestants"] == {}
    assert data["head_to_head"] is None
    assert data["sources"] == {
        "jobs": False,
        "dag": False,
        "findings": False,
        "breakthroughs": False,
    }

    # a contestant dir with no logs at all: turns absent (null), not zero.
    (tmp_path / CHALLENGE / "contestants" / "gamma").mkdir(parents=True)
    data = _run_json(tmp_path, capsys)
    gamma = data["contestants"]["gamma"]
    assert gamma["turns"] is None
    assert gamma["jobs"] is None
    assert gamma["dag"] is None
    assert gamma["findings"] is None


def test_json_output_is_valid_and_shaped(tmp_path, capsys) -> None:
    data = _run_json(_build_state(tmp_path), capsys)
    assert set(data["contestants"]) == {"alpha", "beta"}
    for key in (
        "challenge",
        "state_root",
        "generated_at",
        "window_hours",
        "sources",
        "contestants",
        "head_to_head",
        "notes",
    ):
        assert key in data
    for contestant in data["contestants"].values():
        for key in (
            "turns",
            "last_activity_ts",
            "findings",
            "breakthroughs",
            "jobs",
            "dag",
            "active_hours",
            "rates",
            "median_job_latency_seconds",
        ):
            assert key in contestant
