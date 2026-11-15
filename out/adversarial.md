# Adversarial scenario campaign

200 seeded random farms, each with hard events (breakdown mid-job, road closures and uncertain obstacles, urgent jobs, fuel drops, communication loss, delays, single-lane roads). Every coordinator runs the identical scenario. Invariants are checked on what actually ran.

| Coordinator | Jobs completed | Jobs on time | Priority-weighted on time | Travel (min) | Rule violations |
|---|---|---|---|---|---|
| smart | 1316 | 1076 | 2364 | 63365 | 0 |
| fcfs | 1257 | 751 | 1539 | 71284 | 0 |
| nearest | 1253 | 625 | 1270 | 74843 | 0 |

SYNTERRA not worse than FCFS on priority-weighted on-time work: **192/200**

## Findings

- smart: worse than baseline in 8 scenario(s): seeds [2, 17, 25, 35, 50, 99, 169, 185]

Bugs this campaign found during development (all fixed, and covered by tests): implement hand-over claimed before it was committed; two queued jobs in one field tracked wrongly; a drive longer than the fuel left; unreported blackout work not counted as repeated work; tractors only driving to a low-priority job could not be redirected after a breakdown; a cancelled implement hand-over left the receiving tractor's trip active; a stopped tractor cleared an implement it did not hold; a tractor stranded without fuel locked the only sprayer (now: fuel delivery after recovery_minutes).

Clearest remaining failure: seed 35 - on-time priority 11 < 12 (FCFS)
