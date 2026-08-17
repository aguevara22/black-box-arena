# Setup

Run commands from the repo root. Python 3.11 or newer is recommended.

## Prerequisites

- **Python** environment for the daemon:

  ```sh
  python3 -m venv .venv
  .venv/bin/pip install -r arena/requirements.txt
  ```

- **Lean toolchain manager** (elan). Installs to `~/.elan`, never inside
  this repo:

  ```sh
  curl -sSf https://elan.lean-lang.org/elan-init.sh | sh -s -- -y --no-modify-path --default-toolchain none
  ```

  Each challenge pins its own toolchain in `challenges/<name>/lean-toolchain`;
  elan fetches it on first use.

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

## Arena quickstart

Start the smoke challenge daemon (first boot materializes the workspace and
builds the frozen libraries — seconds for mathlib-free challenges, minutes
for mathlib ones):

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
follow `CONTESTANT.md`.

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

## Evidence-kit demo

Layer B remains standard-library-only:

```sh
python3 run_demo.py
python3 run_demo.py --quick
python3 run_demo.py --instance NAME
```

Generated reports and verification tables appear under the selected
instance's `out/` directory.
