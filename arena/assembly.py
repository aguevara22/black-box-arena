"""Source assembly: compile contexts for node proofs, and the final build.

A node's proof compiles against its dependency closure: the topologically
ordered sources of its children — the real proof source where a child is
proved, a `sorry` stub where it is not (the proof is then "proved modulo"
those stubs). The final assembly is the same construction at the root with
stubs forbidden, plus the defeq check against the frozen goal Prop.

Lean requires all imports to precede all declarations, so concatenation
hoists the union of import lines to the top.
"""
from __future__ import annotations

import re
from typing import Any

try:
    from .dag import DagStore, Node
    from .state_manager import StateManager
except ImportError:  # direct module execution
    from dag import DagStore, Node
    from state_manager import StateManager

_IMPORT_LINE_RE = re.compile(r"^\s*import\s+\S+\s*$")

STUB_BANNER = "-- arena stub (dependency not yet proved)"
SECTION_BANNER = "-- arena node: {name} [{node_id}] ({how})"


def split_imports(source: str) -> tuple[list[str], str]:
    """Split a Lean source into (import lines, body). Import lines may only
    appear at the top of a file; anything after the first non-import,
    non-blank, non-comment-only line is body."""
    imports: list[str] = []
    body_lines: list[str] = []
    in_header = True
    for line in source.splitlines():
        if in_header:
            if _IMPORT_LINE_RE.match(line):
                imports.append(line.strip())
                continue
            if not line.strip() or line.lstrip().startswith("--"):
                continue
            in_header = False
        body_lines.append(line)
    return imports, "\n".join(body_lines)


def stub_decl(node: Node) -> str:
    return f"{STUB_BANNER}\ntheorem {node.name} : {node.statement} := sorry"


def build_proof_context(
    dag: DagStore, state: StateManager, node_id: str
) -> tuple[str, list[str], list[str]]:
    """Context a proof of `node_id` compiles after.

    Returns (context_text, import_lines, stubbed_node_ids). The context
    contains, in dependency order, each closure node's proved source body or
    a sorry stub. The caller appends the contestant's own source body after
    this context (with its imports merged)."""
    closure = dag.dependency_closure(node_id)
    imports: list[str] = []
    seen_imports: set[str] = set()
    sections: list[str] = []
    stubbed: list[str] = []
    for dep in closure:
        source = state.proof_source(dep.node_id) if dep.status in ("proved", "accepted") else None
        if source is None:
            stubbed.append(dep.node_id)
            sections.append(
                SECTION_BANNER.format(name=dep.name, node_id=dep.node_id, how="stub")
            )
            sections.append(stub_decl(dep))
        else:
            dep_imports, body = split_imports(source)
            for imp in dep_imports:
                if imp not in seen_imports:
                    seen_imports.add(imp)
                    imports.append(imp)
            sections.append(
                SECTION_BANNER.format(name=dep.name, node_id=dep.node_id, how="proved")
            )
            sections.append(body.strip())
    return "\n\n".join(sections), imports, stubbed


def compose_proof_file(
    dag: DagStore,
    state: StateManager,
    node_id: str,
    contestant_source: str,
) -> tuple[str, list[str]]:
    """Full file for a proof job on `node_id`: hoisted imports, dependency
    context, then the contestant's body. Returns (file_text, stubbed_ids)."""
    context, ctx_imports, stubbed = build_proof_context(dag, state, node_id)
    own_imports, own_body = split_imports(contestant_source)
    imports: list[str] = []
    seen: set[str] = set()
    for imp in [*ctx_imports, *own_imports]:
        if imp not in seen:
            seen.add(imp)
            imports.append(imp)
    parts = []
    if imports:
        parts.append("\n".join(imports))
    if context:
        parts.append(context)
    parts.append(own_body.strip())
    return "\n\n".join(parts) + "\n", stubbed


def build_final_assembly(
    dag: DagStore, state: StateManager
) -> tuple[str, list[str]]:
    """The strict final build: dependency-ordered proved sources for the
    root's closure plus the root itself. The kernel runner appends the audit
    trailer (defeq fidelity against the frozen goal + `#print axioms`), so
    this returns the bare concatenation.

    Returns (file_text, missing_node_ids) — nonempty missing means the DAG
    is not finishable yet."""
    if dag.root_id is None:
        return "", ["<no root>"]
    root = dag.nodes[dag.root_id]
    closure: list[Node] = dag.dependency_closure(dag.root_id) + [root]
    imports: list[str] = []
    seen_imports: set[str] = set()
    sections: list[str] = []
    missing: list[str] = []
    for dep in closure:
        source = state.proof_source(dep.node_id)
        if source is None or dep.status not in ("proved", "accepted"):
            missing.append(dep.node_id)
            continue
        dep_imports, body = split_imports(source)
        for imp in dep_imports:
            if imp not in seen_imports:
                seen_imports.add(imp)
                imports.append(imp)
        sections.append(
            SECTION_BANNER.format(name=dep.name, node_id=dep.node_id, how="proved")
        )
        sections.append(body.strip())
    if missing:
        return "", missing
    if "import Goal" not in seen_imports:
        imports.append("import Goal")  # the fidelity trailer references the goal def
    text = "\n".join(imports) + "\n\n" + "\n\n".join(sections) + "\n"
    return text, []
