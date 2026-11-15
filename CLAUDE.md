# Field Day — Run the Fleet (Hack Arizona AgX, Challenge 2)

Hackathon project. Hand-in is due 5:00 PM today. Keep everything simple, working and demo-ready.
The team lead is non-technical: explain what you do in plain words, one step at a time.

## What we are building
A coordinator that assigns jobs to 3 autonomous tractors and re-plans when things go wrong.
Judges score the coordinator, not the simulator.

## Architecture (do not change without asking)
Diagram: `docs/architecture.png` (purple = our decision logic, teal = where the facts live, dashed = backup only).
```
Operator (jobs, events, "why?") -> Command center (dashboard and scoreboard) -> Orchestrator
Orchestrator runs the agents in order:  Fleet (assigns jobs) -> Navigation (costs each trip) -> Safety (blocks bad plans)
Then the Explainer puts the plan changes into plain language.
Orchestrator, agents and the baseline dispatcher all write to ONE shared memory (state + event log).
Shared memory <-> Tractor link (sends jobs, reads positions) -> AgOpenWeb (tractors and fields)
                                                             -> backup simulator, ONLY if AgOpenWeb fails
Baseline dispatcher (first come, first served) runs on the same farm for comparison.
```
Rules of the design:
1. One shared memory (`fleet/memory.py`). Agents are plain functions with no memory of their own.
2. Fixed order, event-driven. Agents never chat freely. Same input must give the same plan.
3. Math and rules do the planning. An AI model is used ONLY to word explanations and answer "why?".
   There must be a template fallback that works with no AI key.
4. Only a plan the safety agent accepts is saved.
5. Re-planning keeps healthy tractors' in-progress jobs and re-plans only unfinished work.
6. The tractor link is one interface with two implementations: AgOpenWeb and the backup simulator.
   The simulator is a fallback only; AgOpenWeb is the default.

## Tractor parameters
Every tractor carries three numbers, kept in shared memory and shown on the dashboard:
- **Fuel** (`fuel`, full = `tank_minutes`) — burns 1 per minute of driving or working. Refuel at the yard: `refuel_minutes`.
- **Battery** (`battery`, full = `battery_minutes`) — drains 1 per minute of working only (it powers the implement
  and sensors on the job). Recharge at the yard: `recharge_minutes`.
- **Distance** (`distance_km`) — the odometer: total km driven. `km_per_cell` in the scenario converts grid cells to km.
The planner sends a tractor to the yard before a job if fuel or battery would not cover it (one trip covers both).

## What the demo shows: spraying
All job types stay in the plan, but **spraying is the activity we show**. The tractor mirrored live in AgOpenWeb
runs a spray job, and the dashboard marks tractors as "spraying" and shades fields as they are sprayed.

## Already written (stdlib Python, no installs)
- `scenario.json` — 3 tractors (each with fuel tank and battery size), 8 fields, 4 implements, 10 jobs, 3 events (breakdown, blocked route, priority change). Times are minutes after 06:00.
- `fleet/memory.py` — shared memory and event log.
- `fleet/navigation.py` — travel-time table, rebuilt when a route is blocked.
- `fleet/planner.py` — `make_plan(mem, "smart")` is our fleet agent; `make_plan(mem, "fcfs")` is the baseline.
  Quick test of the initial plan: smart = 276 min travel (69 km), 0 late; baseline = 508 min travel (127 km), 1 late job.

Task fields from the planner: tractor, job, field, type, depart, legs, travel, distance_km, swap, refuel, recharge,
unit, prev_unit, yard_at, take_at, start, end, work, fuel_after, battery_after, odometer_km, deadline.

## Still to build, in this order
1. `fleet/safety.py` — `validate(mem, tasks, unassigned) -> list of violation strings`. Rules:
   - tractor can do the job type; implement type matches and is compatible with the tractor
   - no implement held by two tractors at once
   - fuel covers trip there + job + trip back to the yard
   - battery covers the job's working minutes
   - job ends before its deadline
   - no job given twice; no tractor with overlapping tasks; no two tractors in one field at once
   - broken tractor gets nothing; finished work is never reassigned
   - a half-done job is reassigned only for the remaining minutes
   - no leg uses a blocked route
   - every pending job is assigned or listed in `unassigned` with a reason
2. `fleet/orchestrator.py` — `advance(mem, t)` (finish/commit tasks up to time t), apply events
   (breakdown: keep % done, release the implement after `recovery_minutes`; blocked_route: add to
   `mem.blocked` and rebuild navigation; priority_change), then re-plan -> validate -> save -> explain.
   When a task is committed, update the tractor's `fuel`, `battery` and `distance_km` in shared memory.
   Log every step with `mem.record(...)`.
3. `fleet/explainer.py` — compare old and new plan; one plain sentence per change, e.g.
   "Tractor 2 broke down at 07:35. J3 moved to Tractor 3; only the remaining 25 of 60 minutes are needed."
   Also `why(job_id)` using `mem.why` (score parts and the next-best tractor).
4. `run_demo.py` — runs smart and baseline through the same events; writes
   `out/event_log.jsonl`, `out/comparison.md` (jobs completed, on-time jobs, travel minutes, distance in km,
   idle minutes, refuel and recharge stops, rule violations) and `out/timeline.json` for the dashboard
   (include each tractor's fuel, battery and distance over time, and which fields are being sprayed).
5. `fleet/tractor_link.py` — interface (send a job, read position) + `AgOpenWebLink` + `SimLink` (fallback only).
   The live AgOpenWeb tractor runs a spray job.
6. Dashboard (command center): map of fields and tractors, job list, plan-change explanations,
   "why?" per job, scoreboard vs baseline. Each tractor shows fuel, battery and distance driven.
   Spraying is shown live: a "spraying" label on the tractor and the field shading in as it is covered.
   Very friendly, clean, no jargon.
7. `README.md` — how to run; state clearly which parts used AgOpenWeb and which used our simulator.

## AgOpenWeb (https://github.com/AgOpenGPS-Official/AgOpenWeb)
Run: install .NET 10 SDK, `dotnet run --project Platforms/AgOpenWeb.Desktop`, open http://localhost:5174.
Known from its CLAUDE.md (verify in the code before relying on it):
- One vehicle per running instance; several browsers can connect but only one holds control.
- Fields are saved as `field.geojson` (origin, boundaries, headland, tracks).
- Vehicle/tool settings live in a config store (e.g. tool width).
- Host -> client: binary WebSocket frames (`Tick` at 10 Hz with pose). Client -> host: text commands `id|arg`.
- Modules talk over UDP; `Simulators/AgOpenWeb.VirtualModules` has virtual GPS/steer/machine modules.
NOT yet confirmed: how an outside program gives it a job or moves the tractor. Find this first.
Plan: (a) read fields and tool width from AgOpenWeb files into `scenario.json`; (b) mirror one tractor
live in AgOpenWeb; (c) more instances only if time allows.

## Judging (20 points)
Constraint compliance 4 · Completed work vs baseline 4 · Adaptation to events 3 ·
Operational efficiency 4 · Explanations and demo 5. Tiebreaker: "Would a farm operator trust this plan?"

## Hand-in checklist (5:00 PM)
- [ ] Link to code
- [ ] README
- [ ] Scenario file
- [ ] Event log from the demo run
- [ ] Comparison against the baseline: jobs completed, travel time, idle time
