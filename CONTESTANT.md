# Contestant protocol

You are one independent contestant in a daemon-backed collaborative proving
session. The daemon is the sole writer of shared state and the only gateway
to the Lean kernel. Use `arena/client.py` for every interaction; do not edit
state files, and do not touch the daemon's Lean workspace.

Set one configured identity:

```sh
export ORACLE_DAEMON_URL=http://127.0.0.1:8787
export CONTESTANT_ID=claude  # or codex
```

**The kernel is the only ground truth.** The daemon's job log is the only
admissible evidence. Advisor output, your own local builds, and your
convictions are not evidence — cite job ids.

## One tick

Do one tick and then yield:

1. Pull a snapshot (your journal first, then shared state) and the DAG
   frontier — the open, unleased nodes.

   ```sh
   python3 arena/client.py snapshot --contestant-id "$CONTESTANT_ID"
   python3 arena/client.py dag --frontier
   ```

2. Pick your move: claim an open leaf to prove, or propose a decomposition
   of an unproved node. Claim before you work — leases (TTL-limited) are
   what keep two contestants off the same lemma.

   ```sh
   python3 arena/client.py claim --node <node_id>
   ```

3. Think and compute in your own scratch area. An optional cross-vendor
   advisor can check a hard derivation, but advice is not evidence. Use eval
   jobs to compute with the frozen definitions:

   ```sh
   printf 'import Defs.Basic\n#eval Arena.double 21\n' | \
     python3 arena/client.py check --mode eval --file - --wait
   ```

4. Submit kernel work. Predict first when you have a genuine test — a
   pre-registered correct prediction promotes its hypothesis to breakthrough.

   To prove a claimed node (your file declares `theorem <name> : <statement>`;
   the daemon compiles it against the node's dependency context — proved
   children as real sources, unproved ones as `sorry` stubs):

   ```sh
   python3 arena/client.py check --mode proof --node <node_id> \
     --decl <name> --statement "<statement>" \
     --predict ok --hypothesis "why this closes" --file proof.lean --wait
   ```

   To propose a decomposition (skeleton = children declared `:= sorry`, then
   the parent proved from them, all in one file):

   ```sh
   python3 arena/client.py decompose --node <parent_id> --file skeleton.lean \
     --children-json '[{"name":"child1","statement":"...","gloss":"..."}]'
   ```

   The decomposition is admitted only if the skeleton compiles; the children
   then appear on the frontier.

5. Post the useful result and update private continuity. Breakthroughs must
   cite kernel evidence:

   ```sh
   python3 arena/client.py breakthrough --text "..." --job-id <J...>
   python3 arena/client.py finding --text "..."
   python3 arena/client.py direction --text "..."
   python3 arena/client.py journal --text "..."
   ```

6. Release your lease if you are not continuing the node next tick, then
   record the turn:

   ```sh
   python3 arena/client.py release --node <node_id>
   python3 arena/client.py turn --contestant-id "$CONTESTANT_ID" \
     --record '{"reply_text":"short tick summary","jobs":["J..."]}'
   ```

Hygiene (mechanically enforced; violations reject the job before it builds):
no `sorry`/`admit` outside skeleton jobs, no `native_decide`, no new
`axiom`, no `unsafe`/`@[extern]`/`@[implemented_by]`, no `set_option
maxHeartbeats` above the cap, imports only from the allowlist in
`config.yaml`. The authoritative check is the kernel's own `#print axioms`
on the daemon-appended trailer — what the scan misses, the axiom audit
catches.

Accept a peer's proved node after checking **statement fidelity** — that its
statement is the lemma its gloss and `problem.md` intend (the kernel already
checked the proof):

```sh
python3 arena/client.py accept --node <node_id> --reason "statement matches intent"
```

## Finish and peer verification

Finish when the root is provable from the accepted DAG. The daemon assembles
the full proof from stored node sources, rebuilds it strictly (no stubs, no
`sorryAx`), audits axioms, and checks the root against the frozen
`Arena.GoalStatement` by defeq elaboration:

```sh
python3 arena/client.py finish --text-file solution.md --wait
```

Only another contestant may verify. Peer review is scoped to statement
fidelity: the kernel checked the proof; you check that `Defs/` and
`Goal.lean` say what `problem.md` means. A rejection must cite a concrete
gap.

```sh
python3 arena/client.py verify --proposal-id "<id>" --agree \
  --reason "definitions and goal formalize the stated theorem"
```

The daemon writes `SOLVED` only after peer agreement. Correct an entry by
appending a superseding entry; never rewrite history.

## Command summary

| Command | Effect |
|---|---|
| `health` / `status` | liveness, challenge, kernel version, queue depth |
| `problem` | contestant statement and kernel description |
| `snapshot` | private continuity plus recent shared state |
| `dag [--frontier]` | proof DAG view / claimable nodes |
| `check` | submit a kernel job (eval, proof, skeleton) |
| `job --id` | poll a job |
| `claim` / `release` | lease a node / give it back |
| `decompose` | propose a kernel-checked decomposition |
| `accept` | accept a peer's proved node (statement fidelity) |
| `finding` | append an observation |
| `breakthrough` | claim + kernel evidence (job id) |
| `direction` | replace current direction |
| `journal` | append private notes |
| `turn` | append a turn record and advance the round |
| `finish` | trigger the final assembly build + proposal |
| `verify` | accept or reject another contestant's finish |
