# SYNTERRA coordinator: how it works, how it was tested, what it scores

Owner: Reya. **Runs on plain Python 3 with nothing to install.** `numpy` is used only if present (faster k-Vac; same results), NVIDIA cuOpt only if installed (Linux / WSL2), and `pytest` only for the tests.
No database: all state is in the one shared memory object (`fleet/memory.py`), as the design rules require.

## How to run

```
python run_demo.py            # writes out/ (team scenario) and out/stress/ (every event type)
python evaluate.py            # writes out/optimality.md and out/adversarial.md
python -m pytest -q tests     # 49 automated tests
```

## What each file does

| File | Role |
|---|---|
| `fleet/memory.py` | Shared memory: tractors, fields, implements, jobs split into sections, append-only event log with ids and sequence numbers |
| `fleet/navigation.py` | Farm road graph, shortest paths (Floyd–Warshall); blocked **and** uncertain roads are removed |
| `fleet/planner.py` | Fleet agent: SYNTERRA search + baselines (FCFS, nearest), fragility check, local search, exact solver |
| `fleet/kvac.py` | Work Function Algorithm (the k-Vac algorithm), used as one candidate schedule; numpy or pure Python |
| `fleet/assignment.py` | Hungarian algorithm (Kuhn–Munkres, O(n³)) for optimal tractor-to-job rounds |
| `fleet/cuopt_solver.py` | NVIDIA cuOpt adapter: maps the farm onto cuOpt's routing model; used automatically when cuOpt is installed |
| `fleet/safety.py` | Independent safety check of every plan (re-derives routes, fuel, battery, ownership) |
| `fleet/orchestrator.py` | Runs virtual time, handles every event, keeps finished work, re-plans, saves only safe plans |
| `fleet/belief.py` | Decisions under uncertainty (value of information): verify, or wait for the safety lease |
| `fleet/events.py` | Structured event bus: duplicate and stale messages dropped, safety messages first |
| `fleet/explainer.py` | Plain-language sentences and "why?" answers built only from solver numbers (no AI key) |
| `fleet/execution.py`, `fleet/timeline.py` | Where each tractor is at any minute (no teleporting), dashboard frames |
| `fleet/metrics.py` | Scoreboard computed only from what actually ran, plus an independent audit |
| `fleet/adversarial.py` | Seeded hard-scenario generator and invariant checker |
| `run_demo.py`, `evaluate.py` | Hand-in outputs and accuracy reports |

## The algorithm (what happens at every re-plan)

1. **Candidates.** SYNTERRA builds many complete fleet schedules:
   - **beam search** (width 120, scenario key `beam_width` to lower it): grows schedules one job at a time and keeps the best partial ones; states reached by different orders are merged;
   - **greedy**, **FCFS** and **nearest** schedules (so a new plan is never worse on paper than a baseline at the moment it is made);
   - **k-Vac / Work Function Algorithm** choice of tractor per job;
   - **Hungarian rounds**: each round gives every tractor at most one job so the whole round is optimal (Kuhn–Munkres);
   - **NVIDIA cuOpt** routing solution, when cuOpt is installed (time windows = deadlines, prizes = priority, tractor–job compatibility);
   - **plan repair**: keep every job on its current tractor where still feasible, insert the rest.
2. **Local search.** The best 3 candidates are polished with vehicle-routing moves: reassign, move, swap, exchange two jobs' tractors, relocate (new tractor and new position), insert an unplanned job. Only strict improvements are kept.
3. **Objective, in strict order:** (1) most priority finished on time, (2) most priority finished at all, (3) least priority-weighted lateness, (4) least cost: travel + idle waiting + service stops + plan changes (stability) + finishing priority work early + keeping 15 min deadline slack.
4. **Robustness (contingency check).** Among schedules tied on goals 1–3, pick the lowest `cost + 0.1 × fragility`. Fragility = average damage if any tractor fails before any of its tasks (jobs that become late or impossible, extra driving), recovered greedily by the others. The weight 0.1 was chosen on random farms 1–100 and confirmed on unseen farms 101–200.
5. **Redirect check.** After a disruption, a tractor that is only *driving* to a job (no work started) may change course, but only if that strictly improves goals 1–3. Started work is never disturbed.
6. **Safety agent.** Every plan is checked independently. A plan with a hard violation is never saved (fallback: greedy plan, then nothing new). Late jobs are reported, never hidden.
7. **Explain.** One sentence per change, built from the scored options.

Every trip is built with the real road route, implement swaps, refuel and recharge stops, field and single-lane-road availability (`build_task`), so assignment and routing are optimised together.

## Memory-preserving recovery

