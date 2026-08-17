"""Gate factories for Lean-proof instances: independent re-verification of a
solved arena run, from scratch, outside the daemon.

A consolidation instance materializes the solved challenge's frozen files
plus the accepted proof sources into a plain lake project (its own copy —
sharing no state with the arena workspace), then:

  make_lake_build_gate   builds it and audits `#print axioms` per declared
                         decl against the allowed axiom set
  make_frozen_hash_gate  pins the frozen inputs (toolchain, Defs, Goal) by
                         sha256 against the values recorded at solve time

Stdlib-only, like the rest of gauntlet/.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path

from .gate import Gate, GateContext
from .policy import Policy

_DEPENDS_RE = re.compile(r"'([^']+)'\s+depends on axioms:\s*\[([^\]]*)\]", re.DOTALL)
_NO_AXIOMS_RE = re.compile(r"'([^']+)'\s+does not depend on any axioms")

DEFAULT_ALLOWED = ("propext", "Classical.choice", "Quot.sound")


def _elan_env() -> dict[str, str]:
    env = dict(os.environ)
    elan_bin = str(Path.home() / ".elan" / "bin")
    if elan_bin not in env.get("PATH", ""):
        env["PATH"] = elan_bin + os.pathsep + env.get("PATH", "")
    return env


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def make_lake_build_gate(
    gate_id: str,
    title: str,
    claim_ids: list[str],
    project_dir: Path,
    audit_module: str,
    decls: dict[str, str],
    allowed_axioms: tuple[str, ...] = DEFAULT_ALLOWED,
    policy: Policy | None = None,
) -> Gate:
    """Build `project_dir` with lake, then compile an audit file printing
    `#print axioms` for every decl in `decls` (claim_id -> decl name) and
    check each against `allowed_axioms`. sorryAx anywhere is a FAIL."""

    def run(ctx: GateContext) -> None:
        start = time.time()
        proc = subprocess.run(
            ["lake", "build"],
            cwd=project_dir,
            env=_elan_env(),
            capture_output=True,
            text=True,
            timeout=ctx.policy.time_budget_s or 1800,
        )
        ctx.check(
            "lake-build",
            str(project_dir.name),
            proc.returncode == 0,
            "lake build exits 0",
            measured=f"exit={proc.returncode} in {time.time() - start:.1f}s",
        )
        if proc.returncode != 0:
            ctx.skip("axiom-audit", "all", "build failed")
            return

        audit_path = project_dir / "ArenaAudit.lean"
        lines = [f"import {audit_module}"]
        for decl in decls.values():
            lines.append(f"#print axioms {decl}")
        audit_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        try:
            aproc = subprocess.run(
                ["lake", "env", "lean", str(audit_path)],
                cwd=project_dir,
                env=_elan_env(),
                capture_output=True,
                text=True,
                timeout=ctx.policy.time_budget_s or 1800,
            )
        finally:
            audit_path.unlink(missing_ok=True)
        found: dict[str, list[str]] = {}
        for m in _DEPENDS_RE.finditer(aproc.stdout):
            found[m.group(1)] = [a.strip() for a in m.group(2).split(",") if a.strip()]
        for m in _NO_AXIOMS_RE.finditer(aproc.stdout):
            found[m.group(1)] = []
        for claim_id, decl in decls.items():
            if decl not in found:
                ctx.check(
                    "axiom-audit", decl, False,
                    "audit message present", measured="missing from lean output",
                )
                continue
            bad = [a for a in found[decl] if a not in allowed_axioms]
            ctx.check(
                "axiom-audit",
                decl,
                not bad,
                f"axioms within {list(allowed_axioms)}",
                measured=str(found[decl]),
                extra={"claim": claim_id},
            )

    return Gate(
        id=gate_id,
        title=title,
        claim_ids=claim_ids,
        fn=run,
        policy=policy or Policy(time_budget_s=1800),
        tags=["lean"],
    )


def make_frozen_hash_gate(
    gate_id: str,
    title: str,
    claim_ids: list[str],
    files: dict[str, Path],
    recorded: dict[str, str],
    policy: Policy | None = None,
) -> Gate:
    """Check each file's sha256 against the recorded hash (from the solved
    run's state / SOLVED record)."""

    def run(ctx: GateContext) -> None:
        for name, path in files.items():
            if name not in recorded:
                ctx.skip("frozen-hash", name, "no recorded hash")
                continue
            if not path.exists():
                ctx.check("frozen-hash", name, False, "file exists", measured="missing")
                continue
            actual = sha256_file(path)
            ctx.check(
                "frozen-hash",
                name,
                actual == recorded[name],
                "sha256 matches solve-time record",
                measured=actual[:16],
            )

    return Gate(
        id=gate_id,
        title=title,
        claim_ids=claim_ids,
        fn=run,
        policy=policy or Policy(),
        tags=["provenance"],
    )


def load_recorded_hashes(path: Path) -> dict[str, str]:
    """Load {relpath: sha256} recorded at solve time (frozen_hashes.json)."""
    return json.loads(path.read_text(encoding="utf-8"))
