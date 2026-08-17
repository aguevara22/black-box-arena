/- Calibration anchors, certified by kernel computation at daemon startup.

   The last two rows are instances of the goal formula itself at (m,n) =
   (3,4) and (4,3): fib 8 = fib 4 * fib 5 + fib 3 * fib 4 both ways. Their
   agreement under swapping m and n is a symmetry of the hidden structure —
   the formula is symmetric even though its right-hand side is not
   syntactically so. Treat that as a hint, not an accident. -/
import Defs.Fib

example : Arena.fib 1 = 1 := by decide
example : Arena.fib 2 = 1 := by decide
example : Arena.fib 5 = 5 := by decide
example : Arena.fib 10 = 55 := by decide
example : Arena.fib 12 = 144 := by decide

-- goal instances at (m,n) = (3,4) and (4,3)
example : Arena.fib 8 = Arena.fib 4 * Arena.fib 5 + Arena.fib 3 * Arena.fib 4 := by decide
example : Arena.fib 8 = Arena.fib 5 * Arena.fib 4 + Arena.fib 4 * Arena.fib 3 := by decide
