# SHIPPING — adopting the method on a new problem

Version 0.2 (2026-07-24). What's in the box, how to run the arena on your own
problem, and what is deliberately out of scope.

## What's in the box

- `METHOD.md` — the two-layer method specification (read first).
- `arena/` — the multi-agent research arena: daemon (sole state writer +
  sealed-oracle gateway), stdlib-only contestant client, state manager,
  three-path breakthrough promotion, finish evidence gate, advisor with a
  fail-closed spend cap, non-directive historian, read-only dashboard, ops
  scripts, tests.
- `challenges/` — challenge packages: `_template/` (copy me) and
  `ising_lift/` (worked demo: sealed classical coefficient over spin systems;
  contestants must construct the polynomial lift whose extraction reproduces
  it).
- `CONTESTANT.md` — the problem-agnostic contestant tick protocol.
- `SETUP.md` — venv, keys, daemon boot, contestant shells, ops.
- `gauntlet/` + `example_ising/` + `instance_template/` + `run_demo.py` —
  the evidence kit (Layer B): claim manifests, gates, ladder, provenance,
  independence lint, generated verification tables. `example_ising` is the
  consolidation-side counterpart of the `ising_lift` challenge.
- `report/` — the method report (LaTeX + PDF) with the arena pipeline figure.

## Run the demo (no API keys needed)

```sh
python3 -m venv .venv && .venv/bin/pip install -r arena/requirements.txt
.venv/bin/python arena/daemon.py --challenge smoke_min          # shell 1
python3 arena/client.py health                                    # shell 2
python3 arena/smoke_arena.py          # scripted two-contestant end-to-end run
python3 run_demo.py                   # evidence-kit suite (stdlib only)
```

`smoke_arena.py` drives two scripted contestants through the full funnel —
oracle queries (with a predict-before-query promotion), findings,
breakthrough promotion by cross-confirmation, a mechanically gated finish,
peer verification, SOLVED — against a throwaway state directory, and asserts
the state layout afterward. It is the arena's integration test, not a
substitute for real LLM contestants.

## Run it for real

Real runs put an LLM agent in each contestant seat (different vendors), each
looping the tick in `CONTESTANT.md` (`client.py` is their only interface),
with advisor keys in `.env` and the daemon under `ops/` supervision. See
`SETUP.md`.

## Adopting your own problem

1. **Author the challenge** (Layer A): copy `challenges/_template/` →
   `challenges/<name>/`; write `problem.md` (task, calibration rows with a
   symmetry leak, finish criteria), `config.yaml` (oracle schemas, promotion
   policy, finish-gate row count + coverage tags), `oracle.py`
   (`query(payload)`, sealed — return an explicit wall status off the
   generic locus). Keep the answer key out of every open file.
2. **Repo hygiene**: `git init`; `state/` and `.venv/` stay untracked
   (`.gitignore` ships); backups and scratch outside the tree.
3. **Boot and contest**: daemon + two cross-vendor contestant sessions.
4. **Consolidate** (Layer B): after SOLVED, port the accepted algorithm into
   an evidence-kit instance (copy `instance_template/`, fill the TODOs, run
   `run_demo.py --instance <name>` until green); promote claims up the
   ladder; generate the verification tables into your writeup.

## Deliberately out of scope in v0.2 (roadmap)

- **Contestant runners** — the loop that re-invokes each LLM agent per tick
  is operator-managed (any agent CLI works); a bundled runner is roadmap.
- **Parallel gate execution** in the evidence kit (sequential runner today).
- **Cloud offload hooks** — budgets are policy data; routing a heavy sweep to
  a VM is currently the operator's call.
- **Formal proofs** — `proved` rungs cite human-checked proofs (`proof_ref`);
  proof-assistant integration is out of scope.
- **CI integration** — gates expose exit codes and JSONL; wrapping them is
  trivial if a CI insists.

## Invariants to preserve when extending

- The daemon stays the only writer of shared state, and the oracle source
  stays sealed from contestants.
- No self-certification anywhere: promotion paths, peer verification, the
  mechanical finish gate, provenance checks, and generated tables must not
  be bypassed "just this once."
- Logs stay append-only; corrections are superseding entries; stale SOLVED
  markers are never deleted.
- Verification tables in writeups are build artifacts — never hand-edited.
- A claim's ladder status moves up only with new evidence and down when its
  gate weakens.
