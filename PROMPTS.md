# Prompts for Claude Code — paste one at a time, in order

Wait for each one to finish and work before pasting the next.

## 1. Get oriented
```
Read CLAUDE.md, scenario.json and the three files in fleet/. Then run a quick check that
make_plan works for both "smart" and "fcfs" on scenario.json and tell me in plain words what you see.
Do not change anything yet.
```

## 2. Safety agent
```
Build fleet/safety.py exactly as described in CLAUDE.md step 1. Then test it: the smart plan must
return zero violations, and the baseline plan should report its late job. Show me the results.
```

## 3. Orchestrator and events
```
Build fleet/orchestrator.py as described in CLAUDE.md step 2. It must handle the three events in
scenario.json (breakdown, blocked route, priority change). After the breakdown, check that no work is
lost or duplicated: the half-done job is reassigned only for its remaining minutes.
Write a small test that proves this and run it.
```

## 4. Explainer
```
Build fleet/explainer.py as described in CLAUDE.md step 3, with template sentences that need no AI key.
Show me the explanation it gives for each of the three events, and the "why?" answer for one job.
```

## 5. Demo run and hand-in files
```
Build run_demo.py as described in CLAUDE.md step 4. Run it and show me out/comparison.md.
If our coordinator does not beat the baseline on jobs completed, travel time and idle time with zero
violations, tune the scoring in fleet/planner.py (not the scenario) until it does, and tell me what you changed.
```

## 6. AgOpenWeb adapter (start this in a second terminal as early as possible)
```
Clone https://github.com/AgOpenGPS-Official/AgOpenWeb next to this project and get it running at
http://localhost:5174. Then read its code and Docs to find out how an outside program can
(a) read fields and tool settings and (b) move or command the tractor. Report what you find before
writing code. Then build fleet/tractor_link.py with one interface and two versions: AgOpenWebLink
and SimLink (backup). Start with one tractor shown live in AgOpenWeb.
```

## 7. Dashboard
```
Build the command center as one web page that reads out/timeline.json: a map with fields and
tractors, a job list with status, the plain-language explanation after each event, a "why?" button
per job, and a scoreboard against the baseline. Very friendly, clean, self-explanatory, no jargon.
```

## 8. README and final check
```
Write README.md: what this is, how to run it, and exactly which parts used AgOpenWeb and which used
our own simulator. Then go through the hand-in checklist in CLAUDE.md and tell me what is missing.
```

## If time is short
Skip nothing in 2–5 (they carry most of the points). Cut the dashboard down to the map and the
explanations. Keep the AgOpenWeb adapter to one live tractor.