Jobs are split into 5-minute sections. When a tractor stops mid-job (breakdown, implement failure, low fuel, road closure, safety lease), finished sections stay done for good, the partly-done section is counted as lost work, and only the unfinished sections are re-planned. Implement ownership, field occupancy and single-lane roads are exclusive at all times. A cancelled implement hand-over also stops the tractor that was waiting for it. A tractor that cannot reach the yard on its fuel calls a fuel delivery (arrives after `recovery_minutes`).

**Communication loss:** the silent tractor keeps ownership of its work. By protocol it stops itself after `lease_minutes` (15) without contact; only then is its unconfirmed work reassigned. Work it did but could not report is counted as repeated work until it reconnects and reports it. A value-of-information rule decides whether a scout check is worth sending first. The probabilities are stated assumptions in `belief.py`, not measured rates.

## Your projects and papers: what is used, and how faithfully

| Source | What is in the code | Faithful? |
|---|---|---|
| NVIDIA route optimisation agent / cuOpt | **Real cuOpt adapter** (`cuopt_solver.py`) building cuOpt's routing model; its route is one candidate, rebuilt exactly and safety-checked | Adapter yes, tested against a stand-in. cuOpt itself is Linux-only, so it was **not run on this Windows machine**; it activates under Linux / WSL2 with `pip install --extra-index-url=https://pypi.nvidia.com cuopt-cu12` |
| Hungarian algorithm (your suggestion) | Kuhn–Munkres O(n³) (`assignment.py`), verified against brute force on hundreds of random matrices; used for optimal assignment rounds | Yes |
| k-Vac (OxHack) | **Real Work Function Algorithm** recursion (`kvac.py`), tested: its final value equals the brute-force offline optimum. Used as a candidate schedule; plus look-ahead and stability penalty | Algorithm yes; its competitive guarantee does **not** carry over (work time, fuel, deadlines added) |
| CLEAR (ISEF) | Structured events, ids, per-source sequence numbers, duplicate and stale dropping, safety-first ordering | Messaging ideas yes; no reinforcement learning |
| ScoutCane | Shared road map: blocked / uncertain / cleared, used by every tractor | Yes (no hyperdimensional computing) |
| Adaptive inspection UAV | Value-of-information choice: verify an uncertain road or silent tractor, or wait | Simplified, explicit assumptions |
| Failure-driven self-evolution | Seeded adversarial campaign; found 9 real bugs (listed in `out/adversarial.md`, plus one crash), all fixed and covered by tests | Test generation, not learning |
| RoadStar DispatchOS | Full job workflow states with history per job | Yes |
| Paper 1 (TRO 2025, heterogeneous long-endurance allocation) | Capabilities, fuel + battery, recharging trips, plan repair vs re-plan | Practical version |
| Paper 2 (TASE 2025, contingency-aware) | Fragility over every single-tractor failure scenario, weighted into the choice | Practical version |
| Paper 3 (JAIR 2026, prioritised planning) | Who gets a single-lane road first is decided by the search objective (urgency) | No learning |
| Paper 4 (AAAI 2021, RHCR) | Time reservations on single-lane roads, re-resolved at every re-plan | Simplified: no collision search on 1,000 agents |
| Paper 5 (ESWA 2026, joint task + path) | Real routes and stops priced inside every assignment | Yes (not bee-colony) |
| Paper 6 (AIJ 2026, timing uncertainty) | Delay events push back later work (including other tractors queued behind); 15 min slack kept | Practical version |

## Results (all measured from what actually ran)

**Team scenario (`scenario.json`, hand-in):**

| | SYNTERRA | FCFS | Nearest |
|---|---|---|---|
| Jobs on time | **10/10** | 7/10 | 7/10 |
| Travel (min) | **356** | 544 | 484 |
| Distance (km) | **89** | 136 | 121 |
| All work done after (min) | **443** | 558 | 540 |
| Implement changes | **5** | 9 | 8 |
| Rule violations in what ran | **0** | 0 | 0 |

**Stress scenario (`scenarios/stress.json`, every event type):** SYNTERRA 11/11 completed, 8 on time, 598 min travel; FCFS 5 on time (728 min); nearest 6 on time (654 min). Violations: 0/0/0.

**Optimality (`out/optimality.md`):** on 60 farms of 4–8 jobs, the true optimum was found by exact branch-and-bound. SYNTERRA's committed plan matches it on goals 1–3 (on-time and finished priority work, lateness) in **60/60**, and on all four goals including cost in 50/60 (average cost gap 1.121%; some cost is deliberately traded for robustness to a breakdown).

