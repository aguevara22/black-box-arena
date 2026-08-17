"""Entry point: materialize the consolidation project, run the gates,
generate the proof ledger (verification tables), drift-check."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from gauntlet import dag_lint, lean_gates, report, runner
from gauntlet.gate import Gate
from gauntlet.policy import Policy

from . import consolidate
from .manifest_def import MANIFEST

OUT_DIR = Path(__file__).resolve().parent / "out"


def registry() -> runner.Registry:
    reg = runner.Registry()

    project, decls, _root_decl = consolidate.materialize(OUT_DIR)
    audit_decls = {f"NODE-{name}": name for name in decls.values()}
    audit_decls["GOAL-FIDELITY"] = "_consolidation_goal_check"

    reg.register(
        lean_gates.make_lake_build_gate(
            gate_id="final-build",
            title="Consolidation project builds; axioms audited",
            claim_ids=[c.id for c in MANIFEST.claims],
            project_dir=project,
            audit_module="Final",
            decls=audit_decls,
        )
    )

    frozen_rec_path = consolidate.state_dir() / "frozen_hashes.json"
    if frozen_rec_path.exists():
        recorded = json.loads(frozen_rec_path.read_text(encoding="utf-8"))["files"]
        files = {
            rel: consolidate.challenge_dir() / rel for rel in recorded
        }
        reg.register(
            lean_gates.make_frozen_hash_gate(
                gate_id="frozen-hashes",
                title="Frozen challenge inputs match the solve-time record",
                claim_ids=["GOAL-FIDELITY"],
                files=files,
                recorded=recorded,
            )
        )

    def dag_gate(ctx) -> None:
        problems = dag_lint.lint(consolidate.state_dir())
        if not problems:
            ctx.check("dag-lint", consolidate.CHALLENGE, True, "no structural problems")
        for problem in problems:
            ctx.check("dag-lint", consolidate.CHALLENGE, False, "structural problem", measured=problem)

    reg.register(
        Gate(
            id="dag-lint",
            title="Arena DAG structural lint",
            claim_ids=[c.id for c in MANIFEST.claims if c.node_id],
            fn=dag_gate,
            policy=Policy(),
            tags=["lint"],
        )
    )
    return reg


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not any(a.startswith("--report-dir") for a in argv):
        argv += ["--report-dir", str(OUT_DIR)]
    rc = runner.main(registry(), MANIFEST, argv)
    rep = report.load_report(OUT_DIR / "report.jsonl")
    report.write_tables(
        MANIFEST,
        rep,
        OUT_DIR / "verification_table.md",
        OUT_DIR / "verification_table.tex",
    )
    warnings = report.drift_check(MANIFEST, rep)
    if warnings:
        for w in warnings:
            print(f"DRIFT: {w}")
    else:
        print("DRIFT: clean")
    return 1 if (rc or warnings) else 0


if __name__ == "__main__":
    raise SystemExit(main())
