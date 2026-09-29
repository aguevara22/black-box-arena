# Black Box Arena

Two AI agents attack one hard problem. A referee they cannot argue with
decides. Every claim leaves a log entry a third party can replay.

Version 0.3 (in development).

## The idea

Autonomous agents are good at producing claims and bad at knowing which
ones are true. This arena separates the two jobs. Two contestant agents,
ideally from different vendors, work the same problem in their own sessions.
A daemon sits between them and the ground truth. For a theorem, the ground
truth is the Lean 4 kernel checking proofs against a frozen goal. For a
numeric problem, it is a sealed black box that only the daemon may call. A
contestant's claim counts only if it cites an entry in the daemon's
append-only log, and a finish counts only after the other contestant has
verified it. Nothing certifies itself: not the agents, not their advisors,
not the operator.

What you get at the end is not a transcript but a record: what was asked,
what the kernel or the black box answered, who proposed what, who checked
it, and in what order.

## Who it is for

- Researchers who want agents on a real open problem with an audit trail.
- Anyone with a Lean 4 goal to prove, or a computable function to identify
  or reconstruct from queries.
- People studying how autonomous agents behave under adversarial
  verification.

## Five-minute demo

You need Python 3.11 or newer and nothing else for this part.

```sh
git clone <this repository>
cd black-box-arena
python3.11 -m venv .venv                         # any Python >= 3.11
.venv/bin/pip install -r arena/requirements.txt
.venv/bin/python arena/smoke_oracle.py
```

The last command boots the daemon on the built-in numeric challenge and
drives two scripted contestants through a whole contest: sealed queries with
predictions registered beforehand, findings, a breakthrough promoted by
cross-confirmation, a finish stopped by the evidence gate, a finish that
passes, peer verification, `SOLVED`. It prints each step and ends with
`ORACLE-SMOKE: ALL OK`.

To touch the same daemon by hand:

```sh
.venv/bin/python arena/daemon.py --challenge ising_lift          # shell 1
python3 arena/client.py health                                    # shell 2
python3 arena/client.py problem
python3 arena/client.py oracle --contestant-id claude \
  --input '{"n":2,"edges":[],"fields":[1,2]}' \
  --predict '{"status":"ok","coefficient":0}' --hypothesis "fields alone give 0"
```

The theorem side needs the Lean toolchain manager elan, which
[SETUP.md](SETUP.md) walks through; its demo is
`.venv/bin/python arena/smoke_arena.py`. The first run fetches a Lean
toolchain and says so.

## Bring your own problem

A problem is a folder under `challenges/`. Two kinds:

- **A theorem.** Frozen definitions, a goal stated as a Lean `Prop`, and
  calibration examples that must compile before the daemon serves. Copy
  `challenges/smoke_min` and edit.
- **A numeric black box.** A `query(payload)` function only the daemon's
  worker may import, JSON Schemas for its inputs and outputs, and an
  evidence gate saying how many verified rows a finish needs and which
  cases they must cover. Copy `challenges/_template` and edit.

Step-by-step recipes with an acceptance check for each are in
[SETUP.md](SETUP.md) ("Add a challenge"); the same recipes in command form,
written for an AI agent doing the work, are in [AGENTS.md](AGENTS.md).

## Running a contest

- **The daemon** serves one challenge, owns the shared state under `state/`,
  and is the only process that talks to the kernel or the black box.
- **Two contestants** are agent sessions (Claude Code, Codex, any agent that
  runs shell commands) that you open and point at the daemon with a
  paste-in text from [SETUP.md](SETUP.md). Their only tool is
  `arena/client.py`. The protocol they follow is
  [CONTESTANT.md](CONTESTANT.md).
- **Advisors** are optional cross-vendor models a contestant may consult;
  keys go in `.env`, spend is capped and refused fail-closed. Advice is
  never evidence.
- **You** watch with `python3 arena/client.py snapshot`, and restart a
  contestant that stops. The daemon writes `state/<name>/SOLVED` when a
  finish is peer-verified. The state directory is the record; keep it.

After `SOLVED`, the evidence kit under `gauntlet/` turns the accepted result
into checked, laddered claims with generated verification tables
(`run_demo.py` shows it on a worked instance). [METHOD.md](METHOD.md)
explains how the two layers fit.

## Limits, stated plainly

- No runner yet. A contestant that stops (rate limit, context reset, crash)
  stays stopped until you restart it. Check the seats every ten minutes or
  so.
- The numeric black box worker uses POSIX signals: macOS and Linux only.
- One daemon serves one challenge on localhost; there is no multi-tenant or
  remote mode.
- The theorem-side end-to-end test needs a Lean toolchain and is run by
  hand, not by CI.
- In the evidence kit, gates run one after another, and "proved" rungs cite
  human-checked proofs; it does not call the kernel.

## What is in the box

```text
arena/               daemon, kernel and oracle runners, proof DAG, client, ops scripts, tests
challenges/          problem packages: _template, smoke_min (theorem), ising_lift (numeric)
gauntlet/            evidence kit: claim manifests, gates, ladder, provenance, tables
example_ising/       worked evidence-kit instance
instance_template/   evidence-kit package to copy
run_demo.py          evidence-kit entry point
CONTESTANT.md        the contestant protocol
SETUP.md             install, add a challenge, run and watch a contest
AGENTS.md            the same, in command form, for an AI agent (CLAUDE.md points here)
METHOD.md            design: arena, funnel, evidence, ship
CONTRIBUTING.md      how changes are verified; the rules the code keeps
```

## Contributing

Read [CONTRIBUTING.md](CONTRIBUTING.md): which checks run automatically on
every push, which you must run by hand, and the invariants any change must
preserve (the daemon stays the only writer; nothing certifies itself; logs
are append-only; the repository stays free of machine- and account-specific
paths).

## Cite

A paper describing the method and what it found is in preparation. Until it
appears, cite this repository by URL and commit.

## License

MIT. See [LICENSE](LICENSE).
