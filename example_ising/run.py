"""Run all Ising gates and generate claim-coupled verification tables."""

import argparse
from pathlib import Path
import sys

from gauntlet import report, runner

from .gates.gate_census import GATE as CENSUS_GATE
from .gates.gate_conv import GATE as CONV_GATE
from .gates.gate_dos import GATE as DOS_GATE
from .gates.gate_gs import GATE as GS_GATE
from .gates.gate_oracle import GATE as ORACLE_GATE
from .gates.gate_sym import GATE as SYM_GATE
from .manifest_def import MANIFEST


DEFAULT_REPORT_DIR = Path("example_ising/out")


def registry():
    registered = runner.Registry()
    for gate in (ORACLE_GATE, SYM_GATE, DOS_GATE, GS_GATE, CONV_GATE, CENSUS_GATE):
        registered.register(gate)
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

