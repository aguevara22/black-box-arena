"""Contract duty: run the registry and generate verification tables.
This mechanical entry point stays usable while implementation TODOs fail.
"""

import argparse
from pathlib import Path
import sys

from gauntlet import report, runner

from .gates.gate_example import GATE as EXAMPLE_GATE
from .manifest_def import MANIFEST


DEFAULT_REPORT_DIR = Path("instance_template/out")


def registry():
    registered = runner.Registry()
    registered.register(EXAMPLE_GATE)
    return registered


def _effective_argv(argv):
    arguments = list(argv)
    has_report_dir = any(
        value == "--report-dir" or value.startswith("--report-dir=")
        for value in arguments
    )
    if not has_report_dir:
        arguments.extend(["--report-dir", str(DEFAULT_REPORT_DIR)])
    return arguments


def _report_dir(argv):
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--report-dir", default=str(DEFAULT_REPORT_DIR))
    args, _ = parser.parse_known_args(argv)
    return Path(args.report_dir)


def main(argv=None):
    effective = _effective_argv(sys.argv[1:] if argv is None else argv)
    exit_code = runner.main(registry(), MANIFEST, effective)
    output_dir = _report_dir(effective)
    loaded = report.load_report(output_dir / "report.jsonl")
    report.write_tables(
        MANIFEST,
        loaded,
        output_dir / "verification_table.md",
        output_dir / "verification_table.tex",
    )
    warnings = report.drift_check(MANIFEST, loaded)
    for warning in warnings:
        print(f"DRIFT: {warning}", flush=True)
    if not warnings:
        print("DRIFT: clean", flush=True)
    return 1 if exit_code or warnings else 0


if __name__ == "__main__":
    raise SystemExit(main())

