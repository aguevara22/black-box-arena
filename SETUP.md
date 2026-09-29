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

- **Lean toolchain manager** (elan). Installs to `~/.elan`, never inside
  this repo:

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

- The advisor is optional; its SDK imports are lazy:

  ```sh
  .venv/bin/pip install openai google-genai
  ```

  Put optional `OPENAI_API_KEY` / `GEMINI_API_KEY` in `.env`. Advisor calls
  use a fail-closed spend ledger and a hard cap.

## Where things live

| What | Where | Why |
|---|---|---|
| Repo + challenge sources | wherever you cloned (iCloud is fine) | frozen files only |
| Arena state (append-only JSONL) | `state/` or `$ORACLE_STATE_ROOT` | small text files |
| Lean build workspaces | `~/.cache/arena-lean/<challenge>` or `$ARENA_LEAN_WORKSPACE_ROOT` | `.lake` build trees must stay OUT of iCloud — sync/eviction breaks builds |

For long live sessions prefer a local `ORACLE_STATE_ROOT` too: iCloud
eviction can stall the `fsync` on every append.

## Numeric challenges: the sealed oracle (`ground_truth: oracle`)

The worked example is `challenges/ising_lift` (a sealed classical coefficient
over weighted graphs; contestants must construct the polynomial lift whose
extraction reproduces it). Its `oracle.py` is imported only by the daemon's
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

To add an oracle challenge: copy `challenges/_template/`, set
`ground_truth: oracle`, `kernel.enabled: false`, fill `oracle.py :: query`,
the `oracle.input_schema`, and `finish_gate`; keep the answer key out of
every open file. Skip the Lean files entirely.

## Arena quickstart (kernel regime)

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

## Real contestant sessions

The daemon is passive; the operator starts each agent session by hand. Both
follow `CONTESTANT.md`. There is no runner yet: a seat that stops (rate
limit, context reset, crash) stays stopped until you restart it, so check
each contestant's last turn in `client.py snapshot` now and then.

To seat an agent, open a coding-agent session (Claude Code, Codex, any
agent that can run shell commands) in the repo root and paste, with the id
and URL of that seat:

```text
Read CONTESTANT.md and follow it exactly. You are contestant `claude`
(export CONTESTANT_ID=claude) against the daemon at
ORACLE_DAEMON_URL=http://127.0.0.1:8787. Do one tick, post your turn, then
start the next tick; keep going until SOLVED appears in the snapshot or I
stop you. Use only arena/client.py; never edit state files or the Lean
workspace; do not read challenges/<name>/oracle.py.
```

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
.venv/bin/python arena/status.py --challenge smoke_min --json
.venv/bin/python arena/manager.py --challenge smoke_min
```

## Operations

`arena/ops/start.sh`, `status.sh`, and `stop.sh` provide optional macOS
`launchctl` supervision. Pass the challenge name:

```sh
arena/ops/start.sh smoke_min
arena/ops/status.sh smoke_min
arena/ops/stop.sh
```

## Add a challenge

```sh
cp -R challenges/_template challenges/my_case
```

Then set `challenge.name`, write `problem.md`, and author the frozen Lean
layer: `Defs/` (the definitions), `Goal.lean` (`def Arena.GoalStatement :
Prop := ...`), and `Calibration/` (concrete `by decide` examples — they must
compile at daemon startup or the daemon refuses to serve). Pin
`lean-toolchain`; for mathlib challenges set `kernel.uses_mathlib: true`,
commit a `lake-manifest.json` pin, and add `Mathlib` to
`hygiene.import_allowlist`.

Nothing is secret: contestants may read every frozen file. The discipline is
that the daemon's kernel verdict — recorded in the append-only job log — is
the only admissible evidence, and contestants never touch the workspace.

### Add a numeric challenge (sealed oracle)

No Lean layer. Copy the template, then in `config.yaml` set
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

`challenges/ising_lift` is a complete worked example of the blocks.

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
