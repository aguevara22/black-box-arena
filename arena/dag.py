"""The proof DAG: shared decomposition state over the blackboard.

Nodes are goals/lemmas (a Lean declaration name + statement + informal
gloss); edges are decompositions. A decomposition is admitted only after its
skeleton — the parent proved from children declared as `sorry` — passes a
kernel check, so the DAG never contains an unchecked plan.

Event-sourced: every mutation is an appended event in shared/dag.jsonl and
the in-memory view is rebuilt by replay at daemon boot. The daemon (single
writer, single event loop) is the only mutator, so no locking beyond
StateManager's is needed.

Lease semantics: at most one live lease per node; claim by the holder renews;
expiry is LAZY — whenever the daemon touches a node and finds its lease
expired it appends lease_released{reason: expired} first. No timers: state
remains exactly reconstructible from the log.

Statuses:  open → claimed → sketch → proved → accepted
  sketch   = has an accepted decomposition (children spawned)
  proved   = a kernel-checked proof of this node's statement exists
             (possibly modulo stubbed children — recorded on the event)
  accepted = a peer (not the prover) confirmed statement fidelity
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

try:
    from .protocol import short_hash
    from .state_manager import StateManager
except ImportError:  # direct module execution
    from protocol import short_hash
    from state_manager import StateManager

VALID_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.']*$")

STATUS_ORDER = ("open", "claimed", "sketch", "proved", "accepted")


class DagError(ValueError):
    """Raised for invalid DAG mutations; the daemon maps these to HTTP 4xx."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def normalize_statement(statement: str) -> str:
    return " ".join(statement.split())


@dataclass
class Lease:
    holder: str
    lease_id: str
    expires_ts: float


@dataclass
class Node:
    node_id: str
    name: str
    statement: str
    gloss: str
    proposed_by: str
    status: str = "open"
    children: list[str] = field(default_factory=list)
    decomp_id: str | None = None
    lease: Lease | None = None
    proved_by: str | None = None
    proved_job: str | None = None
    source_sha256: str | None = None
    proved_modulo: list[str] = field(default_factory=list)
    accepted_by: str | None = None

    def public_view(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "name": self.name,
            "statement": self.statement,
            "gloss": self.gloss,
            "proposed_by": self.proposed_by,
            "status": self.status,
            "children": list(self.children),
            "lease": (
                {"holder": self.lease.holder, "expires_ts": self.lease.expires_ts}
                if self.lease
                else None
            ),
            "proved_by": self.proved_by,
            "proved_job": self.proved_job,
            "proved_modulo": list(self.proved_modulo),
            "accepted_by": self.accepted_by,
        }


