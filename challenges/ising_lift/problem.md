# Weighted-Graph Quantum Lift

An input is a finite weighted graph with integer edge couplings and one integer
field at each vertex:

```json
{"n": 3, "edges": [[0,1,-1],[0,2,-1],[1,2,-1]], "fields": [0,0,0]}
```

Vertices are numbered `0` through `n-1`. Every edge is `[i,j,J]` with
`0 <= i < j < n` and nonzero integer `J`; repeated edges are invalid. The
field list has length `n`. Valid sizes satisfy `1 <= n <= 16`.

A sealed oracle returns either an integer classical coefficient `C(G)` or
`wall` on a degenerate input.

Your task is to identify and construct an effective quantum invariant: a
Laurent polynomial `Omega_G(x)` computable for every valid input. Give the
extraction or limit that provably reproduces `C(G)`, and give an algorithm
that produces all coefficients of `Omega_G(x)`.

## Calibration

| n | edges | fields | C(G) |
|---:|---|---|---:|
| 2 | `[[0,1,1]]` | `[0,0]` | 0 |
| 3 | `[[0,1,-1],[0,2,-1],[1,2,-1]]` | `[0,0,0]` | -4 |
| 3 | `[[0,1,1],[0,2,1],[1,2,-1]]` | `[0,0,0]` | -4 |
| 3 | `[[0,1,1],[0,2,1],[1,2,1]]` | `[0,0,0]` | 4 |
| 2 | `[]` | `[1,2]` | 0 |

Rows two and three are related by a change of variables. Identify and exploit
that relation rather than treating the agreement as accidental.

## Finish criteria

A finish must include:

1. An effective coefficient-producing algorithm for `Omega_G(x)`.
2. A correctness argument for the construction and the stated extraction.
3. Live oracle checks in exactly this one-line format:

```text
EVIDENCE: input=<json> | tags=<comma-list> | oracle=<int or wall> | proposed=<int or wall> | match=yes|no
```

Every row must parse. At least 12 rows must have `match=yes`, and matching rows
collectively must cover these tags: `n2`, `n3`, `n4`, `n5`, `fields`,
`nofields`, `frustrated`, `leak_pair`, `wall_checked`, and `disconnected`.
A checked wall row counts when both values are `wall` and `match=yes`.
