# Contributing

The repository is meant to be cloned and run by other people, so every
change is checked the same way on every machine. Some of it is automatic,
some is not; this file says exactly which, and what a change must never
break.

## Before every push

```sh
.venv/bin/python -m pytest arena/tests -q
.venv/bin/python arena/smoke_oracle.py
```

Both must pass. A venv is per machine: create it from
`arena/requirements.txt` with Python 3.11 or newer, never copy one from
another account or computer.

## What runs automatically

GitHub Actions (`.github/workflows/tests.yml`), on every push and pull
request, on Python 3.11 and 3.12:

1. the unit suite `arena/tests`, including `test_portability.py`, which
   fails on any tracked text file carrying a home-directory path or a
   tracked build, venv or state tree;
2. the numeric-regime end-to-end smoke `arena/smoke_oracle.py`.

The unit suite includes tests that boot the daemon on the built-in black box
and drive one runner round with a scripted stand-in for the agent; they need
no agent CLI and no network.

## What you must run by hand

1. The theorem-regime end-to-end smoke, because it needs elan and a Lean
   toolchain (the first run fetches the toolchain and says so):

   ```sh
   .venv/bin/python arena/smoke_arena.py
   ```

   Run it before any release and after any change under `arena/daemon.py`,
   `arena/kernel_runner.py`, `arena/lean_workspace.py`, `arena/hygiene.py`,
   `arena/assembly.py` or `arena/dag.py`.
2. The evidence-kit demo after any change under `gauntlet/`:

   ```sh
   .venv/bin/python run_demo.py --instance example_ising --quick
   ```

   Commit the regenerated tables under `example_ising/out/` (their paths are
   rendered relative to the package; the portability test enforces that).
   The `report.json` and `report.jsonl` beside them are gitignored: they
   carry wall-clock timings.

## Invariants every change preserves

- The daemon stays the only writer of shared state and the only gateway to
  the ground truth; the oracle source stays sealed from contestants.
- Nothing certifies itself. Promotion paths, peer verification, the
  mechanical finish gate, the hygiene audit, provenance checks and generated
  tables are never bypassed "just this once".
- Logs are append-only. Corrections are superseding entries; a stale
  `SOLVED` marker is never deleted.
- Verification tables in write-ups are build artifacts, never hand-edited.
- A claim's ladder status moves up only with new evidence and down when its
  gate weakens.
- The tree is account-agnostic: no home-directory paths, user names, machine
  names or credential files in tracked files. Generated outputs render paths
  through `gauntlet/paths.py`. Every machine-specific value lives in config
  or environment.
- `.env`, `.venv/`, `state/`, `.lake/` and other build or state trees are
  never committed.
- Documentation comes in pairs: a recipe in `SETUP.md` has a command-form
  twin in `AGENTS.md`. Change both.