**Adversarial (`out/adversarial.md`, 500 random hard farms):** jobs on time SYNTERRA 2648 vs 1852 (FCFS) and 1570 (nearest); priority-weighted on-time work 5807 vs 3795 and 3228; travel 153056 vs 172369 and 178989 min; rule violations in what ran 0 / 0 / 0. SYNTERRA is not worse than FCFS in 478/500 farms; in the other 22 an unforeseeable breakdown or event hurt its plan more (reported, not hidden).

## Tests (49, all passing)

`tests/test_coordinator.py`: the 15 required cases (no sprayer → no spray job; not enough energy; no two owners; finished sections kept after breakdown; replacement gets only the rest; blocked road never routed; re-plan keeps finished jobs; duplicate events; repeated failure events; unreachable job reported; communication loss keeps ownership; deadlines and finish times consistent; reproducible; reset; benchmark numbers come from executed events), plus baseline late job reported, SYNTERRA beats baseline, stress scenario clean, grounded "why?", single-lane road never shared, k-Vac work function = offline optimum, local search never worse, redirect only before work starts, cancelled hand-over never double-holds, delay accounting exact, adversarial campaign clean.

`tests/test_algorithms.py`: Hungarian = brute force (400 random matrices), branch-and-bound = exhaustive search, SYNTERRA reaches the exact optimum on the farms it used to miss, pure-Python k-Vac = numpy k-Vac, shortest paths = Dijkstra (with closed roads), cuOpt adapter model and capability matching (stand-in solver), planner works without cuOpt, delay chain pushes everyone queued behind, and **one test per safety rule** proving each violation type is caught (capability, implement, remaining work only, double assignment, broken tractor, closed road, finished job, unexplained job, one tractor per field, battery, fuel, single implement holder).

## For Mridula: `out/timeline.json`

```
{ "format": "field-day timeline v2",
  "scenario": {name, start_clock, yard, fields, tractors, jobs, implements, narrow_roads},
  "step_minutes": 5,
  "frames": [ { "t", "clock", "plan_version",
                "tractors": { "T1": {x, y, activity, status, job, field, fuel, battery, km, implement, spraying} },
                "fields":   { "F1": {job, covered (0..1), spraying, activity, tractor} },
                "jobs":     { "J1": {status, remaining, priority, done_segments, total_segments} },
                "blocked": ["F4-F7"], "uncertain": [] } ],
  "baseline_frames": [ same shape, FCFS run ],
  "explanations": [ {t, clock, cause, plan_version, sentences: [...] } ],
  "decisions": [ value-of-information decisions ],
  "why": { "J1": "plain answer" },
  "job_history": { "J1": [ {t, status, note} ] },
  "events": [ event log entries ],
  "comparison": { "smart": {...}, "fcfs": {...}, "nearest": {...} } }
```
`x, y` are grid cells (yard at 0,0) and are interpolated along roads. `activity` is one of: driving, spraying, mowing, seeding, changing implement, refuelling, recharging, waiting for implement / field / single-lane road, delayed, idle, broken down, no signal.

## Three-minute demo script

1. **Starting plan (06:00):** show the explanation feed: every job on a capable tractor, before its deadline, 276 min of planned driving.
2. **Breakdown (07:35):** T2 breaks; the feed shows which sections were kept and who takes the rest.
3. **Recovery:** the replacement drives there, the field keeps shading from where it stopped, and nothing is done twice.
4. **Blocked road (08:30) and priority change (08:50):** routes change, the plan stays safe, and the feed explains why.
5. **Scoreboard:** 10/10 on time vs 7/10, 35% less driving, 0 violations. Then point to `out/optimality.md` (60/60 optimal on the goals that matter) and `out/adversarial.md` (500 hard farms, 0 violations for SYNTERRA) as the reason to trust the plan.

## Limitations (honest)

- NVIDIA cuOpt is Linux-only, so it was not run on this Windows laptop; the adapter is tested against a stand-in and switches on automatically under Linux / WSL2. Without it, SYNTERRA uses its own solvers (beam + Hungarian + k-Vac + local search).
- The k-Vac competitive guarantee does not apply to this extended problem; WFA is one candidate among several.
- The farm graph uses direct roads between every pair of places (Manhattan distance); a tractor stopped mid-road goes to the nearer end.
- Probabilities in the uncertainty model are assumptions, not data.
- 22/500 adversarial farms end with less on-time priority work than FCFS after an unforeseeable failure.
- Slowest re-plan is under 1 s at beam width 120 (lower `beam_width` in the scenario on a slow laptop).
- Tractor link and AgOpenWeb belong to Mridula's track; this coordinator runs on the built-in simulator.
