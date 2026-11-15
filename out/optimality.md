# How close is SYNTERRA to the best possible plan?

60 seeded farms (4-8 jobs, 2-3 tractors, no events). For each, the TRUE optimum of the same objective was found by exact branch-and-bound (admissible bound, exact duplicate merging; cross-checked against plain exhaustive search in the tests) and compared with what SYNTERRA commits in normal operation.

Objective, in strict order: (1) priority finished on time, (2) priority finished, (3) priority-weighted lateness, (4) cost (travel, idle, service stops, plan changes, early finish of priority work, deadline slack).

- Optimal on goals 1-3 (the work that matters): **60/60**
- Optimal on all four goals including cost: **50/60**
- Average cost gap to optimum: 1.121% (worst 17.35%)

- Average time: SYNTERRA 0.32 s per plan, exact solver 1.20 s (exact is only used here, never in the live demo)

How often each candidate source alone reached the optimum on goals 1-3:

| Source | Optimal on goals 1-3 |
|---|---|
| beam | 56/60 |
| greedy | 33/60 |
| hungarian | 30/60 |
| kvac-wfa | 25/60 |
| fcfs | 23/60 |
| nearest | 13/60 |

Note: the plan SYNTERRA finally commits may trade a little cost for robustness to a tractor failure (hindsight fragility check); goals 1-3 are never traded.

