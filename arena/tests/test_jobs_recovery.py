from __future__ import annotations

import importlib
import json


def test_job_registry_recovers_inflight_and_rebuilds_cache(
    tmp_path, monkeypatch
) -> None:
    paths_module = None
    state_manager_module = None
    jobs_module = None
    try:
        with monkeypatch.context() as environment:
            environment.setenv("ORACLE_STATE_ROOT", str(tmp_path))

            from arena import jobs as imported_jobs
            from arena import state_manager as imported_state_manager
            from arena.utils import paths as imported_paths

            paths_module = importlib.reload(imported_paths)
            state_manager_module = importlib.reload(imported_state_manager)
            jobs_module = importlib.reload(imported_jobs)

            shared = tmp_path / "smoke_min" / "shared"
            shared.mkdir(parents=True)
            records = [
                {
                    "event": "job_finished",
                    "job_id": "J-ok",
                    "cache_key": "cache-ok",
                    "result": {"outcome": "ok"},
                },
                {
                    "event": "job_finished",
                    "job_id": "J-compile",
                    "cache_key": "cache-compile",
                    "result": {
                        "outcome": "failed",
                        "failure_kind": "compile_error",
                    },
                },
                {
                    "event": "job_finished",
                    "job_id": "J-timeout",
                    "cache_key": "cache-timeout",
                    "result": {
                        "outcome": "failed",
                        "failure_kind": "timeout",
                    },
                },
                {
                    "event": "job_submitted",
                    "job_id": "J-inflight",
                    "contestant_id": "claude",
                    "mode": "proof",
                    "cache_key": "cache-inflight",
                    "source_sha256": "source-inflight",
                },
                {
                    "event": "job_started",
                    "job_id": "J-inflight",
                    "contestant_id": "claude",
                },
            ]
            (shared / "jobs.jsonl").write_text(
                "".join(json.dumps(record) + "\n" for record in records),
                encoding="utf-8",
            )

            state = state_manager_module.StateManager("smoke_min")
            registry = jobs_module.JobRegistry(state)

            assert registry.recover() == 1
            interrupted = registry.get("J-inflight")
            assert interrupted is not None
            assert interrupted.status == "done"
            assert interrupted.result is not None
            assert interrupted.result["failure_kind"] == "daemon_restart"
            assert set(registry.cache) == {"cache-ok", "cache-compile"}
            assert "cache-timeout" not in registry.cache
    finally:
        if paths_module is not None:
            importlib.reload(paths_module)
        if state_manager_module is not None:
            importlib.reload(state_manager_module)
        if jobs_module is not None:
            importlib.reload(jobs_module)
