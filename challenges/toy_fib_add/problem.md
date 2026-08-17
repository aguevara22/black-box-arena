# The Fibonacci addition formula

The frozen definitions layer (`Defs/Fib.lean`, importable as `Defs.Fib`)
defines `Arena.fib : Nat → Nat` by the standard double recursion
(`fib 0 = 0`, `fib 1 = 1`, `fib (n+2) = fib (n+1) + fib n`). It is
deliberately not any library's `fib`; the formula must be proved about this
function.

The frozen goal (`Goal.lean`) is

```lean
Arena.GoalStatement : Prop :=
  ∀ m n : Nat,
    Arena.fib (m + n + 1) =
      Arena.fib (m + 1) * Arena.fib (n + 1) + Arena.fib m * Arena.fib n
```

## Calibration

Certified by kernel computation at daemon startup:

| claim | value |
|---|---|
| `fib 1` | 1 |
| `fib 2` | 1 |
| `fib 5` | 5 |
| `fib 10` | 55 |
| `fib 12` | 144 |
| `fib 8 = fib 4 * fib 5 + fib 3 * fib 4` | goal at (m,n)=(3,4) |
| `fib 8 = fib 5 * fib 4 + fib 4 * fib 3` | goal at (m,n)=(4,3) |

The last two rows agree under swapping `m` and `n` although the right-hand
side is not syntactically symmetric. That is a symmetry of the underlying
structure — treat it as a hint about the proof, not an accident.

## Task

Build the proof as a DAG: propose decompositions (skeleton jobs whose
children are `sorry` holes), claim leaves via leases, prove them, and finish
with a root proof. Every claim must cite kernel evidence (a check job id).
A two-variable induction with a simultaneous-pair strengthening is one known
route; finding a decomposition whose leaves are individually easy is the
actual work.

## Finish criteria

- The root proof elaborates against `Arena.GoalStatement` (defeq fidelity).
- `#print axioms` of the audited declaration stays within
  `{propext, Classical.choice, Quot.sound}` — no `sorryAx`, no new axioms.
- Peer verification is statement fidelity: the other contestant confirms
  `Defs/Fib.lean` and `Goal.lean` formalize the formula stated here, then
  agrees.
