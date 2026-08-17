/- Frozen goal: the Fibonacci addition formula for Arena.fib.
   Stated as a Prop definition; the winning root proof must elaborate
   against it (defeq), so statement text is never compared. -/
import Defs.Fib

namespace Arena

def GoalStatement : Prop :=
  ∀ m n : Nat,
    Arena.fib (m + n + 1) =
      Arena.fib (m + 1) * Arena.fib (n + 1) + Arena.fib m * Arena.fib n

end Arena
