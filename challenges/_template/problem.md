# Problem: replace this title

State the task, valid inputs, sealed-oracle outputs, calibration rows, and the
effective construction expected from contestants. Include a deliberate
symmetry clue without disclosing the answer.

## Interaction

Use `arena/client.py` for snapshots, oracle calls, findings, breakthroughs,
turn records, finish proposals, and peer verification. Do not inspect
`oracle.py`.

## Finish criteria

If `config.yaml` enables `finish_gate`, include one line per checked case:

```text
EVIDENCE: input=<json> | tags=<comma-list> | oracle=<int or wall> | proposed=<int or wall> | match=yes|no
```

Every such line must parse. At least `min_rows` lines must say `match=yes`,
and every configured tag must occur on a matching line.
