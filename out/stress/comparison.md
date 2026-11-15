# Comparison: SYNTERRA stress farm (all event types)

Same farm, same jobs, same events, same hard rules (capability, implement, fuel, battery, roads) for every coordinator. Every number below is computed from the tasks that actually ran (`mem.history`) and the event log, never from plans.

| Measure | SYNTERRA (ours) | Baseline A: first come, first served | Baseline B: nearest compatible tractor |
|---|---|---|---|
| Jobs completed | 11 | 11 | 11 |
| Jobs finished on time | **8** | 5 | 6 |
| Priority-weighted work on time | **17** | 9 | 12 |
| Jobs finished late | **3** | 6 | 5 |
| Jobs not finished | 0 | 0 | 0 |
| Travel time (min) | **598** | 728 | 654 |
| Distance driven (km) | **149.5** | 182 | 163.5 |
| Idle while on duty (min) | 66 | **27** | **27** |
| Spare capacity after own work (min) | 58 | 141 | 77 |
| All work finished after (min) | **645** | 761 | 674 |
| Fuel used (min of engine time) | **1101** | 1249 | 1154 |
| Refuel stops | **2** | 4 | 3 |
| Recharge stops | 2 | 2 | 2 |
| Implement changes | **9** | 10 | 10 |
| Repeated or lost work (min) | 3 | 21 | **0** |
| Avg time until interrupted work resumes (min) | 142.5 | 81.3 | 75 |
| Rule violations in what actually ran | 0 | 0 | 0 |
| Deadline warnings raised in plans | 15 | 49 | 36 |
| Plans rejected by the safety agent | 0 | 0 | 0 |
| Re-plans | 13 | 12 | 11 |
| Slowest re-plan (ms) | 694.9 | 1.2 | 3.5 |

## Against the first-come-first-served baseline

- Jobs on time: 8 vs 5 (+3).
- Travel: 598 vs 728 min (-18%).
- All work finished after 645 vs 761 min.
- Idle while on duty: 66 vs 27 min.
- Rule violations in what actually ran: 0 vs 0.
- Where SYNTERRA did worse than the baseline here: Idle while on duty (min).

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

**07:00 - tractor delayed (plan v2)**

- T1 is running 12 min late on J2 (now done at 07:14 instead of 07:02).
- J8 stays with T1 but now starts at 07:42 (+12 min).
- J5 stays with T1 but now starts at 08:43 (+12 min).
- Everything else stays as planned (5 jobs).
- Work already under way is kept as it is (J2, J4, J7).

**07:35 - tractor failed (plan v3)**

- Tractor T2 broke down at 07:35 while mowing F7 (J9). 0 of 7 sections (0 of 35 min) were confirmed done and are kept.
- J9 moved to T1; only the remaining 35 of 35 minutes are needed (0 sections already done). T1 was chosen because it can mow with a mower; T3 could only finish after the deadline; it is done 11 min before the 12:40 deadline.
- J1 moved from T2 to T1 because it can mow with a mower; T3 could only finish after the deadline; it is done 25 min before the 14:00 deadline.
- Everything else stays as planned (4 jobs).
- Work already under way is kept as it is (J7, J8).

**08:00 - communication lost (plan v4)**

- No messages from T3 since 08:00. Its work is NOT handed over yet: T3 stops itself if it hears nothing for 15 min. Decision: wait for lease (a check would cost more than it saves; wait for the safety lease). Other jobs carry on.
- J6 moved from T3 to T1 because it can mow with a mower; it can only finish 119 min after the 13:00 deadline.
- J10 cannot be scheduled right now: no working tractor can do a seed job.
- Warning: J6 will finish after the deadline; this is reported, not hidden.
- Everything else stays as planned (3 jobs).
- Work already under way is kept as it is (J3, J8).

**08:15 - lease expired (plan v5)**

- T3 has been silent for 15 min, so by protocol it has stopped. Work it confirmed before going silent is kept; the rest can now be reassigned safely.
- J10 cannot be scheduled right now: no working tractor can do a seed job.
- J3 cannot be scheduled right now: no working tractor can do a seed job.
- Warning: J6 will finish after the deadline; this is reported, not hidden.
- Everything else stays as planned (4 jobs).
- Work already under way is kept as it is (J8).

**08:30 - route blocked (plan v6)**

- The road F4-F7 was blocked at 08:30; it is closed for planning.
- J10 cannot be scheduled right now: no working tractor can do a seed job.
- J3 cannot be scheduled right now: no working tractor can do a seed job.
- Warning: J6 will finish after the deadline; this is reported, not hidden.
- Everything else stays as planned (3 jobs).
- Work already under way is kept as it is (J5).

**08:30 - communication restored (plan v7)**

- T3 reconnected at 08:30. It reported 3 extra finished section(s), now counted as done.
- J3 is now planned on T3 at 09:52 for the remaining 45 of 60 min because it can seed with a seeder; T1 cannot take it; it is done 23 min before the 11:00 deadline.
- J6 moved from T1 to T3 because it can mow with a mower; it drives 56 min instead of 68 for T1; it is done 22 min before the 13:00 deadline.
- J10 is now planned on T3 at 13:48 because it can seed with a seeder; T1 cannot take it; it can only finish 48 min after the 13:50 deadline.
- Warning: J10 will finish after the deadline; this is reported, not hidden.
- Everything else stays as planned (2 jobs).
- Work already under way is kept as it is (J5).

