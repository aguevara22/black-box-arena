# Setup

Run commands from the SHIPPING root. Python 3.11 or newer is recommended.

## Environment

Create the root virtual environment and install the daemon, manager, and
status dependencies:

```sh
python3 -m venv .venv
.venv/bin/pip install -r arena/requirements.txt
```

The advisor is optional. Its SDK imports are lazy, so neither SDK is needed to
start the daemon or run the smoke test:

```sh
.venv/bin/pip install openai google-genai
```

Put optional `OPENAI_API_KEY` and `GEMINI_API_KEY` values in `.env`. Advisor
calls use a fail-closed spend ledger and a hard cap.

## Arena quickstart

Start the demo daemon:

```sh
.venv/bin/python arena/oracle_daemon.py --challenge ising_lift
```

In another shell:

```sh
python3 arena/client.py health
python3 arena/client.py problem
```

The deterministic integration retest needs the packages in
`arena/requirements.txt` and uses the same interpreter that launches it:

```sh
.venv/bin/python arena/smoke_arena.py
```

It uses localhost only, invokes the real client CLI for both contestant ids,
creates state under a temporary `ORACLE_STATE_ROOT`, and calls no advisor,
agent CLI, or external API.

## Real contestant sessions

The daemon is passive; the operator starts each agent loop. Both follow
`CONTESTANT.md`.

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
`launchctl` supervision. They default to `ising_lift`:

```sh
arena/ops/start.sh ising_lift
arena/ops/status.sh ising_lift
arena/ops/stop.sh
```

Linux and Windows users can run the daemon in the foreground or use their own
service supervisor.

## Add a challenge

```sh
cp -R challenges/_template challenges/my_case
```

Then update `challenge.name`, `problem.md`, the schemas, and `oracle.py`.
Problem-only packages set `oracle.enabled: false`. Oracle-backed packages
define `query(payload)` and may enable the generic `finish_gate` row/tag
policy shown in the template.

The source seal is process isolation plus contestant discipline, not an
operating-system sandbox. Agents with file access must be instructed not to
read the sealed source.

## Evidence-kit demo

Layer B remains standard-library-only:

```sh
python3 run_demo.py
python3 run_demo.py --quick
python3 run_demo.py --instance NAME
```

Generated reports and verification tables appear under the selected
instance's `out/` directory.
