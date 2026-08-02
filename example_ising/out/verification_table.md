<!-- GENERATED — do not edit; source: example_ising/out/report.jsonl -->

## Gate results

| Gate | Title | Checks | Criteria | Result |
|---|---|---:|---|---|
| manifest | Manifest and independence checks | 2 | manifest validates; declared route imports are independent | 2/2 OK |
| ising-oracle | Independent Ising oracle routes | 90 | stored Z, E0, g0, and signed outputs agree exactly; independent Z, E0, g0, and signed outputs agree exactly; signed equals the positive coefficient sum minus the negative sum | 90/90 OK |
| ising-sym | Ising bijection symmetries | 125 | a one-vertex gauge flip preserves Z coefficientwise; negating all fields preserves Z coefficientwise; full parameter negation replaces x by 1/x; every zero-field coefficient is even; every energy has the parameter-sum parity | 125/125 OK |
| ising-dos | DFT density-of-states construction | 22 | rounded inverse DFT equals exact Z and residual is below tolerance | 22/22 OK |
| ising-gs | Radius ground-state construction | 27 | the extracted E0 and g0 equal the exact oracle values | 27/27 OK |
| ising-conv | Radius convergence probe | 3 | residuals strictly decrease, both decay exponents are at least 1.5, and the final residual is below 1e-2 | 3/3 OK |
| ising-census | Provenance-checked triangle census | 31 | artifact code hashes match all current producer files; the signature is stored and its signed value equals direct enumeration | 31/31 OK |

## Claim ladder

| Claim | Status | Gate evidence | Proof reference |
|---|---|---|---|
| ISING-ORACLE-EQ | exact | ising-oracle: 90/90 OK |  |
| ISING-GAUGE | proved | ising-sym: 125/125 OK | oracle_enum.py docstring (S1) |
| ISING-FLIP | proved | ising-sym: 125/125 OK | oracle_enum.py docstring (S2-S4) |
| ISING-DOS | proved | ising-dos: 22/22 OK | constructions.py docstring (C1) |
| ISING-GS | proved | ising-gs: 27/27 OK | constructions.py docstring (C2) |
| ISING-CONV | numeric — empirical decay rate consistent with the spectral gap; no proof shipped | ising-conv: 3/3 OK |  |
| ISING-CENSUS | exact | ising-census: 31/31 OK |  |
