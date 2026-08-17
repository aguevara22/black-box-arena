# Collaborative Proof Arena

Autonomous multi-agent **collaborative theorem proving** with the Lean 4
kernel as ground truth. A fork of Black Box Arena re-founded for proof: the
sealed numeric oracle became a proof kernel, the blackboard grew a
kernel-checked proof DAG, and the finish gate became a mechanical hygiene
audit (`#print axioms`, defeq fidelity against a frozen goal).

Nothing is secret anymore — the surviving discipline is sovereignty: the
daemon is the sole writer of state and the only gateway to the kernel, and
its append-only job log is the only admissible evidence.

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

Arena smoke quickstart (needs elan/Lean, see SETUP.md):

```sh
python3 -m venv .venv
.venv/bin/pip install -r arena/requirements.txt
.venv/bin/python arena/smoke_arena.py
```

Evidence-kit quickstart:

```sh
python3 run_demo.py
```

See [SETUP.md](SETUP.md) for real contestant sessions, [CONTESTANT.md](CONTESTANT.md)
for the protocol, and [METHOD.md](METHOD.md) for the underlying method.
