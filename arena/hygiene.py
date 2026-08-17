"""Mechanical hygiene gate for submitted Lean source.

Two layers, run at different times:

1. `scan_source` — fast daemon-side scan BEFORE any build. Catches the
   legible cheats (sorry, native_decide, smuggled axioms, heartbeat
   inflation, out-of-allowlist imports) and gives contestants an immediate,
   named rejection. This layer is convenience and defense-in-depth.

2. `audit_axioms` — the authoritative verdict, computed from the kernel's
   own `#print axioms` output on the daemon-appended trailer. Anything the
   scan missed (macro-hidden sorries, `native_decide` via aliasing) surfaces
   here as a forbidden axiom (`sorryAx`, `Lean.ofReduceBool`,
   `Lean.trustCompiler`, or the axiom's own name).

The scan works on comment-stripped source so tokens inside comments do not
false-positive, and forbidden constructs cannot hide inside comments anyway
(comments never reach the kernel).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

try:
    from .config import HygieneConfig
except ImportError:  # direct module execution
    from config import HygieneConfig

# Axiom introduced by `sorry`/`admit`; allowed only in skeleton/proof-modulo
# contexts, never in a final build.
SORRY_AXIOM = "sorryAx"


def strip_comments(source: str) -> str:
    """Remove Lean line comments (`-- ...`) and nested block comments
    (`/- ... -/`), preserving line structure so scan diagnostics can cite
    line numbers. String literals are respected (a `--` inside a string is
    not a comment)."""
    out: list[str] = []
    i = 0
    n = len(source)
    depth = 0
    in_string = False
    while i < n:
        ch = source[i]
        nxt = source[i + 1] if i + 1 < n else ""
        if depth > 0:
            if ch == "-" and nxt == "/":
                depth -= 1
                i += 2
            elif ch == "/" and nxt == "-":
                depth += 1
                i += 2
            else:
                if ch == "\n":
                    out.append("\n")
                i += 1
            continue
        if in_string:
            out.append(ch)
            if ch == "\\" and nxt:
                out.append(nxt)
                i += 2
                continue
            if ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
            continue
        if ch == "/" and nxt == "-":
            depth += 1
            i += 2
            continue
        if ch == "-" and nxt == "-":
            # line comment: drop to end of line
            j = source.find("\n", i)
            if j == -1:
                break
            i = j
            continue
        out.append(ch)
        i += 1
    return "".join(out)


# Tokens forbidden as words anywhere in (comment-stripped) contestant source.
# `sorry` handled separately (allowed in skeleton mode).
_FORBIDDEN_WORDS = (
    "admit",
    "native_decide",
    "unsafe",
    "extern",
    "implemented_by",
    "run_cmd",
    "run_elab",
    "initialize",
)

# Commands that could fabricate info messages resembling the axiom audit or
# escape into IO. eval-mode source may use #eval (that's its purpose); proof
# and skeleton submissions have no business emitting anything.
_FORBIDDEN_COMMANDS_PROOFLIKE = ("#print", "#eval", "#reduce", "#check")

_WORD_RE = {w: re.compile(rf"(?<![A-Za-z0-9_.]){re.escape(w)}(?![A-Za-z0-9_'])") for w in _FORBIDDEN_WORDS}
_SORRY_RE = re.compile(r"(?<![A-Za-z0-9_.])(sorry|stop)(?![A-Za-z0-9_'])")
_AXIOM_RE = re.compile(r"(?<![A-Za-z0-9_.])axiom(?![A-Za-z0-9_'])")
_SET_OPTION_RE = re.compile(
    r"set_option\s+([A-Za-z0-9_.]+)\s+(\S+)"
)
_IMPORT_RE = re.compile(r"^\s*import\s+([A-Za-z0-9_.«»]+)", flags=re.MULTILINE)
_OPTION_CAPS = {
    "maxHeartbeats": "max_heartbeats",
    "maxRecDepth": "max_rec_depth",
}
# Options that may never be touched at all.
_OPTION_FORBIDDEN = ("debug.skipKernelTC", "trace.profiler.output")


@dataclass
class ScanReport:
    violations: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations


def scan_source(source: str, cfg: HygieneConfig, mode: str) -> ScanReport:
    """Daemon-side source scan. `mode` is one of eval|proof|skeleton|final
    (final assemblies are daemon-built but re-scanned as defense-in-depth;
    node sources inside them were each scanned at submission)."""
    report = ScanReport()
    stripped = strip_comments(source)

    def _cite(match: re.Match[str], message: str) -> None:
        line = stripped.count("\n", 0, match.start()) + 1
        report.violations.append(f"line {line}: {message}")

    if mode not in ("skeleton",):
        for m in _SORRY_RE.finditer(stripped):
            _cite(m, f"forbidden token '{m.group(1)}' (only skeleton submissions may leave holes)")

    for word, rx in _WORD_RE.items():
        for m in rx.finditer(stripped):
            _cite(m, f"forbidden token '{word}'")

    for m in _AXIOM_RE.finditer(stripped):
        _cite(m, "declaring axioms is forbidden")

    if mode in ("proof", "skeleton", "final"):
        for cmd in _FORBIDDEN_COMMANDS_PROOFLIKE:
            idx = stripped.find(cmd)
            while idx != -1:
                line = stripped.count("\n", 0, idx) + 1
                report.violations.append(
                    f"line {line}: '{cmd}' is not allowed in {mode} submissions"
                )
                idx = stripped.find(cmd, idx + 1)

    for m in _SET_OPTION_RE.finditer(stripped):
        opt, value = m.group(1), m.group(2)
        if opt in _OPTION_FORBIDDEN:
            _cite(m, f"set_option {opt} is forbidden")
            continue
        cap_attr = _OPTION_CAPS.get(opt)
        if cap_attr is not None:
            cap = getattr(cfg, cap_attr)
            try:
                requested = int(value)
            except ValueError:
                _cite(m, f"set_option {opt} with non-integer value {value!r}")
                continue
            if requested > cap:
                _cite(m, f"set_option {opt} {requested} exceeds cap {cap}")

    allow = tuple(cfg.import_allowlist)
    for m in _IMPORT_RE.finditer(stripped):
        module = m.group(1)
        root = module.split(".", 1)[0]
        if root not in allow:
            line = stripped.count("\n", 0, m.start()) + 1
            report.violations.append(
                f"line {line}: import {module} outside allowlist {sorted(allow)}"
            )

    if len(source.encode("utf-8")) != len(source.encode("utf-8", "ignore")):
        report.violations.append("source contains unencodable bytes")

    return report


@dataclass
class AxiomAudit:
    ok: bool
    decl: str
    axioms: list[str]
    violations: list[str]


def audit_axioms(
    decl: str,
    axioms: list[str],
    cfg: HygieneConfig,
    *,
    allow_sorry: bool = False,
) -> AxiomAudit:
    """Judge the kernel-reported axiom set of `decl` against the whitelist.
    `allow_sorry` is True for skeleton jobs and proof jobs compiled against
    stubbed (not-yet-proved) dependencies."""
    allowed = set(cfg.allowed_axioms)
    if allow_sorry:
        allowed.add(SORRY_AXIOM)
    violations = [
        f"decl {decl} depends on forbidden axiom {ax}"
        for ax in axioms
        if ax not in allowed
    ]
    return AxiomAudit(ok=not violations, decl=decl, axioms=axioms, violations=violations)
