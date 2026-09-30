# Instructions for AI agents

This file is for an AI coding agent (Claude Code, Codex, or any agent that
runs shell commands) working in this repository. Humans start at
[README.md](README.md) and [SETUP.md](SETUP.md); everything here is also
there, in prose. Commands run from the repository root.

## First: which role are you in?

- **CONTESTANT.** You were told you are contestant `claude` or `codex` in a
  running session. Read [CONTESTANT.md](CONTESTANT.md) and follow it. Use
  only `arena/client.py`. Never edit anything under `state/`, never touch the
  daemon's Lean workspace, never open `challenges/<name>/oracle.py` (it is
  the sealed answer), and do not read the rest of this file. Stop here.
- **OPERATOR.** You were asked to set the arena up, add a problem, run or
  watch a contest, or change the code. The rest of this file is yours.

## What this is

A research arena: one daemon is the only gateway to a black box and the sole
writer of shared state; two contestants (AI agents in their own sessions)
work the problem through the daemon and verify each other; the daemon's
append-only log is the only admissible evidence. The ground truth is chosen
per challenge in `challenges/<name>/config.yaml`:

- `ground_truth: oracle` — the black box (the main mode). The challenge's
  `oracle.py` answers queries; contestants must identify or construct what it
  computes.
- `ground_truth: kernel` — experimental theorem mode. The Lean 4 kernel
  checks proofs against a frozen goal.

## Set up (every machine, once)

Requirements: Python 3.11 or newer; elan (the Lean toolchain manager) only
for the experimental theorem mode. Every entry point refuses an older Python with one
line saying so.

```sh
python3.11 -m venv .venv                         # any Python >= 3.11
.venv/bin/pip install -r arena/requirements.txt pytest
.venv/bin/python -m pytest arena/tests -q        # expect: all passed
.venv/bin/python arena/smoke_oracle.py           # expect: ORACLE-SMOKE: ALL OK
```

For the experimental theorem mode also:

```sh
curl -sSf https://elan.lean-lang.org/elan-init.sh | sh -s -- -y --no-modify-path --default-toolchain none
.venv/bin/python arena/smoke_arena.py            # expect: ARENA-SMOKE: ALL OK
```

The first theorem-mode run fetches the pinned toolchain; the smoke and the daemon
announce that and wait for it (minutes). Never copy a venv from another
machine. Never commit `.venv/`, `state/`, `.lake/` or `.env`.

## Add the user's problem

Acceptance for either kind: the daemon reports `"ok": true` on `health`,
`client.py problem` shows the statement, and one check or query returns.

### A black box (`ground_truth: oracle`, the main mode)

```sh
cp -R challenges/_template challenges/<name>
```

Then edit, all inside `challenges/<name>/`:

1. `config.yaml`: `challenge.name: <name>`, `ground_truth: oracle`,
   `kernel.enabled: false`; add an `oracle:` block (`enabled: true`, a
   one-sentence `description` contestants may read, `timeout_seconds`, a
   JSON Schema `input_schema` for query payloads, an `output_schema` for
   answers) and a `finish_gate:` block (`min_rows`: how many `match=yes`
   evidence rows a finish needs; `required_tags`: the coverage classes
   every finish must exhibit). `challenges/ising_lift/config.yaml` is a
   complete example.
2. `oracle.py`: `def query(payload) -> answer`. The answer may have any
   JSON shape the output schema declares. Only the daemon's worker
   subprocess imports this file; the daemon validates every payload
   against the schema first.
3. `problem.md`: what contestants are told, including the tags they must
   cover in their evidence.

Check:

```sh
.venv/bin/python arena/daemon.py --challenge <name>     # shell 1
python3 arena/client.py health                           # shell 2
python3 arena/client.py oracle --contestant-id claude --input '<json>' --predict '<json>' --hypothesis "what a hit confirms"
```

### A theorem (`ground_truth: kernel`, experimental)

```sh
cp -R challenges/smoke_min challenges/<name>
```

Then edit, all inside `challenges/<name>/`:

1. `config.yaml`: `challenge.name: <name>` (must equal the directory name)
   and `display_name`.
2. `Defs/` and `Defs.lean`: the definitions. Contestants import them and
   may never redefine them.
3. `Goal.lean`: the target as `def Arena.GoalStatement : Prop := ...`.
4. `Calibration/`: concrete examples closed `by decide` that pin the
   definitions to what `problem.md` says they mean. They must compile at
   daemon start, or the daemon refuses to serve.
