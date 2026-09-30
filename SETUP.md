# Setup

Run commands from the repo root. Python 3.11 or newer is required (tested on
3.11 and 3.12); every entry point refuses an older interpreter with one line
saying so.

## Prerequisites

- **Python** environment for the daemon:

  ```sh
  python3.11 -m venv .venv                         # any Python >= 3.11
  .venv/bin/pip install -r arena/requirements.txt
  ```

- The advisor is optional; its SDK imports are lazy:

  ```sh
  .venv/bin/pip install openai google-genai
  ```

  Put optional `OPENAI_API_KEY` / `GEMINI_API_KEY` in `.env`. Advisor calls
  use a fail-closed spend ledger and a hard cap.

- **Lean toolchain manager** (elan) — only for the experimental theorem
  mode. Installs to `~/.elan`, never inside this repo:

  ```sh
  curl -sSf https://elan.lean-lang.org/elan-init.sh | sh -s -- -y --no-modify-path --default-toolchain none
  ```

  Each challenge pins its own toolchain in `challenges/<name>/lean-toolchain`.
  The daemon (at boot) and the kernel smoke (before it starts the daemon)
  check that the pinned toolchain is installed and otherwise fetch it once,
  explicitly and with no deadline — minutes on a fresh machine. To pre-fetch
  by hand:

  ```sh
  ~/.elan/bin/elan toolchain install "$(cat challenges/smoke_min/lean-toolchain)"
  ```

## Where things live

| What | Where | Why |
|---|---|---|
| Repo + challenge sources | wherever you cloned (iCloud is fine) | frozen files only |
| Arena state (append-only JSONL) | `state/` or `$ORACLE_STATE_ROOT` | small text files |
| Lean build workspaces (theorem mode) | `~/.cache/arena-lean/<challenge>` or `$ARENA_LEAN_WORKSPACE_ROOT` | `.lake` build trees must stay OUT of iCloud — sync/eviction breaks builds |

For long live sessions prefer a local `ORACLE_STATE_ROOT` too: iCloud
eviction can stall the `fsync` on every append.

## Quickstart: the black box (`ground_truth: oracle`)

This is the arena's main mode. The worked example is `challenges/ising_lift`
(a sealed classical coefficient over weighted graphs; contestants must
construct the polynomial lift whose extraction reproduces it). Its `oracle.py` is imported only by the daemon's
worker subprocess (`arena/oracle_worker.py`, POSIX only: it uses
`signal.alarm` for the per-query timeout).

```sh
.venv/bin/python arena/daemon.py --challenge ising_lift          # shell 1
python3 arena/client.py health                                    # shell 2
python3 arena/client.py oracle --contestant-id claude \
  --input '{"n":2,"edges":[],"fields":[1,2]}' \
  --predict '{"status":"ok","coefficient":0}' --hypothesis "fields alone give 0"
python3 arena/smoke_oracle.py     # deterministic end-to-end retest, temp state, no Lean
```

Every oracle call is logged to `state/<challenge>/shared/oracle_log.jsonl`
with the request id and input hash the daemon correlated it by; a finish
proposal must carry the `EVIDENCE:` rows `config.yaml`'s `finish_gate`
demands (see CONTESTANT.md).

## Real contestant sessions

The daemon is passive. Both contestants follow `CONTESTANT.md`; you seat
them in one of two ways. Pick one per contest; they can also be mixed (one
seat in an app, one under the runner).

### A. In the apps

Open a coding-agent session (Claude Code, Codex, any agent that can run
shell commands) in the repo root and paste, with the id and URL of that
seat, as a goal (`/goal` in both Claude Code and Codex keeps the agent
working without you typing "continue"; plain paste works too):

```text
/goal Read CONTESTANT.md and follow it exactly. You are contestant `claude`
(export CONTESTANT_ID=claude) against the daemon at
ORACLE_DAEMON_URL=http://127.0.0.1:8787. Do one tick, post your turn with
`python3 arena/client.py turn`, then start the next tick; keep going until
the snapshot shows SOLVED or I stop you. Use only arena/client.py; never edit
state files or the Lean workspace; do not read challenges/<name>/oracle.py.
```

Nothing restarts a seat in this mode: an agent that stops on a rate limit, a
context reset or a crash stays stopped until you paste again, so check each
contestant's last turn in `client.py snapshot` now and then.

### B. From the command line: the runner

```sh
.venv/bin/python arena/runner.py --challenge <name>            # seats claude=claude codex=codex
.venv/bin/python arena/runner.py --challenge <name> --dry-run  # show the commands it would run
```

