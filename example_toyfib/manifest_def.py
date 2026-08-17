"""Manifest for the consolidation instance: one claim per DAG node, built
from the solved run's dag.jsonl, plus the overall goal-fidelity claim."""
from __future__ import annotations

from gauntlet.manifest import Claim, Manifest

from . import consolidate


def build_manifest() -> Manifest:
    nodes, children, root = consolidate.load_dag()
    claims: list[Claim] = []
    for nid, rec in nodes.items():
        name = rec.get("name", nid)
        claims.append(
            Claim(
                id=f"NODE-{name}",
                statement=rec.get("statement", ""),
                status="proved",
                status_note="kernel-proved in the arena; re-verified here from scratch",
                proof_ref=f"state proofs/{nid}.lean",
                gates=["final-build", "dag-lint"],
                lean_decl=name,
                node_id=nid,
            )
        )
    fidelity_gates = ["final-build"]
    if (consolidate.state_dir() / "frozen_hashes.json").exists():
        fidelity_gates.append("frozen-hashes")
    claims.append(
        Claim(
            id="GOAL-FIDELITY",
            statement="the root declaration elaborates against the frozen Arena.GoalStatement",
            status="proved",
            status_note="defeq elaboration in the consolidation build",
            proof_ref="Final.lean :: _consolidation_goal_check",
            gates=fidelity_gates,
        )
    )
    return Manifest(
        instance="example_toyfib",
        version="0.1",
        description=(
            f"From-scratch re-verification of the solved {consolidate.CHALLENGE} "
            "arena run: independent reassembly, lake build, axiom audit, DAG lint, "
            "frozen-input hashes."
        ),
        claims=claims,
        independence=[],
    )


MANIFEST = build_manifest()
