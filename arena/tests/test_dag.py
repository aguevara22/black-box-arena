from __future__ import annotations

import asyncio
import importlib

import pytest


@pytest.fixture
def arena_modules(tmp_path, monkeypatch):
    paths_module = None
    state_manager_module = None
    dag_module = None
    assembly_module = None
    try:
        with monkeypatch.context() as environment:
            environment.setenv("ORACLE_STATE_ROOT", str(tmp_path))

            from arena import assembly as imported_assembly
            from arena import dag as imported_dag
            from arena import state_manager as imported_state_manager
            from arena.utils import paths as imported_paths

            paths_module = importlib.reload(imported_paths)
            state_manager_module = importlib.reload(imported_state_manager)
            dag_module = importlib.reload(imported_dag)
            assembly_module = importlib.reload(imported_assembly)

            yield state_manager_module, dag_module, assembly_module
    finally:
        if paths_module is not None:
            importlib.reload(paths_module)
        if state_manager_module is not None:
            importlib.reload(state_manager_module)
        if dag_module is not None:
            importlib.reload(dag_module)
        if assembly_module is not None:
            importlib.reload(assembly_module)


def test_propose_node_deduplicates_and_validates(arena_modules) -> None:
    state_manager, dag_module, _ = arena_modules

    async def scenario() -> None:
        state = state_manager.StateManager("smoke_min")
        dag = dag_module.DagStore(state, "smoke_min", lease_ttl=60)

        first, created = await dag.propose_node(
            "claude", "normalized", "  P   ∧\n Q  ", "first spelling"
        )
        duplicate, duplicate_created = await dag.propose_node(
            "codex", "normalized", "P ∧ Q", "normalized spelling"
        )

        assert created is True
        assert duplicate_created is False
        assert duplicate is first
        assert duplicate.node_id == first.node_id

        with pytest.raises(dag_module.DagError) as collision:
            await dag.propose_node(
                "codex", "normalized", "A different proposition", "collision"
            )
        assert collision.value.status == 409

        with pytest.raises(dag_module.DagError):
            await dag.propose_node(
                "claude", "not-a-lean-name", "Another proposition", "invalid"
            )

    asyncio.run(scenario())


def test_decomposition_acceptance_conflicts_and_cycles(arena_modules) -> None:
    state_manager, dag_module, _ = arena_modules

    async def scenario() -> None:
        state = state_manager.StateManager("smoke_min")
        dag = dag_module.DagStore(state, "smoke_min", lease_ttl=60)

        parent, _ = await dag.propose_node(
            "claude", "parent_goal", "Parent proposition", "parent"
        )
        decomp_id = await dag.propose_decomposition(
            "claude",
            parent.node_id,
            [
                {"name": "child_one", "statement": "Child one", "gloss": "one"},
                {"name": "child_two", "statement": "Child two", "gloss": "two"},
            ],
            "J-skeleton",
        )
        children = await dag.accept_decomposition(decomp_id, "J-skeleton")

        assert parent.status == "sketch"
        assert parent.decomp_id == decomp_id
        assert parent.children == [child.node_id for child in children]

        with pytest.raises(dag_module.DagError) as second:
            await dag.propose_decomposition(
                "codex",
                parent.node_id,
                [{"name": "third_child", "statement": "Child three"}],
                "J-second",
            )
        assert second.value.status == 409

        node_a, _ = await dag.propose_node("claude", "cycle_a", "Cycle A", "A")
        node_b, _ = await dag.propose_node("claude", "cycle_b", "Cycle B", "B")
        a_to_b = await dag.propose_decomposition(
            "claude",
            node_a.node_id,
            [{"name": node_b.name, "statement": node_b.statement}],
            "J-a-to-b",
        )
        await dag.accept_decomposition(a_to_b, "J-a-to-b")

        b_to_a = await dag.propose_decomposition(
            "codex",
            node_b.node_id,
            [{"name": node_a.name, "statement": node_a.statement}],
            "J-b-to-a",
        )
        with pytest.raises(dag_module.DagError) as cycle:
            await dag.accept_decomposition(b_to_a, "J-b-to-a")
        assert cycle.value.status == 409
        assert "cycle" in str(cycle.value).lower()

    asyncio.run(scenario())


def test_lease_conflicts_renewal_expiry_release_and_proof(arena_modules) -> None:
    state_manager, dag_module, _ = arena_modules

    async def scenario() -> None:
        state = state_manager.StateManager("smoke_min")
        dag = dag_module.DagStore(state, "smoke_min", lease_ttl=60)
        node, _ = await dag.propose_node(
            "claude", "leased_node", "Leased proposition", "leased"
        )

        first = await dag.claim(node.node_id, "claude")
        first_expiry = first.expires_ts
        with pytest.raises(dag_module.DagError) as foreign_claim:
            await dag.claim(node.node_id, "codex")
        assert foreign_claim.value.status == 409

        await asyncio.sleep(0.001)
        renewed = await dag.claim(node.node_id, "claude")
        assert renewed.lease_id == first.lease_id
        assert renewed.expires_ts > first_expiry

        with pytest.raises(dag_module.DagError) as foreign_release:
            await dag.release(node.node_id, "codex")
        assert foreign_release.value.status == 409

        await dag.mark_proved(
            node.node_id,
            "claude",
            "J-proof",
            "source-sha",
            proved_modulo=[],
        )
        assert node.status == "proved"
        assert node.lease is None

        expiring = dag_module.DagStore(state, "smoke_min", lease_ttl=0)
        expiring_node, _ = await expiring.propose_node(
            "claude", "expiring_node", "Expiring proposition", "expires"
        )
        expired_lease = await expiring.claim(expiring_node.node_id, "claude")
        replacement = await expiring.claim(expiring_node.node_id, "codex")

        assert replacement.holder == "codex"
        records = list(state.iter_dag_records())
        expired_index = next(
            index
            for index, record in enumerate(records)
            if record.get("event") == "lease_released"
            and record.get("node_id") == expiring_node.node_id
            and record.get("lease_id") == expired_lease.lease_id
            and record.get("reason") == "expired"
        )
        replacement_index = next(
            index
            for index, record in enumerate(records)
            if record.get("event") == "lease_claimed"
            and record.get("lease_id") == replacement.lease_id
        )
        assert expired_index < replacement_index

    asyncio.run(scenario())


