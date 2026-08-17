"""Parse `lean --json` output: diagnostics and `#print axioms` extraction.

`lean --json` emits one JSON object per line per diagnostic, e.g.
  {"severity":"error","pos":{"line":3,"column":2},"endPos":{...},
   "fileName":"Candidates/J01.lean","data":"unknown identifier 'foo'"}

`#print axioms decl` arrives as an information message whose data is either
  "'<decl>' depends on axioms: [ax1, ax2, ...]"
or
  "'<decl>' does not depend on any axioms"
(with minor formatting drift across versions — the regexes are tolerant).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

MAX_MESSAGE_CHARS = 4000
MAX_DIAGNOSTICS = 50

_DEPENDS_RE = re.compile(
    r"'(?P<decl>[^']+)'\s+depends on axioms:\s*\[(?P<axioms>[^\]]*)\]",
    flags=re.DOTALL,
)
_NO_AXIOMS_RE = re.compile(r"'(?P<decl>[^']+)'\s+does not depend on any axioms")


@dataclass
class Diagnostic:
    severity: str
    line: int
    column: int
    message: str

    def as_record(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "pos": f"{self.line}:{self.column}",
            "message": self.message[:MAX_MESSAGE_CHARS],
        }


def parse_output(raw: str) -> list[Diagnostic]:
    """Parse the stdout of `lean --json`. Non-JSON lines (build noise from
    lake, progress chatter) are ignored."""
    diags: list[Diagnostic] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict) or "severity" not in obj:
            continue
        pos = obj.get("pos") or {}
        severity = str(obj.get("severity", ""))
        if severity == "info":
            severity = "information"
        diags.append(
            Diagnostic(
                severity=severity,
                line=int(pos.get("line", 0) or 0),
                column=int(pos.get("column", 0) or 0),
                message=str(obj.get("data", "")),
            )
        )
    return diags


def errors(diags: list[Diagnostic]) -> list[Diagnostic]:
    return [d for d in diags if d.severity == "error"]


def extract_axioms(
    diags: list[Diagnostic], *, min_line: int = 0
) -> dict[str, list[str]]:
    """Collect `#print axioms` results from information messages.

    `min_line`: only trust messages positioned at or after this line — the
    daemon knows where its appended trailer starts, and anything a contestant
    managed to print above it is not the audit. (Proof-mode source scans
    already forbid `#print`/`#eval`, so this is belt-and-braces.)
    """
    found: dict[str, list[str]] = {}
    for d in diags:
        if d.severity != "information" or d.line < min_line:
            continue
        m = _DEPENDS_RE.search(d.message)
        if m:
            axioms = [a.strip() for a in m.group("axioms").split(",") if a.strip()]
            found[m.group("decl")] = axioms
            continue
        m = _NO_AXIOMS_RE.search(d.message)
        if m:
            found[m.group("decl")] = []
    return found


def truncate_for_record(diags: list[Diagnostic]) -> list[dict[str, Any]]:
    """Bounded representation for the append-only job log."""
    records = [d.as_record() for d in diags if d.severity in ("error", "warning")]
    if len(records) > MAX_DIAGNOSTICS:
        overflow = len(records) - MAX_DIAGNOSTICS
        records = records[:MAX_DIAGNOSTICS]
        records.append(
            {"severity": "information", "pos": "0:0", "message": f"[{overflow} more diagnostics truncated]"}
        )
    return records
