<!-- GENERATED — do not edit; source: /Users/alfredoguevara/Library/Mobile Documents/com~apple~CloudDocs/colaborative proof/black-box-arena/example_toyfib/out/report.jsonl -->

## Gate results

| Gate | Title | Checks | Criteria | Result |
|---|---|---:|---|---|
| manifest | Manifest and independence checks | 2 | manifest validates; declared route imports are independent | 2/2 OK |
| final-build | Consolidation project builds; axioms audited | 5 | lake build exits 0; axioms within ['propext', 'Classical.choice', 'Quot.sound'] | 5/5 OK |
| dag-lint | Arena DAG structural lint | 1 | no structural problems | 1/1 OK |

## Claim ladder

| Claim | Status | Gate evidence | Proof reference |
|---|---|---|---|
| NODE-goal_root | proved — kernel-proved in the arena; re-verified here from scratch | final-build: 5/5 OK; dag-lint: 1/1 OK | state proofs/n_f5e546e03449e586.lean |
| NODE-double_zero | proved — kernel-proved in the arena; re-verified here from scratch | final-build: 5/5 OK; dag-lint: 1/1 OK | state proofs/n_ebe204434b85d15a.lean |
| NODE-double_succ | proved — kernel-proved in the arena; re-verified here from scratch | final-build: 5/5 OK; dag-lint: 1/1 OK | state proofs/n_93843b277e17d4d3.lean |
| GOAL-FIDELITY | proved — defeq elaboration in the consolidation build | final-build: 5/5 OK | Final.lean :: _consolidation_goal_check |
