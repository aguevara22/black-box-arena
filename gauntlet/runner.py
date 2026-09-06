"""Sequential gate registry, execution, CLI, and JSON/JSONL reporting."""

import argparse
from collections import OrderedDict
from dataclasses import asdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import random
import sys
import time
import traceback

from .gate import Gate, GateContext, GateResult
from .independence import lint
from .paths import scrub_paths
from .policy import Policy, derive_seed


class Registry:
    def __init__(self) -> None:
        self.gates = OrderedDict()

    def register(self, gate: Gate) -> None:
        if gate.id in self.gates:
            raise ValueError(f"duplicate gate id: {gate.id}")
        self.gates[gate.id] = gate


def _gate_record(result: GateResult) -> dict:
    return {
        "type": "gate",
        "gate_id": result.gate_id,
        "title": result.title,
        "claim_ids": result.claim_ids,
        "verdict": result.verdict,
        "n_pass": result.n_pass,
        "n_fail": result.n_fail,
        "n_skip": result.n_skip,
        "runtime_s": result.runtime_s,
        "seed": result.seed,
        "policy_snapshot": result.policy_snapshot,
    }


def _totals(results: list[GateResult]) -> dict:
    return {
        "gates": len(results),
        "checks": sum(len(result.checks) for result in results),
        "pass": sum(result.n_pass for result in results),
        "fail": sum(result.n_fail for result in results),
        "skip": sum(result.n_skip for result in results),
    }


def run(
    registry,
    manifest,
    root_seed=2026,
    quick=False,
    only=None,
    report_dir="out",
):
    sys.dont_write_bytecode = True
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    started_utc = datetime.now(timezone.utc).isoformat()

    meta_started = time.perf_counter()
    meta_seed = derive_seed(root_seed, "manifest")
    meta_policy = Policy()
    meta_ctx = GateContext(
        meta_policy, quick, meta_seed, random.Random(meta_seed), "manifest"
    )
    manifest_problems = manifest.validate()
    if manifest_problems:
        for problem in manifest_problems:
            meta_ctx.check("manifest-problem", manifest.instance, False, problem)
    else:
        meta_ctx.check(
            "manifest-valid", manifest.instance, True, "manifest validates"
        )
    package_root = Path(__file__).resolve().parents[1]
    independence_problems = lint(manifest.independence, package_root)
    if independence_problems:
        for problem in independence_problems:
            meta_ctx.check("independence-violation", manifest.instance, False, problem)
    else:
        meta_ctx.check(
            "independence-lint-clean",
            manifest.instance,
            True,
            "declared route imports are independent",
        )

    requested = None if only is None else set(only)
    if requested is not None:
        for unknown in sorted(requested.difference(registry.gates)):
            meta_ctx.check(
                "unknown-gate-selection",
                unknown,
                False,
                "--only names a registered gate",
            )
    results = [
        GateResult(
            gate_id="manifest",
            title="Manifest and independence checks",
            claim_ids=[],
            checks=meta_ctx.checks,
            runtime_s=time.perf_counter() - meta_started,
            seed=meta_seed,
            policy_snapshot=meta_policy.snapshot(),
        )
    ]

    for gate in registry.gates.values():
        if requested is not None and gate.id not in requested:
            continue
        seed = derive_seed(root_seed, gate.id)
        ctx = GateContext(gate.policy, quick, seed, random.Random(seed), gate.id)
        gate_started = time.perf_counter()
        try:
            gate.fn(ctx)
        except Exception as exc:
            ctx.check(
                "unhandled-exception",
                gate.id,
                False,
                "gate completes without an exception",
                measured=f"{type(exc).__name__}: {exc}",
                extra={"traceback": scrub_paths(traceback.format_exc())},
            )
        results.append(
            GateResult(
                gate_id=gate.id,
                title=gate.title,
                claim_ids=list(gate.claim_ids),
                checks=ctx.checks,
                runtime_s=time.perf_counter() - gate_started,
                seed=seed,
                policy_snapshot=gate.policy.snapshot(),
            )
        )

    ok = all(result.n_fail == 0 for result in results)
    totals = _totals(results)
    summary = {"type": "summary", "ok": ok, "totals": totals}
    meta = {
        "type": "meta",
        "instance": manifest.instance,
        "root_seed": root_seed,
        "quick": bool(quick),
        "started_utc": started_utc,
    }

    output_dir = Path(report_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = output_dir / "report.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as stream:
        stream.write(json.dumps(meta, sort_keys=True) + "\n")
        for result in results:
            for check in result.checks:
                record = {"type": "check", "gate_id": result.gate_id, **asdict(check)}
                stream.write(json.dumps(record, sort_keys=True) + "\n")
            stream.write(json.dumps(_gate_record(result), sort_keys=True) + "\n")
        stream.write(json.dumps(summary, sort_keys=True) + "\n")

    aggregate = {
        "meta": meta,
        "gates": [
            {**_gate_record(result), "checks": [asdict(c) for c in result.checks]}
            for result in results
        ],
        "summary": summary,
    }
    (output_dir / "report.json").write_text(
        json.dumps(aggregate, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return ok, results, jsonl_path


def main(registry, manifest, argv):
    parser = argparse.ArgumentParser(prog=f"python3 -m {manifest.instance}.run")
    parser.add_argument("--quick", action="store_true")
    parser.add_argument("--only", help="comma-separated gate ids")
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--report-dir", default="out")
    args = parser.parse_args(argv)
    only = args.only.split(",") if args.only else None
    ok, _, _ = run(
        registry,
        manifest,
        root_seed=args.seed,
        quick=args.quick,
        only=only,
        report_dir=args.report_dir,
    )
    print(f"GAUNTLET: {'ALL OK' if ok else 'FAILURES'}", flush=True)
    return 0 if ok else 1
