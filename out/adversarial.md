# Adversarial scenario campaign

500 seeded random farms, each with hard events (breakdown mid-job, road closures and uncertain obstacles, urgent jobs, fuel drops, communication loss, delays, single-lane roads). Every coordinator runs the identical scenario. Invariants are checked on what actually ran.

| Coordinator | Jobs completed | Jobs on time | Priority-weighted on time | Travel (min) | Rule violations |
|---|---|---|---|---|---|
| smart | 3202 | 2648 | 5807 | 153056 | 0 |
| fcfs | 3057 | 1852 | 3795 | 172369 | 0 |
| nearest | 3043 | 1570 | 3228 | 178989 | 0 |

SYNTERRA not worse than FCFS on priority-weighted on-time work: **478/500**

## Findings

- smart: worse than baseline in 22 scenario(s): seeds [2, 17, 25, 35, 50, 73, 99, 169, 185, 213, 284, 287, 315, 349, 374]

Bugs this campaign found during development (all fixed, and covered by tests): implement hand-over claimed before it was committed; two queued jobs in one field tracked wrongly; a drive longer than the fuel left; unreported blackout work not counted as repeated work; tractors only driving to a low-priority job could not be redirected after a breakdown; a cancelled implement hand-over left the receiving tractor's trip active; a stopped tractor cleared an implement it did not hold; a tractor stranded without fuel locked the only sprayer (now: fuel delivery after recovery_minutes); a delay pushed back only the first tractor queued behind, not the whole chain.

Clearest remaining failure: seed 495 - on-time priority 4 < 5 (FCFS)
