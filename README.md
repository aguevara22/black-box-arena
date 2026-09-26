# Collaborative Proof Arena

Autonomous multi-agent research under adversarial peer verification, with
**one daemon and two ground truths**, chosen per challenge by
`ground_truth:` in `config.yaml`:

- **`kernel`** — collaborative theorem proving with the Lean 4 kernel: a
  kernel-checked proof DAG, a mechanical hygiene audit (`#print axioms`,
  defeq fidelity against a frozen goal), peer verification of statement
  fidelity.
- **`oracle`** — identification and construction against a **sealed numeric
  oracle**: the challenge ships `oracle.py :: query(payload)`, which only the
  daemon's worker subprocess ever imports; contestants get answers, each
  correlated by request id and input hash; predict-before-query promotes
  hypotheses; a mechanical evidence-matrix gate filters thin finishes.

The discipline is the same in both: the daemon is the sole writer of state
and the only gateway to the ground truth, and its append-only log is the
only admissible evidence. Nothing certifies itself.

```text
arena/               daemon, kernel runner, proof DAG, client, ops, tests
challenges/          exchangeable challenge packages (frozen Defs + Goal)
CONTESTANT.md        contestant tick, DAG, and finish protocol
SETUP.md             installation and operating guide
gauntlet/            fixed evidence-kit machinery (Layer B)
example_ising/       worked evidence-kit instance (legacy, numeric)
instance_template/   exchangeable evidence-kit package
run_demo.py          evidence-kit entry point
report/              method report source for the original method
```

Requirements: Python 3.11 or newer (every entry point refuses an older
interpreter with one line saying so); elan, only for the kernel regime
(see [SETUP.md](SETUP.md)). The working branch is `collaborative-proof`,
the repository default; `main` (the Lean re-founding) and `master` (Black
Box Arena v0.2) are frozen history.

Quickstart, oracle regime (no Lean toolchain needed):

```sh
python3.11 -m venv .venv                         # any Python >= 3.11
.venv/bin/pip install -r arena/requirements.txt
.venv/bin/python arena/smoke_oracle.py          # scripted two-contestant run on challenges/ising_lift
.venv/bin/python arena/daemon.py --challenge ising_lift   # then use arena/client.py oracle ...
```

Quickstart, kernel regime (needs elan/Lean, see SETUP.md):

```sh
.venv/bin/python arena/smoke_arena.py           # scripted run on challenges/smoke_min
.venv/bin/python arena/daemon.py --challenge smoke_min
```

Evidence-kit quickstart:

```sh
.venv/bin/python run_demo.py
```

See [SETUP.md](SETUP.md) for real contestant sessions, [CONTESTANT.md](CONTESTANT.md)
for the protocol, and [METHOD.md](METHOD.md) for the underlying method.

## Verify before you push

The repo is meant to be cloned and run by other people, so every change is
checked the same way on every machine. Some of it is automatic, some is not;
this section says exactly which.

**Automatic (GitHub Actions, `.github/workflows/tests.yml`, on every push and
pull request):**

1. the unit suite `arena/tests` on Python 3.11 and 3.12 — including
   `test_portability.py`, which fails on any tracked text file carrying a home
   directory path (a macOS or Linux user-home prefix) or a tracked build, venv
   or state tree;
2. the oracle-regime end-to-end smoke `arena/smoke_oracle.py`.

**Manual — you must run these yourself, CI does not:**

1. the kernel-regime end-to-end smoke, because it needs elan and a Lean
   toolchain. On a machine that lacks the toolchain pinned by
   `challenges/smoke_min/lean-toolchain`, the smoke says so, fetches it once
   with elan (minutes, no deadline) and only then starts the daemon's
   120-second clock:

   ```sh
   .venv/bin/python arena/smoke_arena.py
   ```

   Run it before any release and after any change under `arena/daemon.py`,
   `arena/kernel_runner.py`, `arena/lean_workspace.py`, `arena/hygiene.py`,
   `arena/assembly.py` or `arena/dag.py`.
2. the evidence-kit demo after any change under `gauntlet/`:

   ```sh
   .venv/bin/python run_demo.py --instance example_ising --quick
   ```

   and commit the regenerated tables under `example_ising/out/` (their paths
   are rendered relative to the package; the portability test enforces that).
   The `report.json` and `report.jsonl` beside them are gitignored: they carry
   wall-clock timings and change on every run.

**Before every push, locally:**

```sh
.venv/bin/python -m pytest arena/tests -q
.venv/bin/python arena/smoke_oracle.py
```

Both must pass. A venv is per machine: create it from `arena/requirements.txt`,
never copy one from another account or computer.
