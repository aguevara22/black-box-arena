# The Method — Arena → Funnel → Evidence → Ship

Version 0.3 (in development; 0.2 was 2026-07-24). Problem-agnostic
specification of a working method for hard proof and
identification-and-construction problems, in two coupled layers:

- **Layer A — the arena** (`arena/`, `challenges/`): autonomous multi-agent
  research under adversarial peer verification against a ground truth the
  agents cannot argue with — a sealed numeric oracle, the black box
  (`ground_truth: oracle`); experimentally, the Lean 4 kernel for theorems
  (`ground_truth: kernel`). This is the discovery engine.
- **Layer B — the evidence kit** (`gauntlet/`, `example_ising/`,
  `instance_template/`): the claim-coupled verification discipline an agent
  (or a human) uses to make results finish-grade, and to consolidate a
  solved run into checked, laddered claims.

A problem enters as a **challenge**; the arena drives it to a peer-verified
solution; the kit turns that solution into verified, documented claims.

## 0. Scope

The method targets problems where a mathematical object must be identified
(often behind a sealed oracle), explicit constructions must be produced, and
every claim must be evidenced by re-runnable checks — with the boundary
between "verified numerically", "verified exactly", and "proved" kept honest
and visible. It is not a proof assistant and not a test framework; it is the
discipline connecting discovery to both.

## 1. The problem template

A well-posed instance supplies five elements, authored as a challenge package
`challenges/<name>/{problem.md, config.yaml, oracle.py}`:

1. **A sealed oracle.** Structured input → ground-truth output (typically the
   *classical* target of a richer object to be discovered). Implemented in
   `oracle.py :: query(payload)`, loaded only inside a dedicated subprocess;
   contestants are forbidden from reading its source. Problem-only challenges
   may disable it.
2. **Calibration with a deliberate leak.** Worked input/output pairs in
   `problem.md`, at least two related by a nontrivial symmetry of the hidden
   object.
3. **Chained tasks.** The deliverable must be *read off* prior deliverables
   (e.g. a limit or extraction of the constructed object must reproduce the
   oracle), so later parts cannot be faked without the earlier ones.
4. **An explicitness constraint.** The finish must be an *effective
   algorithm* producing computable output for any valid input — "formally
   correct" is not accepted, and the daemon mechanically requires oracle
   evidence before a finish may enter verification.
5. **A genericity locus.** The oracle returns an explicit wall/degenerate
   status off the generic locus; generators and gates reject non-generic
   samples.

## 2. Layer A — the arena

### 2.1 Sovereignty: the daemon

One daemon process is the **sole writer of all shared state** and the **only
gateway to the ground truth** — the kernel or the sealed oracle. Contestants
interact exclusively through a stdlib-only client. State writes are
serialized; nothing an agent does can corrupt the record, touch the Lean
workspace, or read the answer key.

### 2.2 Contestants: independent, stateless, stigmergic

Two (or more) contestant agents — **different vendors by design** — work the
same challenge in parallel. Each runs an identical stateless tick:

    snapshot (own journal first) → think/compute → oracle branch →
    post (finding / breakthrough / direction / journal) → turn record → yield

Contestants never message each other. Coordination is **stigmergic**: an
append-only shared blackboard whose entries (findings, breakthroughs, finish
proposals) appear in the other's snapshot as FYI. Private state (journal,
current direction) keeps each agent's line of inquiry its own. This is the
participant-level independence that makes agreement meaningful: routes to a
result are built blind, results are shared.

### 2.3 The oracle protocol: predict-before-query, correlate every response

- **Predict-before-query.** An oracle call may carry a pre-registered
  prediction and the hypothesis a correct prediction would confirm. Queries
  are hypothesis tests, not lookups; a hit can auto-promote the hypothesis.