class DagStore:
    def __init__(self, state: StateManager, challenge: str, lease_ttl: int):
        self.state = state
        self.challenge = challenge
        self.lease_ttl = lease_ttl
        self.nodes: dict[str, Node] = {}
        self.root_id: str | None = None
        self._decomp_proposals: dict[str, dict[str, Any]] = {}

    # ---- identity ----

    def node_id_for(self, statement: str) -> str:
        return "n_" + short_hash(
            {"challenge": self.challenge, "statement": normalize_statement(statement)}
        )

    # ---- replay ----

    def load(self) -> None:
        for rec in self.state.iter_dag_records():
            self._apply(rec)

    def _apply(self, rec: dict[str, Any]) -> None:
        event = rec.get("event")
        if event == "node_proposed":
            node = Node(
                node_id=rec["node_id"],
                name=rec.get("name", ""),
                statement=rec.get("statement", ""),
                gloss=rec.get("gloss", ""),
                proposed_by=rec.get("actor", ""),
            )
            self.nodes.setdefault(node.node_id, node)
            if rec.get("is_root"):
                self.root_id = node.node_id
        elif event == "decomposition_proposed":
            self._decomp_proposals[rec["decomp_id"]] = rec
        elif event == "decomposition_accepted":
            parent = self.nodes.get(rec.get("parent", ""))
            if parent is not None:
                parent.children = list(rec.get("child_node_ids", []))
                parent.decomp_id = rec.get("decomp_id")
                if parent.status in ("open", "claimed"):
                    parent.status = "sketch"
        elif event == "lease_claimed":
            node = self.nodes.get(rec.get("node_id", ""))
            if node is not None:
                node.lease = Lease(
                    holder=rec.get("contestant_id", ""),
                    lease_id=rec.get("lease_id", ""),
                    expires_ts=float(rec.get("expires_ts", 0)),
                )
                if node.status == "open":
                    node.status = "claimed"
        elif event == "lease_released":
            node = self.nodes.get(rec.get("node_id", ""))
            if node is not None:
                node.lease = None
                if node.status == "claimed":
                    node.status = "open"
        elif event == "status_changed":
            node = self.nodes.get(rec.get("node_id", ""))
            if node is not None and rec.get("to") == "proved":
                node.status = "proved"
                node.proved_by = rec.get("contestant_id")
                node.proved_job = rec.get("job_id")
                node.source_sha256 = rec.get("source_sha256")
                node.proved_modulo = list(rec.get("proved_modulo", []))
                node.lease = None
        elif event == "node_accepted":
            node = self.nodes.get(rec.get("node_id", ""))
            if node is not None:
                node.status = "accepted"
                node.accepted_by = rec.get("actor")

    # ---- lazy lease expiry ----

    async def _expire_lease_if_due(self, node: Node) -> None:
        if node.lease is not None and node.lease.expires_ts <= time.time():
            rec = {
                "event": "lease_released",
                "actor": "daemon",
                "node_id": node.node_id,
                "lease_id": node.lease.lease_id,
                "reason": "expired",
            }
            await self.state.append_dag_event(rec)
            self._apply(rec)

    async def _expire_all_due(self) -> None:
        for node in list(self.nodes.values()):
            await self._expire_lease_if_due(node)

    # ---- mutations (called by daemon handlers only) ----

    async def ensure_root(self, name: str, statement: str, gloss: str) -> Node:
        if self.root_id is not None:
            return self.nodes[self.root_id]
        node_id = self.node_id_for(statement)
        rec = {
            "event": "node_proposed",
            "actor": "daemon",
            "node_id": node_id,
            "name": name,
            "statement": statement,
            "gloss": gloss,
            "is_root": True,
        }
        await self.state.append_dag_event(rec)
        self._apply(rec)
        return self.nodes[node_id]

    async def propose_node(
        self, cid: str, name: str, statement: str, gloss: str
    ) -> tuple[Node, bool]:
        """Free-standing lemma proposal (not yet wired to a parent).
        Returns (node, created)."""
        if not VALID_NAME_RE.match(name):
            raise DagError(f"invalid declaration name {name!r}")
        if not statement.strip():
            raise DagError("statement must be nonempty")
        node_id = self.node_id_for(statement)
        if node_id in self.nodes:
            return self.nodes[node_id], False
        if any(n.name == name for n in self.nodes.values()):
            raise DagError(f"declaration name {name!r} already used by another node", 409)
        rec = {
            "event": "node_proposed",
            "actor": cid,
            "node_id": node_id,
            "name": name,
            "statement": statement,
            "gloss": gloss,
        }
        await self.state.append_dag_event(rec)
        self._apply(rec)
        return self.nodes[node_id], True

    async def propose_decomposition(
        self,
        cid: str,
        parent_id: str,
        children: list[dict[str, str]],
        skeleton_job: str,
    ) -> str:
        """Record a decomposition proposal (skeleton already submitted as a
        check job). Acceptance happens via accept_decomposition once the
        daemon confirms the skeleton job succeeded."""
        parent = self.nodes.get(parent_id)
        if parent is None:
            raise DagError(f"unknown parent node {parent_id!r}", 404)
        if parent.decomp_id is not None:
            raise DagError(f"parent {parent_id} already has an accepted decomposition", 409)
        if not children:
            raise DagError("decomposition requires at least one child")
        for child in children:
            if not isinstance(child, dict) or not child.get("name") or not child.get("statement"):
                raise DagError("each child needs 'name' and 'statement'")
            if not VALID_NAME_RE.match(child["name"]):
                raise DagError(f"invalid child declaration name {child['name']!r}")
        names = [c["name"] for c in children]
        if len(set(names)) != len(names):
            raise DagError("duplicate child names in decomposition")
        decomp_id = "d_" + short_hash(
            {"p": parent_id, "c": [(c["name"], c["statement"]) for c in children]}
        )
        rec = {
            "event": "decomposition_proposed",
            "actor": cid,
            "decomp_id": decomp_id,
            "parent": parent_id,
            "children": children,
            "skeleton_job": skeleton_job,
        }
        await self.state.append_dag_event(rec)
        self._apply(rec)
        return decomp_id

    async def accept_decomposition(self, decomp_id: str, checked_by_job: str) -> list[Node]:
        proposal = self._decomp_proposals.get(decomp_id)
        if proposal is None:
            raise DagError(f"unknown decomposition {decomp_id!r}", 404)
        parent_id = proposal["parent"]
        parent = self.nodes.get(parent_id)
        if parent is None:
            raise DagError(f"unknown parent node {parent_id!r}", 404)
        if parent.decomp_id is not None:
            raise DagError(f"parent {parent_id} already decomposed", 409)

        child_ids: list[str] = []
        new_children: list[dict[str, Any]] = []
        for child in proposal["children"]:
            cid_ = self.node_id_for(child["statement"])
            child_ids.append(cid_)
            if cid_ not in self.nodes:
                new_children.append(
                    {
                        "event": "node_proposed",
                        "actor": proposal.get("actor", "daemon"),
                        "node_id": cid_,
                        "name": child["name"],
                        "statement": child["statement"],
                        "gloss": child.get("gloss", ""),
                    }
                )
            elif self.nodes[cid_].name != child["name"]:
                raise DagError(
                    f"child statement already exists as node {cid_} under name "
                    f"{self.nodes[cid_].name!r}, not {child['name']!r}",
                    409,
                )

        # cycle check: no existing child may reach the parent
        for existing in child_ids:
            if existing in self.nodes and self._reaches(existing, parent_id):
                raise DagError(
                    f"decomposition would create a cycle via {existing}", 409
                )

        for rec in new_children:
            await self.state.append_dag_event(rec)
            self._apply(rec)
        rec = {
            "event": "decomposition_accepted",
            "actor": "daemon",
            "decomp_id": decomp_id,
            "parent": parent_id,
            "child_node_ids": child_ids,
            "checked_by_job": checked_by_job,
        }
        await self.state.append_dag_event(rec)
        self._apply(rec)
        return [self.nodes[c] for c in child_ids]

    def _reaches(self, start: str, target: str) -> bool:
        stack = [start]
        seen = set()
        while stack:
            cur = stack.pop()
            if cur == target:
                return True
            if cur in seen:
                continue
            seen.add(cur)
            node = self.nodes.get(cur)
            if node is not None:
                stack.extend(node.children)
        return False

    async def claim(self, node_id: str, cid: str) -> Lease:
        node = self.nodes.get(node_id)
        if node is None:
            raise DagError(f"unknown node {node_id!r}", 404)
        await self._expire_lease_if_due(node)
        if node.status in ("proved", "accepted"):
            raise DagError(f"node {node_id} is already {node.status}", 409)
        if node.lease is not None:
            if node.lease.holder == cid:
                # renewal
                rec = {
                    "event": "lease_claimed",
                    "actor": cid,
                    "node_id": node_id,
                    "contestant_id": cid,
                    "lease_id": node.lease.lease_id,
                    "ttl_seconds": self.lease_ttl,
                    "expires_ts": time.time() + self.lease_ttl,
                    "renewal": True,
                }
                await self.state.append_dag_event(rec)
                self._apply(rec)
                return node.lease
            raise DagError(
                f"node {node_id} is leased by {node.lease.holder} until "
                f"{node.lease.expires_ts:.0f}",
                409,
            )
        lease_id = "L_" + short_hash({"n": node_id, "c": cid, "t": time.time()})
        rec = {
            "event": "lease_claimed",
            "actor": cid,
            "node_id": node_id,
            "contestant_id": cid,
            "lease_id": lease_id,
            "ttl_seconds": self.lease_ttl,
            "expires_ts": time.time() + self.lease_ttl,
        }
        await self.state.append_dag_event(rec)
        self._apply(rec)
        assert node.lease is not None
        return node.lease

    async def release(self, node_id: str, cid: str) -> None:
        node = self.nodes.get(node_id)
        if node is None:
            raise DagError(f"unknown node {node_id!r}", 404)
        if node.lease is None:
            raise DagError(f"node {node_id} has no live lease", 409)
        if node.lease.holder != cid:
            raise DagError(f"lease on {node_id} is held by {node.lease.holder}", 409)
        rec = {
            "event": "lease_released",
            "actor": cid,
            "node_id": node_id,
            "lease_id": node.lease.lease_id,
            "reason": "released",
        }
        await self.state.append_dag_event(rec)
        self._apply(rec)

    async def mark_proved(
        self,
        node_id: str,
        cid: str,
        job_id: str,
        source_sha256: str,
        proved_modulo: list[str],
    ) -> Node:
        node = self.nodes.get(node_id)
        if node is None:
            raise DagError(f"unknown node {node_id!r}", 404)
        rec = {
            "event": "status_changed",
            "actor": cid,
            "node_id": node_id,
            "contestant_id": cid,
            "from": node.status,
            "to": "proved",
            "job_id": job_id,
            "source_sha256": source_sha256,
            "proved_modulo": proved_modulo,
        }
        await self.state.append_dag_event(rec)
        self._apply(rec)
        return node

    async def accept_node(self, node_id: str, cid: str, reason: str) -> Node:
        node = self.nodes.get(node_id)
        if node is None:
            raise DagError(f"unknown node {node_id!r}", 404)
        if node.status != "proved":
            raise DagError(f"node {node_id} is {node.status}, not proved", 409)
        if node.proved_by == cid:
            raise DagError("a node must be accepted by a contestant other than its prover", 409)
        rec = {
            "event": "node_accepted",
            "actor": cid,
            "node_id": node_id,
            "reason": reason,
        }
        await self.state.append_dag_event(rec)
        self._apply(rec)
        return node

    # ---- queries ----

    async def frontier(self) -> list[Node]:
        """Open, unleased nodes — the claimable work."""
        await self._expire_all_due()
        return [
            n
            for n in self.nodes.values()
            if n.status in ("open", "sketch") and n.lease is None and not self._is_settled(n)
        ]

    def _is_settled(self, node: Node) -> bool:
        return node.status in ("proved", "accepted")

    def dependency_closure(self, node_id: str) -> list[Node]:
        """Topologically ordered (dependencies first) closure of a node's
        children — the context a proof of this node compiles against."""
        order: list[Node] = []
        seen: set[str] = set()

        def visit(nid: str) -> None:
            if nid in seen:
                return
            seen.add(nid)
            node = self.nodes.get(nid)
            if node is None:
                return
            for child in node.children:
                visit(child)
            order.append(node)

        node = self.nodes.get(node_id)
        if node is None:
            raise DagError(f"unknown node {node_id!r}", 404)
        for child in node.children:
            visit(child)
        return order

    def public_view(self, *, frontier_only: bool = False) -> dict[str, Any]:
        return {
            "root": self.root_id,
            "nodes": [n.public_view() for n in self.nodes.values()],
        }
