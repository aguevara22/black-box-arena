#!/usr/bin/env python3
"""Run a selected worked instance and summarize its reports."""

import sys

sys.dont_write_bytecode = True

import argparse
import importlib
from pathlib import Path
import time

from gauntlet.report import drift_check, load_report


def _summary(instance, manifest, output_dir, exit_code):
    report_path = output_dir / "report.jsonl"
    loaded = load_report(report_path)
    warnings = drift_check(manifest, loaded)
    totals = loaded["summary"]["totals"]
    return {
        "instance": instance,
        "ok": exit_code == 0 and not warnings and bool(loaded["summary"].get("ok")),
        "totals": totals,
        "warnings": warnings,
        "report": report_path,
        "table_md": output_dir / "verification_table.md",
        "table_tex": output_dir / "verification_table.tex",
    }


def _print_summary(items, elapsed):
    print("\n=== COMBINED SUMMARY ===")
    for item in items:
        totals = item["totals"]
        print(f"{item['instance']}: {'OK' if item['ok'] else 'FAIL'}")
        print(
            f"  gates={totals['gates']} checks={totals['checks']} "
            f"PASS={totals['pass']} FAIL={totals['fail']} SKIP={totals['skip']}"
        )
        print(f"  report={item['report']}")
        print(f"  tables={item['table_md']}, {item['table_tex']}")
        print(f"  drift={'clean' if not item['warnings'] else '; '.join(item['warnings'])}")
    print(f"total_wall_time_s={elapsed:.3f}")


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true")
    parser.add_argument("--instance", default="example_ising")
    args = parser.parse_args(argv)
    output_dir = Path(args.instance) / "out"
    forwarded = ["--report-dir", str(output_dir)]
    if args.quick:
        forwarded.append("--quick")
    started = time.perf_counter()

    manifest_module = importlib.import_module(f"{args.instance}.manifest_def")
    run_module = importlib.import_module(f"{args.instance}.run")
    manifest = manifest_module.MANIFEST
    exit_code = run_module.main(forwarded)
    summaries = [
        _summary(args.instance, manifest, output_dir, exit_code)
    ]

    elapsed = time.perf_counter() - started
    _print_summary(summaries, elapsed)
    return 0 if all(item["ok"] for item in summaries) else 1


if __name__ == "__main__":
    raise SystemExit(main())