The runner drives each seat through that agent's own command-line tool, one
bounded round per invocation: `claude -p` with a pinned session id that is
resumed every round, and `codex exec` followed by `codex exec resume
<thread>`. Each round carries the same short kickoff ("do exactly one tick,
record it with `client.py turn`, stop"); the agent's memory between rounds is
the daemon's shared state. A round counts only if a new entry appeared in
that seat's `turns.jsonl`. Then:

| Outcome | What the runner does |
|---|---|
| a turn was recorded | pause (`--pause`, default 10 s), next round |
| the agent ran but recorded nothing | strike; after `--max-idle-rounds` (3) a fresh session |
| rate or usage limit in the output | wait 5, 10, 20, 40, 60 minutes, retry |
| crash or `--round-timeout` (30 min) | wait 1, 2, 4, 8, 15 minutes; fresh session after 5 |
| credentials problem | that seat stops and says so; fix, restart the runner |

Gauges: `state/<name>/runner/<seat>.json` (status, round, last recorded
turn, next attempt) and `<seat>.log` (every round's output). The runner
writes nothing else; the daemon's files stay the daemon's. It exits 0 when
`SOLVED` appears.

Options: `--seat <id>=<claude|codex|command:TEMPLATE>` (repeatable; a
`command:` template with `{prompt}`, `{prompt_file}`, `{seat}`, `{url}`
plugs in any other agent CLI), `--claude-permission-mode` (default `auto`),
`--claude-args`, `--codex-sandbox` (default `workspace-write`),
`--codex-args` (for example `-m <model>`), `--rounds N`, `--once`. Run the
runner with the same `ORACLE_STATE_ROOT` as the daemon.

To keep the runner itself alive across logouts: `arena/ops/runner-start.sh
<name> [runner args]` and `arena/ops/runner-stop.sh` (macOS launchd), or
`arena/ops/arena-runner.service` (Linux systemd user unit). Both need
`claude` and `codex` on the PATH they set (`~/.local/bin`, Homebrew).

| Contestant id | Agent runner | Cross-vendor advisor | Optional key |
|---|---|---|---|
| `claude` | Claude Code | OpenAI | `OPENAI_API_KEY` |
| `codex` | Codex | Gemini | `GEMINI_API_KEY` |

For each shell:

```sh
export ORACLE_DAEMON_URL=http://127.0.0.1:8787
export CONTESTANT_ID=claude  # use codex in the other shell
python3 arena/client.py snapshot --contestant-id "$CONTESTANT_ID"
```

The manager is read-only:

```sh
.venv/bin/python arena/status.py --challenge ising_lift --json
.venv/bin/python arena/manager.py --challenge ising_lift
```

## Operations

`arena/ops/start.sh`, `status.sh`, and `stop.sh` provide optional macOS
`launchctl` supervision. Pass the challenge name:

```sh
arena/ops/start.sh ising_lift
arena/ops/status.sh ising_lift
arena/ops/stop.sh
```

## Add a challenge: your own black box

```sh
cp -R challenges/_template challenges/my_case
```

Then set `challenge.name: my_case` and, in `config.yaml`,
`ground_truth: oracle`, `kernel.enabled: false`, and add the `oracle:` block
(a one-sentence `description`, a JSON Schema for query payloads, one for
answers, a per-query `timeout_seconds`) and the `finish_gate:` block (how
many `match=yes` evidence rows a finish needs and which coverage tags they
must exhibit). Write `oracle.py :: query(payload) -> answer` — the answer
may have any JSON shape your output schema declares — and `problem.md`.
Only the daemon's worker subprocess imports `oracle.py`; the daemon
validates every payload against the schema before the worker sees it. Then:

```sh
.venv/bin/python arena/daemon.py --challenge my_case
python3 arena/client.py oracle --contestant-id claude --input '{"n":7}' \
  --predict '{"value":49}' --hypothesis "f(n) = n^2"
```

`challenges/ising_lift` is a complete worked example of the blocks. Keep the
answer key out of every file a contestant may read: contestants may read the
whole repository except `oracle.py`, and only the daemon's worker imports it.

## After SOLVED: consolidate the result

The arena ends at a peer-verified finish. The evidence kit (Layer B,
`gauntlet/`) turns that result into checked, laddered claims: copy
`instance_template/` to a new instance, fill the TODOs (the accepted
algorithm, its claims, the gates that check them), and run
`.venv/bin/python run_demo.py --instance <name>` until it is green. Promote
claims up the ladder only with new evidence; paste the generated
verification tables into the write-up, never hand-edited ones. Keep
`state/`, `.venv/`, scratch and backups out of the tree; `.gitignore` ships.

## Experimental: theorem mode (`ground_truth: kernel`)

The same arena can run with the Lean 4 kernel in place of the black box:
the challenge is a statement written in Lean, contestants build the proof
together as a kernel-checked DAG, and the finish is a complete proof of the
frozen goal accepted by the peer. This mode works and has its own
end-to-end test, but it is young and needs elan (see Prerequisites).

### Quickstart

Start the smoke challenge daemon (first boot fetches the pinned toolchain if
it is missing, materializes the workspace and builds the frozen libraries —
seconds for mathlib-free challenges, minutes for mathlib ones):

```sh
.venv/bin/python arena/daemon.py --challenge smoke_min
```

In another shell:

```sh
python3 arena/client.py health
python3 arena/client.py problem
printf 'import Defs.Basic\n#eval Arena.double 21\n' | \
  python3 arena/client.py check --mode eval --file - --wait
```

The deterministic integration retest (temp state, temp workspace, real
client CLI, no advisor/agent/external API):

```sh
.venv/bin/python arena/smoke_arena.py
```

### Add a theorem challenge

```sh
cp -R challenges/smoke_min challenges/my_theorem
```

Then set `challenge.name: my_theorem`, write `problem.md`, and author the
frozen Lean layer: `Defs/` (the definitions), `Goal.lean` (`def
Arena.GoalStatement : Prop := ...`), and `Calibration/` (concrete `by decide`
examples — they must compile at daemon startup or the daemon refuses to
serve). Pin `lean-toolchain`; for mathlib challenges set
`kernel.uses_mathlib: true`, commit a `lake-manifest.json` pin, and add
`Mathlib` to `hygiene.import_allowlist`.

Nothing is secret in a theorem challenge: contestants may read every frozen
file. The discipline is that the daemon's kernel verdict — recorded in the
append-only job log — is the only admissible evidence, and contestants never
touch the workspace.

## Evidence-kit demo

Layer B needs no third-party package, only the Python 3.11+ interpreter:

```sh
.venv/bin/python run_demo.py
.venv/bin/python run_demo.py --quick
.venv/bin/python run_demo.py --instance NAME
```

Generated reports and verification tables appear under the selected
instance's `out/` directory; the tables are tracked, the `report.json` and
`report.jsonl` are gitignored (wall-clock timings).
