/- Frozen definitions for the smoke_min challenge. Contestants may import
   and use these; they may not redefine them. `Arena.double` is deliberately
   defined by recursion (not as `2 * n`) so that the goal is not `rfl`-trivial
   and the decomposition machinery has something to bite on. -/

namespace Arena

def double : Nat → Nat
  | 0 => 0
  | n + 1 => double n + 2

end Arena
