"""Independent re-assembly of a solved arena run into a lake project.

Deliberately does NOT import arena/ — this is the consolidation's own route
to the assembled proof (Layer B stays decoupled, and the reassembly is an
independent implementation of the same construction the daemon performed).
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

CHALLENGE = os.environ.get("ARENA_CONSOLIDATE_CHALLENGE", "toy_fib_add")
STATE_ROOT = Path(
    os.environ.get("ORACLE_STATE_ROOT", str(PROJECT_ROOT / "state"))
).expanduser()

_IMPORT_RE = re.compile(r"^\s*import\s+\S+\s*$")


def challenge_dir() -> Path:
    return PROJECT_ROOT / "challenges" / CHALLENGE


def state_dir() -> Path:
    return STATE_ROOT / CHALLENGE


def load_dag() -> tuple[dict[str, dict], dict[str, list[str]], str | None]:
    """Replay dag.jsonl into (nodes, children, root_id) — our own reader."""
    nodes: dict[str, dict] = {}
    children: dict[str, list[str]] = {}
    root: str | None = None
    path = state_dir() / "shared" / "dag.jsonl"
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            event = rec.get("event")
            if event == "node_proposed":
                nodes[rec["node_id"]] = rec
                if rec.get("is_root"):
                    root = rec["node_id"]
            elif event == "decomposition_accepted":
                children[rec["parent"]] = list(rec.get("child_node_ids", []))
    return nodes, children, root


def topo_order(children: dict[str, list[str]], root: str) -> list[str]:
    """Dependencies-first ordering of the root closure."""
    order: list[str] = []
    seen: set[str] = set()

    def visit(nid: str) -> None:
        if nid in seen:
            return
        seen.add(nid)
        for child in children.get(nid, []):
            visit(child)
        order.append(nid)

    visit(root)
    return order


def assemble_final() -> tuple[str, dict[str, str]]:
    """Concatenate stored proof sources in topological order, hoisting
    imports. Returns (final_text, decl_by_node)."""
    nodes, children, root = load_dag()
    if root is None:
        raise RuntimeError("no root node in dag.jsonl")
    imports: list[str] = []
    seen_imports: set[str] = set()
    sections: list[str] = []
    decls: dict[str, str] = {}
    for nid in topo_order(children, root):
        src_path = state_dir() / "proofs" / f"{nid}.lean"
        if not src_path.exists():
            raise RuntimeError(f"missing stored proof source for node {nid}")
        decls[nid] = nodes[nid].get("name", "")
        body_lines: list[str] = []
        header = True
        for line in src_path.read_text(encoding="utf-8").splitlines():
            if header and _IMPORT_RE.match(line):
                if line.strip() not in seen_imports:
                    seen_imports.add(line.strip())
                    imports.append(line.strip())
                continue
            if header and (not line.strip() or line.lstrip().startswith("--")):
                continue
            header = False
            body_lines.append(line)
        sections.append(f"-- node {nid} ({decls[nid]})")
        sections.append("\n".join(body_lines).strip())
    if "import Goal" not in seen_imports:
        imports.append("import Goal")
    return "\n".join(imports) + "\n\n" + "\n\n".join(sections) + "\n", decls


def materialize(out_dir: Path) -> tuple[Path, dict[str, str], str]:
    """Create a fresh lake project under out_dir with the frozen challenge
    files plus Final.lean; returns (project_dir, decl_by_node, root_decl)."""
    import shutil

    nodes, children, root = load_dag()
    assert root is not None
    project = out_dir / "consolidation"
    project.mkdir(parents=True, exist_ok=True)
    src = challenge_dir()
    for fname in ("lean-toolchain", "lake-manifest.json", "Goal.lean", "Defs.lean", "Calibration.lean"):
        f = src / fname
        if f.exists():
            shutil.copy2(f, project / fname)
    for dname in ("Defs", "Calibration"):
        d = src / dname
        if d.is_dir():
            shutil.copytree(d, project / dname, dirs_exist_ok=True)

    final_text, decls = assemble_final()
    root_decl = decls[root]
    goal_def = "Arena.GoalStatement"
    final_text += (
        "\n-- consolidation audit\n"
        f"theorem _consolidation_goal_check : {goal_def} := {root_decl}\n"
    )
    (project / "Final.lean").write_text(final_text, encoding="utf-8")

    lakefile = (
        'name = "consolidation"\n'
        'defaultTargets = ["Final"]\n\n'
        "[[lean_lib]]\nname = \"Defs\"\n\n"
        "[[lean_lib]]\nname = \"Goal\"\nneeds = [\"Defs\"]\n\n"
        "[[lean_lib]]\nname = \"Calibration\"\nneeds = [\"Defs\"]\n\n"
        "[[lean_lib]]\nname = \"Final\"\nneeds = [\"Defs\", \"Goal\"]\n"
    )
    (project / "lakefile.toml").write_text(lakefile, encoding="utf-8")
    return project, decls, root_decl
