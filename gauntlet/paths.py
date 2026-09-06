"""Portable path rendering for everything the kit writes into reports,
tables and tracebacks.

Tracked outputs must never carry a machine's home directory or account name:
the repo is meant to be cloned and run by other people. Paths inside the
package render relative to the package root (POSIX separators, so two
machines produce identical bytes); paths under the user's home render as
`~/...`; anything else renders as given.
"""

from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]


def display_path(path) -> str:
    candidate = Path(path)
    try:
        resolved = candidate.resolve()
    except OSError:
        resolved = candidate
    try:
        return resolved.relative_to(PACKAGE_ROOT).as_posix()
    except ValueError:
        pass
    try:
        return "~/" + resolved.relative_to(Path.home()).as_posix()
    except ValueError:
        return resolved.as_posix()


def scrub_paths(text: str) -> str:
    """Replace the package root and the home directory inside free text
    (tracebacks, log excerpts) so recorded output stays account-agnostic."""
    return text.replace(str(PACKAGE_ROOT), ".").replace(str(Path.home()), "~")