def test_accept_node_requires_proof_and_peer(arena_modules) -> None:
    state_manager, dag_module, _ = arena_modules

    async def scenario() -> None:
        state = state_manager.StateManager("smoke_min")
        dag = dag_module.DagStore(state, "smoke_min", lease_ttl=60)
        node, _ = await dag.propose_node(
            "claude", "accepted_node", "Accepted proposition", "accepted"
        )

        with pytest.raises(dag_module.DagError) as unproved:
            await dag.accept_node(node.node_id, "codex", "not proved")
        assert unproved.value.status == 409

        await dag.mark_proved(
            node.node_id,
            "claude",
            "J-proof",
            "source-sha",
            proved_modulo=[],
        )
        with pytest.raises(dag_module.DagError) as self_accept:
            await dag.accept_node(node.node_id, "claude", "my own proof")
        assert self_accept.value.status == 409

        accepted = await dag.accept_node(node.node_id, "codex", "peer checked")
        assert accepted.status == "accepted"
        assert accepted.accepted_by == "codex"

    asyncio.run(scenario())


def test_replay_restores_statuses_children_and_leases(arena_modules) -> None:
    state_manager, dag_module, _ = arena_modules

    def signature(dag):
        return {
            node_id: {
                "status": node.status,
                "children": list(node.children),
                "lease": (
                    None
                    if node.lease is None
                    else (
                        node.lease.holder,
                        node.lease.lease_id,
                        node.lease.expires_ts,
                    )
                ),
            }
            for node_id, node in dag.nodes.items()
        }

    async def scenario() -> None:
        state = state_manager.StateManager("smoke_min")
        original = dag_module.DagStore(state, "smoke_min", lease_ttl=3600)
        root = await original.ensure_root("replay_root", "Replay root", "root")
        decomp_id = await original.propose_decomposition(
            "claude",
            root.node_id,
            [
                {"name": "replay_claimed", "statement": "Replay claimed"},
                {"name": "replay_accepted", "statement": "Replay accepted"},
            ],
            "J-replay-skeleton",
        )
        claimed, accepted = await original.accept_decomposition(
            decomp_id, "J-replay-skeleton"
        )
        await original.claim(claimed.node_id, "claude")
        await original.mark_proved(
            accepted.node_id,
            "codex",
            "J-replay-proof",
            "replay-source-sha",
            proved_modulo=[],
        )
        await original.accept_node(accepted.node_id, "claude", "peer replay check")

        replayed = dag_module.DagStore(state, "smoke_min", lease_ttl=3600)
        replayed.load()

        assert replayed.root_id == original.root_id
        assert signature(replayed) == signature(original)

    asyncio.run(scenario())


def test_split_imports_and_compose_proof_file(arena_modules) -> None:
    state_manager, dag_module, assembly = arena_modules

    imports, body = assembly.split_imports(
        "import Header.One\n"
        "-- header comment\n"
        "\n"
        "import Header.Two\n"
        "theorem first_decl : True := True.intro\n"
        "import Body.Late\n"
    )
    assert imports == ["import Header.One", "import Header.Two"]
    assert body.startswith("theorem first_decl")
    assert "import Body.Late" in body
    assert "import Body.Late" not in imports

    async def scenario() -> None:
        state = state_manager.StateManager("smoke_min")
        dag = dag_module.DagStore(state, "smoke_min", lease_ttl=60)
        parent, _ = await dag.propose_node(
            "claude", "assembly_parent", "Assembly parent", "parent"
        )
        decomp_id = await dag.propose_decomposition(
            "claude",
            parent.node_id,
            [
                {"name": "proved_child", "statement": "Proved child"},
                {"name": "unproved_child", "statement": "Unproved child"},
            ],
            "J-assembly-skeleton",
        )
        proved, unproved = await dag.accept_decomposition(
            decomp_id, "J-assembly-skeleton"
        )
        proved_source = (
            "import Shared.Base\n"
            "import Shared.Base\n"
            "import Child.Tools\n"
            "\n"
            "theorem proved_child : Proved child := by exact proof_marker\n"
        )
        await state.store_proof_source(proved.node_id, proved_source, "J-child-proof")
        await dag.mark_proved(
            proved.node_id,
            "claude",
            "J-child-proof",
            "proved-source-sha",
            proved_modulo=[],
        )

        text, stubbed = assembly.compose_proof_file(
            dag,
            state,
            parent.node_id,
            (
                "import Shared.Base\n"
                "import Parent.Tools\n"
                "\n"
                "theorem assembly_parent : Assembly parent := by exact parent_marker\n"
            ),
        )

        assert stubbed == [unproved.node_id]
        assert text.count("theorem proved_child : Proved child") == 1
        assert text.count("import Shared.Base") == 1
        assert text.count("import Child.Tools") == 1
        assert text.count("import Parent.Tools") == 1
        assert text.index("import Parent.Tools") < text.index("-- arena node:")
        assert assembly.STUB_BANNER in text
        assert f"theorem {unproved.name} : {unproved.statement} := sorry" in text

    asyncio.run(scenario())
