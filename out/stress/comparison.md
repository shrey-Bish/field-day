# Comparison: SYNTERRA stress farm (all event types)

Same farm, same jobs, same events, same hard rules (capability, implement, fuel, battery, roads) for every coordinator. Every number below is computed from the tasks that actually ran (`mem.history`) and the event log, never from plans.

| Measure | SYNTERRA (ours) | Baseline A: first come, first served | Baseline B: nearest compatible tractor |
|---|---|---|---|
| Jobs completed | 11 | 11 | 11 |
| Jobs finished on time | **9** | 5 | 7 |
| Priority-weighted work on time | **19** | 9 | 14 |
| Jobs finished late | **2** | 6 | 4 |
| Jobs not finished | 0 | 0 | 0 |
| Travel time (min) | **476** | 728 | 644 |
| Distance driven (km) | **119** | 182 | 161 |
| Idle while on duty (min) | 27 | 27 | 27 |
| Spare capacity after own work (min) | 37 | 141 | 67 |
| All work finished after (min) | **539** | 761 | 664 |
| Fuel used (min of engine time) | **979** | 1249 | 1144 |
| Refuel stops | **2** | 4 | 3 |
| Recharge stops | 2 | 2 | 2 |
| Implement changes | **6** | 10 | 10 |
| Repeated or lost work (min) | 3 | 21 | **0** |
| Avg time until interrupted work resumes (min) | 160 | 81.3 | 70 |
| Rule violations in what actually ran | 0 | 0 | 0 |
| Deadline warnings raised in plans | 11 | 49 | 35 |
| Plans rejected by the safety agent | 0 | 0 | 0 |
| Re-plans | 12 | 12 | 11 |
| Slowest re-plan (ms) | 141.1 | 0.9 | 1 |

## Against the first-come-first-served baseline

- Jobs on time: 9 vs 5 (+4).
- Travel: 476 vs 728 min (-35%).
- All work finished after 539 vs 761 min.
- Idle while on duty: 27 vs 27 min.
- Rule violations in what actually ran: 0 vs 0.
- SYNTERRA was not worse than the baseline on any scored measure in this scenario.

## Plan changes and why (SYNTERRA)

**06:00 - initial (plan v1)**

- Starting plan: 10 jobs on 3 tractors, 276 min of driving, every job before its deadline.
- J2 (spray, F1) -> T1, 06:22-07:02: it can spray with a sprayer; it keeps the fleet's total plan better (next best: T3); T2 cannot take it; it is done 88 min before the 08:30 deadline.
- J4 (mow, F2) -> T2, 06:34-07:19: it can mow with a mower; it keeps the fleet's total plan better (next best: T3); it is done 161 min before the 10:00 deadline.
- J7 (seed, F4) -> T3, 06:42-07:37: it can seed with a seeder; it finishes at 07:37, 103 min sooner than T2; T1 cannot take it; it is done 163 min before the 10:20 deadline.
- J3 (seed, F6) -> T3, 07:57-08:57: it can seed with a seeder; T1 and T2 cannot take it; it is done 123 min before the 11:00 deadline.
- J5 (spray, F7) -> T1, 07:38-08:28: it can spray with a sprayer; T2 and T3 cannot take it; it is done 182 min before the 11:30 deadline.
- J9 (mow, F7) -> T2, 08:28-09:03: it can mow with a mower; it finishes at 09:03, 121 min sooner than T1; it is done 217 min before the 12:40 deadline.
- J1 (mow, F8) -> T2, 09:19-10:09: it can mow with a mower; it finishes at 10:09, 86 min sooner than T1; it is done 231 min before the 14:00 deadline.
- J8 (spray, F5) -> T1, 08:44-09:29: it can spray with a sprayer; T2 and T3 cannot take it; it is done 241 min before the 13:30 deadline.
- J10 (seed, F3) -> T3, 10:13-11:03: it can seed with a seeder; T1 and T2 cannot take it; it is done 167 min before the 13:50 deadline.
- J6 (mow, F3) -> T3, 12:08-12:48: it can mow with a mower; it drives 40 min instead of 60 for T1; it is done 12 min before the 13:00 deadline.