5. `problem.md`: the informal statement, the calibration table, the finish
   criteria (keep the template's sections).
6. `lean-toolchain`: keep the pin unless the problem needs another. For
   Mathlib set `kernel.uses_mathlib: true`, commit a `lake-manifest.json`
   pin, and add `Mathlib` to `hygiene.import_allowlist`.

Check:

```sh
.venv/bin/python arena/daemon.py --challenge <name>     # shell 1
python3 arena/client.py health                           # shell 2
printf 'import Defs.Basic\n#eval 1 + 1\n' | python3 arena/client.py check --mode eval --file - --wait --contestant-id claude
```

## Run a contest

1. Start the daemon on the challenge (shell 1, or `arena/ops/start.sh
   <name>` for supervised operation on macOS). Optional advisor keys go in
   `.env` (`OPENAI_API_KEY`, `GEMINI_API_KEY`); the daemon runs without them.
2. Seat two contestants, one of two ways.

   **Command line (preferred when unattended):**

   ```sh
   .venv/bin/python arena/runner.py --challenge <name> --dry-run   # inspect the commands
   .venv/bin/python arena/runner.py --challenge <name>             # seats claude=claude codex=codex
   ```

   The runner calls `claude -p` and `codex exec` one bounded round at a
   time, resumes each seat's session, counts a round only if a new turn
   appeared in `state/<name>/contestants/<seat>/turns.jsonl`, waits out
   rate limits, retries crashes, gives a silent seat a fresh session, and
   stops a seat with a credentials problem. Gauges:
   `state/<name>/runner/<seat>.json`. Keep it alive with
   `arena/ops/runner-start.sh <name>` (macOS) or `arena/ops/arena-runner.service`
   (Linux). Any other agent CLI plugs in with
   `--seat <id>=command:'<shell template with {prompt}>'`.

   **In the apps:** open two agent sessions in the repository root and
   paste this into each, with its own id (`claude`, `codex`) and the
   daemon's URL, as a `/goal` so the agent keeps going:

   ```text
   /goal Read CONTESTANT.md and follow it exactly. You are contestant `claude`
   (export CONTESTANT_ID=claude) against the daemon at
   ORACLE_DAEMON_URL=http://127.0.0.1:8787. Do one tick, post your turn with
   `python3 arena/client.py turn`, then start the next tick; keep going until
   the snapshot shows SOLVED or I stop you. Use only arena/client.py; never
   edit state files or the Lean workspace; do not read challenges/<name>/oracle.py.
   ```

3. Watch. `python3 arena/client.py snapshot --contestant-id claude` shows
   shared state and turns; the daemon writes `state/<name>/SOLVED` when a
   finish is peer-verified. In the apps, a contestant that stops (rate
   limit, context reset, crash) stays stopped until you paste again; check
   both seats every ten minutes or so. Under the runner, read the gauges.
4. Stop with `arena/ops/stop.sh` or by ending the daemon process. State is
   append-only under `state/<name>/` (or `$ORACLE_STATE_ROOT`); keep it, it
   is the record.

## Change the code

- Before every push: `.venv/bin/python -m pytest arena/tests -q` and
  `.venv/bin/python arena/smoke_oracle.py` must pass. After changes under
  `arena/daemon.py`, `kernel_runner.py`, `lean_workspace.py`, `hygiene.py`,
  `assembly.py` or `dag.py`, also `arena/smoke_arena.py`. After changes under
  `gauntlet/`, `.venv/bin/python run_demo.py --instance example_ising --quick`
  and commit the regenerated tables. CI runs the first two on every push.
- The repository is account-agnostic: no home-directory paths, user names,
  machine names or credential files in tracked files. The suite's
  portability test fails on them; render paths through
  `gauntlet/paths.py` when a report must name one.
- Never commit `.env` or any key. Never read a key into a log or a prompt.
- Keep `state/` and the Lean workspace daemon-owned: no script edits them.

## Where things are

| Path | What |
|---|---|
| `arena/daemon.py` | the daemon: sole writer of state, only gateway to the black box (or, experimentally, the Lean kernel) |
| `arena/client.py` | the contestants' only tool (standard library) |
| `arena/runner.py` | drives the seats from the command line: bounded rounds, resume, backoff, gauges |
| `arena/smoke_oracle.py`, `arena/smoke_arena.py` | end-to-end retests: black box; experimental theorem mode |
| `arena/tests/` | unit suite, including the portability guard |
| `challenges/<name>/` | one exchangeable problem package; `_template` (blank), `ising_lift` (worked black box), `smoke_min` (theorem mode) |
| `state/<name>/` | append-only logs, proposals, `SOLVED` |
| `CONTESTANT.md` | the contestant protocol (tick, finish, peer verification; theorem mode at the end) |
| `README.md`, `SETUP.md`, `METHOD.md`, `CONTRIBUTING.md` | human documentation: front page, recipes, design, rules for changes |