- **Request correlation.** Every response round-trips a request id and input
  hash; the response cache is versioned. Records from before a protocol
  version are **demoted to legacy/unverified** (trust epochs). This exists
  because ground truth *was once corrupted in production*: a stale-response
  misattribution poisoned the cache with false coefficients; the append-only
  log made line-level forensics possible, the protocol fix made recurrence
  impossible, and the demotion made the contamination auditable. Treat the
  oracle channel with the same adversarial care as the claims it judges.

### 2.4 The claim funnel

    finding → breakthrough → finish → peer-verify → SOLVED

Independent gates between every stage:

- **finding**: open observation, append-only, no gate.
- **breakthrough**: promoted only via one of three paths (per-challenge
  policy): (i) *independent cross-agent confirmation* — another contestant's
  own entries corroborate it; (ii) *adversarial critic* — a separate LLM
  verifier judges the claim against the oracle evidence it cites;
  (iii) *predictive match* — a pre-registered oracle prediction came true.
- **finish**: a complete effective algorithm plus argument. The daemon
  **mechanically rejects** a finish that lacks the challenge's required
  oracle-evidence matrix (row count and case-coverage tags from
  `config.yaml`) before any human or agent even sees it for verification.
- **peer-verify**: the **other** contestant verifies against `problem.md`
  criteria. There is no self-certification. A rejection must cite a concrete
  gap or failing input. Superseded or retracted SOLVED states remain on disk
  as stale markers — victory declarations are part of the auditable record.

### 2.5 Roles around the contest

- **Advisor** (per contestant, cross-vendor): highest-effort external model
  for hard derivations and direction checks. Advice **never substitutes for
  evidence**, and spend is governed by a **fail-closed ledger with a hard
  dollar cap** — a call that could breach the cap is refused before it runs.
- **Historian** (optional, cheap model, periodic): digests the blackboard —
  active threads, dead ends, open questions. **Non-directive by
  construction**: it summarizes, never instructs.
- **Manager dashboard**: strictly read-only observation of state and health.

### 2.6 Append-only reproducibility

Findings, breakthroughs, finishes, verifications, turns, and every oracle
call are append-only JSONL (+ human-readable mirrors). Corrections are
superseding entries, never edits. The entire discovery history — including
wrong turns, incidents, and retracted victories — is reconstructible from
state alone.

## 3. Layer B — the evidence kit

The kit (`gauntlet/` and a worked instance) is the single-agent verification
discipline, used in two places: *inside* the arena, to assemble finish-grade
evidence; and *after* a solved run, to consolidate the accepted solution into
laddered, machine-checked claims.

- **Redundant ground truth**: reconstruct the identified object at least
  twice, from independent formulations sharing no code; exact arithmetic
  wherever the object is exact; structural facts as running assertions.
- **Gates**: one per claim; policies own tolerances, sample counts, budgets,
  precision escalation; seeded from a root seed; greppable lines + JSONL
  records; every coverage cut is an explicit SKIP.
- **The claim ladder**: conjectured → numeric → exact → proved_small →
  proved; promotion only with evidence, demotion when a gate weakens; the
  frontier is published, not hidden.
