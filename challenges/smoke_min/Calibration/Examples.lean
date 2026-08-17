/- Calibration anchors: concrete values checked by kernel computation.
   These must compile at daemon startup — they pin the frozen definitions to
   the meaning problem.md describes. If a refactor of Defs breaks these, the
   daemon refuses to serve. -/
import Defs.Basic

example : Arena.double 0 = 0 := by decide
example : Arena.double 1 = 2 := by decide
example : Arena.double 5 = 10 := by decide
example : Arena.double 21 = 42 := by decide
