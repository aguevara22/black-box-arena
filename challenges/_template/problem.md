# Replace this title

State the theorem informally here, then point at the frozen formal artifacts:

- `Defs/` — the definitions layer (what the objects are). Importable as
  `Defs.<Module>`; contestants may use it, never redefine it.
- `Goal.lean` — the target, stated as `def Arena.GoalStatement : Prop := ...`.
  The winning root proof must elaborate against it (defeq fidelity).

## Calibration

Give a table of concrete instances certified by kernel computation in
`Calibration/` (they compile at daemon startup, or the daemon refuses to
serve). Include at least two rows related by a nontrivial symmetry of the
underlying object, and say so.

## Task

Build the proof as a DAG: propose decompositions (skeleton jobs — the parent
proved from children declared `sorry`), claim leaves, prove them, and finish
with a root proof. Every claim must cite kernel evidence (a check job id).

## Finish criteria

- Root proof elaborates against `Arena.GoalStatement`.
- `#print axioms` of the audited declaration stays within the allowed set —
  no `sorryAx`, no new axioms.
- Peer verification is statement fidelity: the other contestant confirms
  `Defs/` and `Goal.lean` formalize what this document says, then agrees.