- **Provenance**: cached artifacts carry code hashes; consuming gates fail on
  staleness (the artifact-level analog of the arena's trust epochs).
- **Independence lint**: declared route pairs are checked for shared imports
  — the mechanical backstop of an authorial discipline.
- **Generated verification tables + drift check**: the writeup's tables are
  build artifacts of the manifest and the latest report; prose cannot
  quietly diverge from the suite.

## 4. How the layers interlock

- The arena's funnel gates **acceptance**; the kit gates **evidence
  quality**. A finish assembled with kit-style checks passes peer review or
  fails it for concrete reasons.
- The arena's oracle log is the raw record; the kit's consolidation turns the
  accepted algorithm into a claims manifest with statuses, re-runnable gates,
  and a generated verification table — the difference between "the other
  agent agreed" and "every claim is checked and its proof status is stated."
- Both layers share one epistemic rule: **nothing certifies itself** — not a
  claim (promotion paths), not a finish (peer verify), not a cached artifact
  (provenance), not the oracle channel (request correlation), not the
  writeup (generated tables).

## 5. Operating procedure

- **P0 — Author.** Copy a challenge package (`challenges/smoke_min/` for a
  theorem, `challenges/_template/` for a numeric oracle); write `problem.md`
  (task, calibration, finish criteria) and `config.yaml` (ground truth,
  contestants, promotion policy); then either the frozen Lean layer (`Defs/`,
  `Goal.lean`, `Calibration/`) or the sealed `oracle.py` with its schemas and
  finish gate. Version control; state and scratch out of tree.
- **P1 — Boot.** Start the daemon; verify health; contestants read
  `problem.md` through the client only.
- **P2 — Contest.** Contestants run ticks; advisors on hard derivations;
  predict-before-query; blackboard fills; promotions fire; historian digests.
- **P3 — Finish.** A contestant assembles the evidence matrix (kit checks
  recommended) and proposes; the daemon's mechanical gate filters; the peer
  verifies or rejects with a concrete gap.
- **P4 — Solve.** SOLVED only by daemon, only on peer agreement.
- **P5 — Consolidate.** Port the accepted solution into an evidence-kit
  instance: two routes, gates per claim, ladder statuses, provenance,
  generated tables. Promote what can be proved; publish the frontier.
- **P6 — Ship.** Writeup with generated tables, reproduction commands, the
  discovery history (reconstructible from state), and open problems.

## 6. Failure modes and defenses

| Failure mode | Defense |
|---|---|
| Premature victory / self-certification | peer-verify by the other contestant; daemon-set SOLVED; stale SOLVED markers retained |
| Plausible-but-wrong claims | three-path breakthrough promotion; predict-before-query; adversarial critic |
| Groupthink / shared blind spots | cross-vendor contestants and advisors; stigmergic (not conversational) coordination; blind construction |
| Gamed or thin evidence | mechanical finish gate (row count + coverage tags) before verification |
| Oracle-channel corruption | request id + input hash on every response; versioned cache; trust epochs demoting legacy records; recompute audits |
| Shared-state corruption / lost history | single-writer daemon; append-only logs; corrections as superseding entries |
| Runaway advisor spend | fail-closed hard-cap ledger, pre-call reservation |
| Context drift over long runs | journal-first snapshots; non-directive historian digests |
| Convention/sign errors in consolidation | two independent routes + symmetry invariants + calibration leak |
| Writeup drift from the suite | generated verification tables + drift check |
| Silent coverage loss | explicit SKIP records with reasons |
| Stale cached artifacts | provenance hashes checked by every consuming gate |
| Irreproducible randomness | root seed + stable per-gate derivation, recorded in reports |

## 7. Map: principle → implementation

| Principle | Where |
|---|---|
| Daemon sovereignty, funnel, finish gate | `arena/daemon.py`, `arena/state_manager.py` |
| Contestant tick protocol | `CONTESTANT.md`, `arena/client.py` |
| Sealed oracle subprocess + request correlation (`ground_truth: oracle`) | `arena/oracle_runner.py`, `arena/oracle_worker.py`, `arena/oracle_protocol.py`; kernel jobs correlate through `arena/protocol.py` |
| Breakthrough promotion (three paths) | `arena/promotion.py` |
| Advisor with fail-closed spend cap | `arena/advisor.py` |
| Non-directive historian | `arena/historian.py` |
| Read-only dashboard / status | `arena/manager.py`, `arena/status.py` |
| Challenge modularity | `challenges/_template/`, `challenges/ising_lift/` (oracle), `challenges/smoke_min/` (kernel) |
| Evidence kit (routes, gates, ladder, provenance, lint, tables) | `gauntlet/`, `example_ising/`, `instance_template/` |

Recipes: `SETUP.md` (for people) and `AGENTS.md` (for agents). Rules every
change keeps: `CONTRIBUTING.md`. A paper describing the method is in
preparation and supersedes the 0.2 method report that used to live here.