**08:50 - priority changed (plan v8)**

- The operator changed J8's priority from 1 to 3 at 08:50.
- Warning: J10 will finish after the deadline; this is reported, not hidden.
- Everything else stays as planned (4 jobs).
- Work already under way is kept as it is (J3, J5).

**09:00 - obstacle detected (plan v9)**

- The road F3-YARD was reported as possibly blocked at 09:00; it is closed for planning until confirmed clear. Decision: verify (worth checking: planned trips would save about 24 min if the road is clear).
- J10 stays with T3 but now starts at 14:12 (+24 min).
- Warning: J10 will finish after the deadline; this is reported, not hidden.
- Everything else stays as planned (3 jobs).
- Work already under way is kept as it is (J3, J5).

**09:08 - obstacle verified (plan v10)**

- The scout check at 09:08 found road F3-YARD clear; it is open again.
- J10 stays with T3 but now starts at 13:48 (-24 min).
- Warning: J10 will finish after the deadline; this is reported, not hidden.
- Everything else stays as planned (3 jobs).
- Work already under way is kept as it is (J3, J5).

**09:20 - job submitted (plan v11)**

- A new urgent spray job J11 for F2 arrived at 09:20 (deadline 11:30). To finish more high-priority work on time, T3 (was driving to J3) changed course before starting work; nothing finished is affected.
- J11 is now planned on T3 at 10:55 because it can spray with a sprayer; T1 could only finish after the deadline; it is done 5 min before the 11:30 deadline.
- J10 stays with T3 but now starts at 12:34 (-74 min).
- J3 is now planned on T3 at 14:40 for the remaining 45 of 60 min because it can seed with a seeder; T1 cannot take it; it can only finish 265 min after the 11:00 deadline.
- J6 moved from T3 to T1 because it can mow with a mower; T3 could only finish after the deadline; it can only finish 119 min after the 13:00 deadline.
- Warning: J3, J6 will finish after the deadline; this is reported, not hidden.
- Everything else stays as planned (2 jobs).
- Work already under way is kept as it is (J5).

**09:50 - low energy (plan v12)**

- T1 reported low fuel (15 min left) at 09:50. It paused J9 with 0 of 35 min done (kept).
- J9 moved to T3; only the remaining 35 of 35 minutes are needed (0 sections already done). T3 was chosen because it can mow with a mower; T1 cannot take it; it is done 77 min before the 12:40 deadline.
- J10 stays with T3 but now starts at 12:56 (+22 min).
- J3 stays with T3 but now starts at 15:02 (+22 min).
- J6 moved from T1 to T3 because it can mow with a mower; T1 cannot take it; it can only finish 273 min after the 13:00 deadline.
- J1 moved from T1 to T3 because it can mow with a mower; T1 cannot take it; it can only finish 362 min after the 14:00 deadline.
- J11 cannot be scheduled right now: no free sprayer, or not enough fuel or battery.
- Warning: J3, J6, J1 will finish after the deadline; this is reported, not hidden.

**10:20 - fuel delivered (plan v13)**

- A fuel delivery reached T1 at F7 at 10:20; it is back in service.
- J6 moved from T3 to T1 because it can mow with a mower; T3 could only finish after the deadline; it is done 22 min before the 13:00 deadline.
- J11 is now planned on T1 at 13:32 because it can spray with a sprayer; T3 could only finish after the deadline; it can only finish 152 min after the 11:30 deadline.
- J1 moved from T3 to T1 because it can mow with a mower; T3 could only finish after the deadline; it can only finish 165 min after the 14:00 deadline.
- Warning: J11, J1, J3 will finish after the deadline; this is reported, not hidden.
- Everything else stays as planned (2 jobs).
- Work already under way is kept as it is (J9).

## Decisions made under uncertainty

- 08:00 comm_loss: **wait_for_lease** - a check would cost more than it saves; wait for the safety lease (value of information -4.4; assumptions: {'lease_minutes': 15, 'verify_minutes': 8, 'verify_cost': 10.0, 'priority': 2}, hypotheses: {'still_working': 0.6, 'communication_failure_stopped': 0.1, 'mechanical_breakdown': 0.25, 'implement_failure': 0.05})
- 09:00 obstacle: **verify** - worth checking: planned trips would save about 24 min if the road is clear (value of information 2.0; assumptions: {'verify_cost': 10.0, 'verify_minutes': 8}, hypotheses: {'clear': 0.5, 'blocked': 0.5})

## Notes

- Idle while on duty = time a tractor waited (for an implement, a field or a road) before its own last task finished. Spare capacity = time after its own work was done while others still worked.
- Repeated or lost work = minutes inside a section that was interrupted before it finished, plus sections done twice after a communication loss. Finished sections are never reset.
- Deadline warnings are reported by the safety agent and shown, never hidden.
