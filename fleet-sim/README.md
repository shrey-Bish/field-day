# Run the Fleet: three-tractor coordinator

> This folder is a self-contained live simulation (AgOpenWeb virtual tractors, Python
> coordinator, Leaflet dashboard), separate from the planner in the repo's top-level `fleet/`
> package. Run every command below from inside `fleet-sim/`.

Hack Arizona AgX challenge "Run the Fleet" (American Tractor Company): a coordinator that assigns
field work to three simulated autonomous tractors and re-plans when something goes wrong.

**Simulator:** our own, built on the **AgOpenGPS / AgOpenWeb simulator backend**. Each tractor is an
AgOpenWeb `VirtualModuleHub` that sends real AgOpenGPS `$PANDA` GPS sentences over UDP, the same
format the tractor hardware sends. The coordinator only knows where a tractor is from that GPS.

```
Leaflet dashboard  <--JSON/HTTP-->  coordinator (Python)  --drive commands-->  fleet bridge (C#)
  :8080                               |      ^                     UDP 10100     3 x VirtualModuleHub
                                      |      | telemetry JSON                         |
                          commands    v      | (GPS, fuel, battery)                   | $PANDA GPS, 10 Hz
                       UDP 10301-3  tractor agents (one per tractor)  <---------------+ UDP 10001-10003
```

## Run it (Windows)

1. Install the .NET 10 SDK (`winget install Microsoft.DotNet.SDK.10`) and Python 3.9+.
2. Run `.\setup-agopenweb.ps1` once. It clones AgOpenWeb at the commit we used and applies
   `patches/agopenweb-fleet.patch`.
3. Double-click **Start Fleet Simulation.bat**. It builds the bridge the first time, starts the
   bridge and the coordinator, and opens http://localhost:8080.
4. Double-click **Stop Fleet Simulation.bat** to stop.

From a terminal: `.\run-fleet-sim.ps1` (`-Rebuild`, `-Stop`, `-NoBrowser`).

## Using the dashboard

1. **Field:** search a place (or type `lat, lon`), then draw a rectangle or polygon, import a
   Google Earth KML, or click *Demo square*.
2. **Setup:** implement width, simulation speed, and per tractor its starting fuel %, starting
   battery % and working speed.
3. **Start.** The field is cut into passes along its longest edge and split into three
   side-by-side blocks (faster tractors get more). Each tractor starts at the edge of its block
   and works across it, with curved U-turns between passes.
4. **Events:** *Break down* / *Repair* on any tractor; tractors that run out of fuel or battery
   are taken out of service automatically. Unfinished work is re-split among the others, finished
   work is kept, and the event log explains the new plan in plain language.
5. **Agent status:** expand a tractor card to see its agent's live report (GPS position and
   quality, heading, speed, fuel, battery, distance, task, time since last report).

The event log of every run is appended to `fleet/logs/event-log.txt`.

## Files

| Path | What it is |
|---|---|
| `fleet/coordinator.py` | Central coordinator: planning, assignment, re-planning, monitoring, HTTP API, serves the dashboard |
| `fleet/tractor_agent.py` | On-board agent per tractor: reads its GPS, tracks fuel and battery, reports telemetry |
| `fleet/dashboard.html` | Leaflet dashboard (the "Command Center") |
| `fleet/bridge/` | C# app hosting three AgOpenWeb `VirtualModuleHub`s; drive commands `place`, `goto`, `path`, `stop`, `rate` |
| `run-fleet-sim.ps1`, `*.bat` | Start / stop |
| `setup-agopenweb.ps1`, `patches/` | Fetch AgOpenWeb and apply our changes |
| `fleet-dashboard.html` | Earlier stand-alone browser-only prototype (no backend) |
| `start-fleet.ps1`, `fleet-view.html`, `install-field.ps1`, `fleet-field.js` | Earlier experiment: three full AgOpenWeb instances side by side (ports 5174-5176) |

## Not done yet

- Per-tractor capabilities / implement compatibility, job types, priorities and deadlines.
- Planning ahead on fuel and battery (today the coordinator reacts when a tractor runs low).
- Blocked routes and changed priorities.
- Comparison against a first-come-first-served baseline (jobs completed, travel, idle time).
- Collision avoidance in the turning area.
