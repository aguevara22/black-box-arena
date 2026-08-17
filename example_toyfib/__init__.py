"""Evidence-kit consolidation instance for a solved proof-arena run.

Defaults to the toy_fib_add challenge; point it elsewhere with
  ARENA_CONSOLIDATE_CHALLENGE=<name>   (challenge package to consolidate)
  ORACLE_STATE_ROOT=<dir>              (where the solved state lives)

The instance re-verifies the accepted solution FROM SCRATCH, outside the
daemon, by its own code path: it re-reads dag.jsonl + proofs/, re-assembles
the final Lean file itself, builds it in a fresh lake project, and audits
axioms — an independent route to the same verdict the arena reached.
"""
