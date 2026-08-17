/- Frozen goal for the smoke_min challenge. The target is stated as a Prop
   definition; the winning root proof must elaborate against it (defeq), so
   the daemon never needs to compare statement text. -/
import Defs.Basic

namespace Arena

def GoalStatement : Prop :=
  ∀ a b : Nat, Arena.double (a + b) = Arena.double a + Arena.double b

end Arena