**07:00 - tractor delayed (plan v2)**

- T1 is running 12 min late on J2 (now done at 07:14 instead of 07:02).
- J8 stays with T1 but now starts at 07:42 (-62 min).
- J9 stays with T2 but now starts at 07:43 (-45 min).
- J5 stays with T1 but now starts at 08:43 (+65 min).
- J1 stays with T2 but now starts at 08:34 (-45 min).
- J6 stays with T3 but now starts at 10:03 (-125 min).
- J10 stays with T3 but now starts at 12:08 (+115 min).
- Everything else stays as planned (1 job).
- Work already under way is kept as it is (J2, J4, J7).

**07:35 - tractor failed (plan v3)**

- Tractor T2 broke down at 07:35 while mowing F7 (J9). 0 of 7 sections (0 of 35 min) were confirmed done and are kept.
- J10 stays with T3 but now starts at 10:13 (-115 min).
- J6 stays with T3 but now starts at 12:08 (+125 min).
- J9 moved to T1; only the remaining 35 of 35 minutes are needed (0 sections already done). T1 was chosen because it can mow with a mower; T3 could only finish after the deadline; it is done 11 min before the 12:40 deadline.
- J1 moved from T2 to T1 because it can mow with a mower; T3 could only finish after the deadline; it is done 25 min before the 14:00 deadline.
- Everything else stays as planned (2 jobs).
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

- A new urgent spray job J11 for F2 arrived at 09:20 (deadline 11:30).
- J10 stays with T3 but now starts at 12:08 (-100 min).
- J11 is now planned on T3 at 13:52 because it can spray with a sprayer; T1 could only finish after the deadline; it can only finish 172 min after the 11:30 deadline.
- J6 moved from T3 to T1 because it can mow with a mower; T3 could only finish after the deadline; it can only finish 119 min after the 13:00 deadline.
- Warning: J11, J6 will finish after the deadline; this is reported, not hidden.
- Everything else stays as planned (2 jobs).
- Work already under way is kept as it is (J3, J5).

**09:50 - low energy (plan v12)**

- T1 reported low fuel (1 min left) at 09:50. It paused J9 with 0 of 35 min done (kept).
- J9 moved to T1; only the remaining 35 of 35 minutes are needed (0 sections already done). T1 was chosen because it can mow with a mower; T3 could only finish after the deadline; it is done 11 min before the 12:40 deadline.
- Warning: J11, J6 will finish after the deadline; this is reported, not hidden.
- Everything else stays as planned (4 jobs).
- Work already under way is kept as it is (J3).

## Decisions made under uncertainty

- 08:00 comm_loss: **wait_for_lease** - a check would cost more than it saves; wait for the safety lease (value of information -4.4; assumptions: {'lease_minutes': 15, 'verify_minutes': 8, 'verify_cost': 10.0, 'priority': 2}, hypotheses: {'still_working': 0.6, 'communication_failure_stopped': 0.1, 'mechanical_breakdown': 0.25, 'implement_failure': 0.05})
- 09:00 obstacle: **verify** - worth checking: planned trips would save about 24 min if the road is clear (value of information 2.0; assumptions: {'verify_cost': 10.0, 'verify_minutes': 8}, hypotheses: {'clear': 0.5, 'blocked': 0.5})

## Notes

- Idle while on duty = time a tractor waited (for an implement, a field or a road) before its own last task finished. Spare capacity = time after its own work was done while others still worked.
- Repeated or lost work = minutes inside a section that was interrupted before it finished, plus sections done twice after a communication loss. Finished sections are never reset.
- Deadline warnings are reported by the safety agent and shown, never hidden.
