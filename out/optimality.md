# How close is SYNTERRA to the best possible plan?

30 small seeded farms (4-6 jobs, 2-3 tractors, no events). For each, the TRUE optimum of the same objective was found by exhaustive search (every order and tractor choice, merging only states that are exactly identical), and compared with SYNTERRA's normal search (beam width 30 + seeds).

Objective, in strict order: (1) priority finished on time, (2) priority finished, (3) priority-weighted lateness, (4) cost (travel, idle, service stops, plan changes, early finish of priority work, deadline slack).

- Optimal on goals 1-3 (the work that matters): **29/30**
- Optimal on all four goals including cost: **24/30**
- Average cost gap to optimum: 1.083% (worst 21.43%)

How often each candidate source alone reached the optimum on goals 1-3:

| Source | Optimal on goals 1-3 |
|---|---|
| beam | 25/30 |
| greedy | 15/30 |
| fcfs | 14/30 |
| kvac-wfa | 12/30 |
| nearest | 7/30 |

Note: the plan SYNTERRA finally commits may trade a little cost for robustness to a tractor failure (hindsight fragility check); goals 1-3 are never traded.

