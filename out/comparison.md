# Comparison: Field Day demo farm

Same farm, same jobs, same events, same hard rules (capability, implement, fuel, battery, roads) for every coordinator. Every number below is computed from the tasks that actually ran (`mem.history`) and the event log, never from plans.

| Measure | SYNTERRA (ours) | Baseline A: first come, first served | Baseline B: nearest compatible tractor |
|---|---|---|---|
| Jobs completed | 10 | 10 | 10 |
| Jobs finished on time | **10** | 7 | 7 |
| Priority-weighted work on time | **20** | 14 | 14 |
| Jobs finished late | **0** | 3 | 3 |
| Jobs not finished | 0 | 0 | 0 |
| Travel time (min) | **356** | 544 | 484 |
| Distance driven (km) | **89** | 136 | 121 |
| Idle while on duty (min) | 0 | 0 | 0 |
| Spare capacity after own work (min) | 35 | 18 | 56 |
| All work finished after (min) | **443** | 558 | 540 |
| Fuel used (min of engine time) | **826** | 1018 | 954 |
| Refuel stops | **2** | 3 | 3 |
| Recharge stops | 2 | 2 | 2 |
| Implement changes | **5** | 9 | 8 |
| Repeated or lost work (min) | **0** | 4 | **0** |
| Avg time until interrupted work resumes (min) | 247 | 128 | - |
| Rule violations in what actually ran | 0 | 0 | 0 |
| Deadline warnings raised in plans | 0 | 10 | 12 |
| Plans rejected by the safety agent | 0 | 0 | 0 |
| Re-plans | 4 | 4 | 4 |
| Slowest re-plan (ms) | 933.4 | 1.3 | 1.4 |

## Against the first-come-first-served baseline

- Jobs on time: 10 vs 7 (+3).
- Travel: 356 vs 544 min (-35%).
- All work finished after 443 vs 558 min.
- Idle while on duty: 0 vs 0 min.
- Rule violations in what actually ran: 0 vs 0.
- SYNTERRA was not worse than the baseline on any scored measure in this scenario.

## Plan changes and why (SYNTERRA)

**06:00 - initial (plan v1)**

- Starting plan: 10 jobs on 3 tractors, 268 min of driving, every job before its deadline.
- J2 (spray, F1) -> T1, 06:22-07:02: it can spray with a sprayer; it keeps the fleet's total plan better (next best: T3); T2 cannot take it; it is done 88 min before the 08:30 deadline.
- J4 (mow, F2) -> T2, 06:34-07:19: it can mow with a mower; it keeps the fleet's total plan better (next best: T3); it is done 161 min before the 10:00 deadline.
- J7 (seed, F4) -> T3, 06:42-07:37: it can seed with a seeder; it finishes at 07:37, 103 min sooner than T2; T1 cannot take it; it is done 163 min before the 10:20 deadline.
- J3 (seed, F6) -> T3, 07:57-08:57: it can seed with a seeder; T1 and T2 cannot take it; it is done 123 min before the 11:00 deadline.
- J9 (mow, F7) -> T2, 07:43-08:18: it can mow with a mower; it finishes at 08:18, 29 min sooner than T1; it is done 262 min before the 12:40 deadline.
- J8 (spray, F5) -> T1, 07:30-08:15: it can spray with a sprayer; T2 and T3 cannot take it; it is done 315 min before the 13:30 deadline.
- J5 (spray, F7) -> T1, 08:31-09:21: it can spray with a sprayer; T2 and T3 cannot take it; it is done 129 min before the 11:30 deadline.
- J1 (mow, F8) -> T2, 08:34-09:24: it can mow with a mower; it finishes at 09:24, 168 min sooner than T3; it is done 276 min before the 14:00 deadline.
- J10 (seed, F3) -> T3, 10:13-11:03: it can seed with a seeder; T1 and T2 cannot take it; it is done 167 min before the 13:50 deadline.
- J6 (mow, F3) -> T3, 12:08-12:48: it can mow with a mower; it drives 40 min instead of 68 for T1; it is done 12 min before the 13:00 deadline.

**07:35 - tractor failed (plan v2)**

- Tractor T2 broke down at 07:35 while mowing F7 (J9). 0 of 7 sections (0 of 35 min) were confirmed done and are kept.
- J9 moved to T1; only the remaining 35 of 35 minutes are needed (0 sections already done). T1 was chosen because it can mow with a mower; T3 could only finish after the deadline; it is done 23 min before the 12:40 deadline.
- J1 moved from T2 to T1 because it can mow with a mower; it finishes at 13:23, 19 min sooner than T3; it is done 37 min before the 14:00 deadline.
- Everything else stays as planned (4 jobs).
- Work already under way is kept as it is (J7, J8).

**08:30 - route blocked (plan v3)**

- The road F4-F7 was blocked at 08:30; it is closed for planning.
- Everything else stays as planned (4 jobs).
- Work already under way is kept as it is (J3, J5).

**08:50 - priority changed (plan v4)**

- The operator changed J8's priority from 1 to 3 at 08:50.
- Everything else stays as planned (4 jobs).
- Work already under way is kept as it is (J3, J5).

## Notes

- Idle while on duty = time a tractor waited (for an implement, a field or a road) before its own last task finished. Spare capacity = time after its own work was done while others still worked.
- Repeated or lost work = minutes inside a section that was interrupted before it finished, plus sections done twice after a communication loss. Finished sections are never reset.
- Deadline warnings are reported by the safety agent and shown, never hidden.
