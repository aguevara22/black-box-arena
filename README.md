# Black Box Arena

Autonomous multi-agent research under adversarial peer verification against a
sealed oracle. This repository ships the two-layer method in
[METHOD.md](METHOD.md): an adversarial discovery arena and a claim-coupled
evidence kit.

```text
arena/               daemon, client, promotion funnel, ops, tests, smoke retest
challenges/          exchangeable arena packages and the ising_lift demo
CONTESTANT.md        contestant tick and finish protocol
SETUP.md             installation and operating guide
gauntlet/            fixed evidence-kit machinery
example_ising/       worked evidence-kit instance
instance_template/   exchangeable evidence-kit package
run_demo.py          evidence-kit entry point
report/              method report source, PDF, and build file
```

Arena smoke quickstart:

```sh
python3 -m venv .venv
.venv/bin/pip install -r arena/requirements.txt
.venv/bin/python arena/smoke_arena.py
```

Evidence-kit quickstart:

```sh
python3 run_demo.py
```

See [SETUP.md](SETUP.md) for real contestant sessions and
[SHIPPING.md](SHIPPING.md) for the adoption contract.
