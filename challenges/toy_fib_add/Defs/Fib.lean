/- Frozen definitions for the toy_fib_add challenge.

   `Arena.fib` is deliberately its own definition — NOT `Nat.fib` — so the
   challenge exercises the frozen-Defs mechanism: the formula must be proved
   about THIS function, whose meaning is pinned by the calibration examples,
   not about whatever a library happens to provide. -/

namespace Arena

def fib : Nat → Nat
  | 0 => 0
  | 1 => 1
  | n + 2 => fib (n + 1) + fib n

end Arena
