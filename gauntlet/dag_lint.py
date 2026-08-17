"""Structural lint over an exported arena proof DAG.

The Lean-instance analog of independence.py: mechanical backstops on the
solved run's shared/dag.jsonl and proofs/index.jsonl —

  - the DAG is acyclic;
  - every proved/accepted node's stored source hash matches the hash
    recorded on its status_changed event (no silent mutation after proof);
  - no node is `accepted` without being proved;
  - every accepted node was accepted by someone other than its prover;
  - the root reaches only proved/accepted nodes (no dangling stubs behind
    a claimed-solved run).

Pure functions returning problem strings; the caller (runner's meta gate or
an instance gate) turns them into checks. Stdlib-only.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def _iter_jsonl(path: Path):
    if not path.exists():
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue


def lint(state_dir: Path) -> list[str]:
    """Lint state/<challenge>/ (shared/dag.jsonl + proofs/). Returns every
    problem found, empty when clean."""
    problems: list[str] = []
    dag_path = state_dir / "shared" / "dag.jsonl"
    proofs_dir = state_dir / "proofs"

    nodes: dict[str, dict] = {}
    children: dict[str, list[str]] = {}
    root: str | None = None
    proved_sha: dict[str, str] = {}
    proved_by: dict[str, str] = {}
    accepted_by: dict[str, str] = {}

    for rec in _iter_jsonl(dag_path):
        event = rec.get("event")
        if event == "node_proposed":
            nodes[rec["node_id"]] = rec
            if rec.get("is_root"):
                root = rec["node_id"]
        elif event == "decomposition_accepted":
            children[rec.get("parent", "")] = list(rec.get("child_node_ids", []))
        elif event == "status_changed" and rec.get("to") == "proved":
            proved_sha[rec["node_id"]] = rec.get("source_sha256", "")
            proved_by[rec["node_id"]] = rec.get("contestant_id", "")
        elif event == "node_accepted":
            accepted_by[rec["node_id"]] = rec.get("actor", "")

    # acyclicity
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {nid: WHITE for nid in nodes}

    def visit(nid: str, stack: list[str]) -> None:
        color[nid] = GRAY
        for child in children.get(nid, []):
            if child not in nodes:
                problems.append(f"decomposition of {nid} references unknown node {child}")
                continue
            if color[child] == GRAY:
                problems.append(f"cycle: {' -> '.join(stack + [nid, child])}")
            elif color[child] == WHITE:
                visit(child, stack + [nid])
        color[nid] = BLACK

    for nid in nodes:
        if color[nid] == WHITE:
            visit(nid, [])

    # stored-source integrity
    for nid, sha in proved_sha.items():
        src = proofs_dir / f"{nid}.lean"
        if not src.exists():
            problems.append(f"proved node {nid} has no stored source {src.name}")
            continue
        actual = hashlib.sha256(src.read_bytes()).hexdigest()
        if sha and actual != sha:
            problems.append(
                f"proved node {nid}: stored source hash {actual[:16]} != "
                f"recorded {sha[:16]} (mutated after proof)"
            )

    # acceptance discipline
    for nid, acceptor in accepted_by.items():
        if nid not in proved_sha:
            problems.append(f"node {nid} accepted without a proved event")
        elif proved_by.get(nid) == acceptor:
            problems.append(f"node {nid} accepted by its own prover {acceptor}")

    # root closure fully proved
    if root is not None:
        stack = [root]
        seen: set[str] = set()
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            if cur not in proved_sha:
                problems.append(f"root closure contains unproved node {cur}")
            stack.extend(children.get(cur, []))
    else:
        problems.append("no root node recorded in dag.jsonl")

    return problems
