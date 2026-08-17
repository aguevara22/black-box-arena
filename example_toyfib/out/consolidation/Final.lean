import Defs.Basic
import Goal

-- node n_ebe204434b85d15a (double_zero)

theorem double_zero : Arena.double 0 = 0 := rfl

-- node n_93843b277e17d4d3 (double_succ)

theorem double_succ : ∀ n : Nat, Arena.double (n + 1) = Arena.double n + 2 := fun n => rfl

-- node n_f5e546e03449e586 (goal_root)

theorem goal_root : Arena.GoalStatement := by
  intro a b
  induction b with
  | zero => simp [double_zero]
  | succ n ih =>
      rw [Nat.add_succ, double_succ, double_succ, ih]
      omega

-- consolidation audit
theorem _consolidation_goal_check : Arena.GoalStatement := goal_root
