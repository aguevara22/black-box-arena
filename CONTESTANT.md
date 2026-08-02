# Contestant protocol

You are one independent contestant in a daemon-backed research session. The
daemon is the sole writer of shared state and the only oracle gateway. Use
`arena/client.py` for every interaction; do not edit state files.

Set one configured identity:

```sh
export ORACLE_DAEMON_URL=http://127.0.0.1:8787
export CONTESTANT_ID=claude  # or codex
```

Do not inspect or import `challenges/**/oracle.py` or
`arena/oracle_worker.py`. Oracle answers obtained through the client are the
only contestant-visible ground truth.

## One tick

Do one tick and then yield:

1. Pull a snapshot and read your journal before shared entries.

   ```sh
   python3 arena/client.py snapshot --contestant-id "$CONTESTANT_ID"
   ```

2. Think and compute in your own scratch area. An optional cross-vendor
   advisor can check a hard derivation, but advice is not evidence.

   ```sh
   .venv/bin/python arena/advisor.py --advisor openai --prompt-file question.txt
   ```

3. If the oracle is enabled, predict first when you have a genuine test.

   ```sh
   python3 arena/client.py oracle --contestant-id "$CONTESTANT_ID" \
     --input '{"n":2,"edges":[],"fields":[1,2]}' \
     --predict '{"status":"ok","coefficient":0}' \
     --hypothesis "state the claim tested by this prediction"
   ```

   Every accepted response is correlated by request id and input hash. A
   correct preregistered prediction may promote its hypothesis.

4. Post the useful result and update private continuity.

   ```sh
   python3 arena/client.py finding --contestant-id "$CONTESTANT_ID" --text "..."
   python3 arena/client.py breakthrough --contestant-id "$CONTESTANT_ID" --text "..."
   python3 arena/client.py direction --contestant-id "$CONTESTANT_ID" --text "..."
   python3 arena/client.py journal --contestant-id "$CONTESTANT_ID" --text "..."
   ```

5. Record the turn. Include the oracle calls made during that tick.

   ```sh
   python3 arena/client.py turn --contestant-id "$CONTESTANT_ID" \
     --record '{"reply_text":"short tick summary","oracle_calls":[]}'
   ```

Findings are open observations. A breakthrough candidate is promoted only by
an enabled independent-confirmation, critic, or predictive path. Correct an
entry by appending a superseding entry; never rewrite history.

## Finish and peer verification

A finish gives the complete effective algorithm and correctness argument
required by `problem.md`:

```sh
python3 arena/client.py finish --contestant-id "$CONTESTANT_ID" \
  --text-file solution.md
```

When a challenge config declares `finish_gate`, the proposal must contain
lines in this exact form:

```text
EVIDENCE: input=<json> | tags=<comma-list> | oracle=<int or wall> | proposed=<int or wall> | match=yes|no
```

The daemon parses every evidence line before creating a finish proposal. It
rejects the submission unless at least `min_rows` rows say `match=yes` and
every `required_tags` value occurs on a matching row. A matching wall row is
valid. When `finish_gate` is omitted, the proposal proceeds directly to peer
verification.

Only another contestant may verify:

```sh
python3 arena/client.py verify --contestant-id "$CONTESTANT_ID" \
  --proposal-id "<id>" --agree --reason "checked algorithm and evidence"

python3 arena/client.py verify --contestant-id "$CONTESTANT_ID" \
  --proposal-id "<id>" --reject --reason "concrete gap or failing input"
```

The daemon writes `SOLVED` only after peer agreement.

## Command summary

| Command | Effect |
|---|---|
| `health` / `status` | liveness, challenge, round count, solved flag |
| `problem` | contestant statement and oracle mode |
| `snapshot` | private continuity plus recent shared state |
| `oracle` | sealed query with optional prediction |
| `finding` | append an observation |
| `breakthrough` | submit a promotion candidate |
| `direction` | replace current direction |
| `journal` | append private notes |
| `turn` | append a turn record and advance the round |
| `finish` | submit a complete solution |
| `verify` | accept or reject another contestant's proposal |
