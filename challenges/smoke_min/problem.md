# Double distributes over addition

The frozen definitions layer (`Defs/Basic.lean`, importable as `Defs.Basic`)
defines a function `Arena.double : Nat → Nat` by structural recursion. The
frozen goal (`Goal.lean`) is

```lean
Arena.GoalStatement : Prop :=
  ∀ a b : Nat, Arena.double (a + b) = Arena.double a + Arena.double b
```

## Calibration

These concrete values are certified by kernel computation at daemon startup:

| input | `Arena.double` |
|---|---|
| 0 | 0 |
| 1 | 2 |
| 5 | 10 |
| 21 | 42 |

## Task

Produce a proof of `Arena.GoalStatement`, built as a proof DAG: propose
decompositions (skeleton jobs whose children are `sorry` holes), prove the
leaves, and finish with a root proof. Every claim must cite kernel evidence
(a finished check job id).

## Finish criteria

- The root proof elaborates against `Arena.GoalStatement` (defeq fidelity).
- `#print axioms` of the audited declaration is within
  `{propext, Classical.choice, Quot.sound}` — no `sorryAx`, no new axioms.
- Peer verification: the other contestant confirms the statement and the
  frozen definitions say what this document says (statement fidelity), then
  agrees.
