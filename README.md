# Black Box Arena

Put two AI agents on a hard mathematical problem, and let a checker that
nobody can argue with decide what is true.

Version 0.3 (in development).

## What it does

You have a hard problem. It is one of two kinds:

- **A theorem you want proved.**
- **A black box you want understood.** You can compute the answer for any
  input, but you do not know the formula, the algorithm, or the
  construction behind it, and you want one.

You hand the problem to two AI agents. They work at the same time, each in
its own session, and they can read each other's notes. Left alone, agents
also fool themselves and each other: they announce results that are not
true, and they agree with each other too easily. The arena makes that
impossible with three rules.

1. **A neutral checker decides what is true.** For a theorem, the checker is
   the Lean proof checker: a proof either compiles or it does not, and no
   amount of argument changes that. For a black box, the checker is the
   black box itself. The agents may ask it questions, but only through a
   gatekeeper program that writes down every question and every answer.
   The agents never see the code inside the box.
2. **An agent may claim only what the checker has confirmed.** "I am sure"
   counts for nothing. "The checker confirmed it, here is the receipt"
   counts. Each receipt is a line in the gatekeeper's record, and the
   record can only grow; nothing in it is ever edited or erased.
3. **The problem is finished only when the two agents agree.** One agent
   proposes a complete answer with its receipts. The other agent examines
   it and either accepts or points at the gap. Only then is the problem
   marked solved.

What you get at the end is the answer, and with it a complete written
account of how it was found: every question asked, every answer given,
every claim made, who checked what, in what order. Anyone can read that
account later and follow the reasoning step by step.

A few words the rest of the documentation uses: the gatekeeper program is
called **the daemon**; each of the two agents is a **contestant**; your
problem, packaged as a folder, is a **challenge**; the file the daemon
writes when the two agents agree is called **SOLVED**.

## Who it is for

- Mathematicians and scientists who want to try AI agents on a real open
  problem and be able to trust, and show, how the result was reached.
- Anyone with a statement to prove in Lean 4, or a computable function they
  want turned into a formula or a construction.
- People who study how autonomous agents behave when they cannot bluff.

## Try it in five minutes

You need Python 3.11 or newer, and nothing else for this part.

```sh
git clone <this repository>
cd black-box-arena
python3.11 -m venv .venv                         # any Python 3.11 or newer works
.venv/bin/pip install -r arena/requirements.txt
.venv/bin/python arena/smoke_oracle.py
```

The last command plays a whole contest on a small built-in black-box
problem, with two scripted stand-ins for the agents. You will see them
question the box, register a guess before asking and get it confirmed,
post findings, try to finish too early and be refused for thin evidence,
finish properly, check each other, and reach `SOLVED`. It ends with the
line `ORACLE-SMOKE: ALL OK`.

To ask the box a question yourself, keep a daemon running in one terminal
and use the second:

```sh
.venv/bin/python arena/daemon.py --challenge ising_lift          # terminal 1
python3 arena/client.py problem                                   # terminal 2: read the problem
python3 arena/client.py oracle --contestant-id claude \
  --input '{"n":2,"edges":[],"fields":[1,2]}' \
  --predict '{"status":"ok","coefficient":0}' --hypothesis "fields alone give 0"
```

The theorem side needs the Lean toolchain as well; [SETUP.md](SETUP.md)
shows how to install it, and `.venv/bin/python arena/smoke_arena.py` plays
a small proving contest the same way. The first run downloads a Lean
toolchain and says so.

## Give it your own problem

Your problem becomes a folder under `challenges/`.

For a **theorem**, the folder holds the definitions the statement needs,
the statement itself, written in Lean 4, and a few small worked examples
that must compile before the arena will start, so that the definitions
provably mean what your description says. Start from the folder
`challenges/smoke_min` and change it.

For a **black box**, the folder holds a Python function that computes the
answer, a description of what inputs it accepts and what its answers look
like, a short written statement of the task for the agents, and a rule for
how much evidence a proposed solution must show before anyone looks at it.
Start from the folder `challenges/_template` and change it.

[SETUP.md](SETUP.md) walks through both, with a check at the end that tells
you it worked. [AGENTS.md](AGENTS.md) has the same steps written for an AI
agent, so you can hand the setup to one.

## Running it for real

You are the person in charge. The work is this:

1. Start the daemon on your problem. It stays up for the whole contest.
2. Open two AI agent sessions, for example one Claude Code and one Codex,
   and paste into each a short text from [SETUP.md](SETUP.md) that tells it
   which of the two contestants it is and where the daemon is. From then on
   each agent works in rounds: read the shared notes, think, ask the checker,
   post what it found, repeat.
3. Look in from time to time. The agents cannot cheat, but they can stop:
   a rate limit, a lost context, a crash. Nothing restarts them yet; you
   paste the text again.
4. When the two agents agree, the daemon writes the `SOLVED` file. The
   `state/` folder next to it is the full account of the contest. Keep it.

The agents may also consult an outside model for hard derivations. That
advice never counts as evidence; only the checker's receipts do.

Optional, after a solution: the folder `gauntlet/` holds tools that turn
the accepted solution into a set of independently checked claims with
generated tables you can put in a paper. [METHOD.md](METHOD.md) explains
the design behind all of this.

## Known limits

- Nothing restarts an agent that stops; you do.
- The black-box side runs on macOS and Linux only.
- One daemon serves one problem on one machine.
- The theorem-side test run needs a Lean installation, so it is run by hand
  rather than automatically.

## What is where

```text
arena/               the daemon, the checkers, the agents' command-line tool, tests
challenges/          problems: _template (blank), smoke_min (a tiny theorem), ising_lift (a black box)
gauntlet/            tools that turn a solution into checked claims and tables
example_ising/       a worked example of those tools
instance_template/   a blank to copy for them
run_demo.py          runs the worked example
CONTESTANT.md        the rules an agent follows during a contest
SETUP.md             installing, adding a problem, running a contest
AGENTS.md            the same for an AI agent doing the setup (CLAUDE.md points here)
METHOD.md            the design and the reasons behind it
CONTRIBUTING.md      how changes to this code are checked
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for the checks every change goes
through and the rules no change may break.

## Cite

A paper about the method is in preparation. Until it appears, cite this
repository by its address and the commit you used.

## License

MIT. See [LICENSE](LICENSE).
