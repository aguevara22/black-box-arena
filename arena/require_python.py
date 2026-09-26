"""Refuse, with one clear line, any interpreter below the tested floor.

Every entry point (daemon, both smokes, the demo, the test suite) imports this
before any other arena module. The arena and the gauntlet use ``X | None``
annotations that pydantic and dataclasses evaluate at import time, so on an
older interpreter the failure would otherwise surface as a TypeError deep in
the import chain -- on a stock macOS ``python3`` (3.9) that is the first thing
a new user sees. This file itself must stay valid on old interpreters.
"""
import sys

MINIMUM = (3, 11)
TESTED = "3.11 and 3.12"


def check(version_info=sys.version_info, executable=sys.executable):
    """Exit with an actionable message when the interpreter is too old."""
    if tuple(version_info[:2]) < MINIMUM:
        raise SystemExit(
            "arena: Python %d.%d or newer is required (tested on %s); this "
            "interpreter is %d.%d.%d at %s. Create the venv with a newer "
            "interpreter, e.g.  python3.11 -m venv .venv  (see SETUP.md)."
            % (
                MINIMUM[0],
                MINIMUM[1],
                TESTED,
                version_info[0],
                version_info[1],
                version_info[2],
                executable,
            )
        )


check()
