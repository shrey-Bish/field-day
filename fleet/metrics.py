"""Benchmark numbers, computed ONLY from what actually ran (mem.history and the event log),
plus an independent audit of the executed run (not of the plans)."""
from .navigation import edge_minutes, INF
from .memory import COMPLETED

EPS = 1e-6


def _phase_minutes(h, kinds):
    tot = 0.0
    for ph in h["phases"]:
        if ph["kind"] in kinds and ph["t0"] < h["actual_end"]:
            tot += min(ph["t1"], h["actual_end"]) - ph["t0"]
    return tot


def _completed_service(h, kind):
    return sum(1 for ph in h["phases"] if ph["kind"] == kind and ph["t1"] <= h["actual_end"] + EPS)


def compute(mem):
    jobs = list(mem.jobs.values())
    done = [j for j in jobs if j["status"] == COMPLETED]
    on_time = [j for j in done if j["completed_at"] <= j["deadline"] + EPS]
    travel = sum(h["travel_done"] for h in mem.history)
    work = sum(h["work_done"] for h in mem.history)
    end = max([h["actual_end"] for h in mem.history] + [0])
    busy, idle, spare = {}, 0.0, 0.0
    for tid in mem.tractors:
        hs = [h for h in mem.history if h["tractor"] == tid]
        b = sum(h["travel_done"] + h["work_done"] + _phase_minutes(h, {"swap", "refuel", "recharge"}) for h in hs)
        stop = end
        for e in mem.log:
            if e["event"] == "TRACTOR_STOP_CONFIRMED" and e.get("tractor") == tid and mem.tractors[tid]["status"] != "ok":
                stop = min(stop, e["t"])
        own_end = min(stop, max([h["actual_end"] for h in hs] + [0]))
        busy[tid] = b
        idle += max(0.0, own_end - b)          # waiting while it still had work to do
        spare += max(0.0, stop - max(own_end, b))   # done early, free for more work
    recov = []
    for r in mem.recoveries:
        nxt = [h for h in mem.history if h["job"] == r["job"] and h["depart"] >= r["t"] - EPS]
        if nxt:
            recov.append(min(h["start"] for h in nxt) - r["t"])
    return {
        "jobs_total": len(jobs),
        "jobs_completed": len(done),
        "jobs_on_time": len(on_time),
        "jobs_late": len(done) - len(on_time),
        "jobs_unfinished": len(jobs) - len(done),
        "priority_weighted_completed": sum(j["priority"] for j in done),
        "priority_weighted_on_time": sum(j["priority"] for j in on_time),
        "travel_minutes": round(travel, 1),
        "distance_km": round(travel / mem.cell_minutes * mem.km_per_cell, 1),
        "work_minutes": round(work, 1),
        "idle_minutes": round(idle, 1),
        "spare_capacity_minutes": round(spare, 1),
        "fuel_used_minutes": round(travel + work, 1),
        "refuel_stops": sum(_completed_service(h, "refuel") for h in mem.history),
        "recharge_stops": sum(_completed_service(h, "recharge") for h in mem.history),
        "implement_swaps": sum(_completed_service(h, "swap") for h in mem.history),
        "makespan_minutes": round(end, 1),
        "repeated_or_lost_work_minutes": round(mem.lost_work + mem.repeated_work, 1),
        "recovery_minutes_avg": round(sum(recov) / len(recov), 1) if recov else None,
        "recoveries": len(recov),
        "replans": mem.replans,
        "plans_rejected": sum(e["event"] == "PLAN_REJECTED" for e in mem.log),
        "deadline_warnings_in_plans": sum(len(e.get("deadline_warnings") or []) for e in mem.log
                                          if e["event"] == "PLAN_APPROVED"),
        "planning_ms_total": round(sum(mem.plan_ms), 1),
        "planning_ms_max": round(max(mem.plan_ms), 1) if mem.plan_ms else 0,
        "audit_violations": len(audit(mem)),
    }


def audit(mem):
    """Check the run that actually happened (independent of the safety agent)."""
    out = []
    # 1. capability / implement compatibility of everything that ran
    for h in mem.history:
        tr = mem.tractors[h["tractor"]]
        job = mem.jobs[h["job"]]
        if job["type"] not in tr["capabilities"]:
            out.append(f"{h['tractor']} did {job['type']} work without the capability")
        if h["work_done"] > EPS and mem.implements[h["unit"]]["type"] != job["implement"]:
            out.append(f"{h['tractor']} worked {h['job']} with the wrong implement")
        if h.get("fuel_end", 0) < -EPS:
            out.append(f"{h['tractor']} ran out of fuel during {h['job']}")
    # 2. no two tractors working one field at the same time
    spans = []
    for h in mem.history:
        for ph in h["phases"]:
            if ph["kind"] == "work" and ph["t0"] < h["actual_end"]:
                spans.append((ph["t0"], min(ph["t1"], h["actual_end"]), h["field"], h["tractor"]))
    spans.sort()
    for i, a in enumerate(spans):
        for b in spans[i + 1:]:
            if b[0] >= a[1]:
                break
            if a[2] == b[2] and a[3] != b[3] and b[0] < a[1] - EPS:
                out.append(f"{a[3]} and {b[3]} worked {a[2]} at the same time ({a[0]}-{a[1]} / {b[0]}-{b[1]})")
    # 3. no road driven after it was closed (confirmed or uncertain)
    for h in mem.history:
        for ph in h["phases"]:
            if ph["kind"] != "drive":
                continue
            clock = ph["t0"]
            p = ph["path"]
            for k in range(len(p) - 1):
                d = edge_minutes(mem, p[k], p[k + 1])
                e = frozenset((p[k], p[k + 1]))
                if clock >= h["actual_end"]:
                    break
                closed = any(a - EPS <= clock < (INF if b is None else b) for a, b in mem.block_times.get(e, []))
                if closed:
                    out.append(f"{h['tractor']} entered closed road {'-'.join(sorted(e))} at {clock}")
                clock += d
    # 4. completed jobs have every section done exactly once; no section has two owners now
    for j in mem.jobs.values():
        if j["status"] == COMPLETED and any(s["status"] != "done" for s in j["segments"]):
            out.append(f"{j['id']} marked complete with unfinished sections")
        for s in j["segments"]:
            if s["status"] == "done" and not s["done_by"]:
                out.append(f"section {s['id']} done without a recorded owner")
    # 5. work done never exceeds what the job needed, apart from honestly counted repeats
    for j in mem.jobs.values():
        did = sum(h["work_done"] for h in mem.history if h["job"] == j["id"])
        if did > j["work_minutes"] + mem.lost_work + mem.repeated_work + EPS:
            out.append(f"{j['id']}: {did:.0f} min of work recorded for a {j['work_minutes']} min job")
    return out
