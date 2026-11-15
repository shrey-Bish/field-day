"""Safety agent: deterministic, independent check of every proposed plan.

validate(mem, tasks, unassigned) -> list of violation strings.
Each string starts with a category tag:
  "[hard] ..."      a broken rule; the orchestrator never saves a plan with one of these
  "[deadline] ..."  the job would finish after its deadline (reported honestly, not hidden)

It re-derives travel times, fuel and battery from the navigation table and the tractors'
committed state; it does not trust the numbers the planner wrote into the tasks.
"""
from .navigation import travel, edges_of, avoid, YARD, INF
from .memory import PLANNABLE


def is_hard(v):
    return v.startswith("[hard]")


def split(violations):
    return [v for v in violations if is_hard(v)], [v for v in violations if not is_hard(v)]


def validate(mem, tasks, unassigned):
    out = []
    hard = lambda msg: out.append("[hard] " + msg)
    closed = avoid(mem)

    # ---- per task: capability, implement, ownership of work ----
    seen_jobs, seen_segs = {}, {}
    for a in mem.active.values():
        for sid in a.get("segments", []):
            seen_segs[sid] = f"{a['tractor']} (in progress)"
    for t in tasks:
        tr = mem.tractors.get(t["tractor"])
        job = mem.jobs.get(t["job"])
        if tr is None or job is None:
            hard(f"{t['job']} -> {t['tractor']}: unknown tractor or job")
            continue
        if tr["status"] != "ok":
            hard(f"{t['job']} given to {t['tractor']}, which is {tr['status']}")
        if job["type"] not in tr["capabilities"]:
            hard(f"{t['tractor']} cannot do {job['type']} ({t['job']})")
        unit = mem.implements.get(t["unit"])
        if unit is None or unit["type"] != job["implement"]:
            hard(f"{t['job']} needs a {job['implement']} but {t['tractor']} would carry {t['unit']}")
        elif unit["type"] not in tr["compatible_implements"]:
            hard(f"{t['unit']} ({unit['type']}) does not fit {t['tractor']}")
        elif unit.get("out_of_service"):
            hard(f"{t['unit']} is out of service but planned for {t['job']}")
        if job["status"] == "COMPLETED" or job["remaining"] <= 0:
            hard(f"{t['job']} is already finished but was assigned again")
        elif job["status"] not in PLANNABLE:
            hard(f"{t['job']} is {job['status']} and must not be re-planned")
        if abs(t["work"] - job["remaining"]) > 1e-6:
            hard(f"{t['job']} planned for {t['work']} min but only {job['remaining']} min remain")
        pend = [s["id"] for s in job["segments"] if s["status"] != "done"]
        if sorted(t.get("segments", pend)) != sorted(pend):
            hard(f"{t['job']}: task does not cover exactly the unfinished sections")
        if t["job"] in seen_jobs:
            hard(f"{t['job']} given twice ({seen_jobs[t['job']]} and {t['tractor']})")
        seen_jobs[t["job"]] = t["tractor"]
        for sid in t.get("segments", []):
            if sid in seen_segs:
                hard(f"section {sid} would have two owners: {seen_segs[sid]} and {t['tractor']}")
            seen_segs[sid] = t["tractor"]
        if t["end"] > t["deadline"]:
            out.append(f"[deadline] {t['job']} would finish at {mem.clock(t['end'])}, "
                       f"after its {mem.clock(t['deadline'])} deadline")

    # ---- per tractor: route, time order, fuel, battery (re-derived) ----
    by_tr = {}
    for t in tasks:
        by_tr.setdefault(t["tractor"], []).append(t)
    for tid, seq in by_tr.items():
        tr = mem.tractors.get(tid)
        if tr is None:
            continue
        seq.sort(key=lambda t: t["depart"])
        pos, clock = tr["pos"], max(tr["available_at"], mem.now)
        fuel, battery = tr["fuel"], tr["battery"]
        for t in seq:
            if t["depart"] < clock - 1e-6:
                hard(f"{tid} overlaps itself: {t['job']} departs {mem.clock(t['depart'])} "
                     f"before it is free at {mem.clock(clock)}")
            here = pos
            for leg in t["legs"]:
                if not leg or leg[0] != here:
                    hard(f"{t['job']}: route for {tid} does not start where the tractor is ({here})")
                    break
                for e in edges_of(leg):
                    if e in closed:
                        hard(f"{t['job']}: route for {tid} uses closed road {'-'.join(sorted(e))}")
                here = leg[-1]
            if here != t["field"]:
                hard(f"{t['job']}: route for {tid} does not end at field {t['field']}")
            # energy, phase by phase
            for ph in t.get("phases", []):
                dur = ph["t1"] - ph["t0"]
                if ph["kind"] == "drive":
                    expect = sum(travel(mem, ph["path"][i], ph["path"][i + 1]) for i in range(len(ph["path"]) - 1))
                    if expect == INF or abs(expect - dur) > 1e-6:
                        hard(f"{t['job']}: drive {'-'.join(ph['path'])} timed {dur} min, map says {expect}")
                    fuel -= dur
                elif ph["kind"] == "refuel":
                    fuel = tr["tank_minutes"]
                elif ph["kind"] == "recharge":
                    battery = tr["battery_minutes"]
                elif ph["kind"] == "work":
                    if fuel < dur + travel(mem, t["field"], YARD):
                        hard(f"{tid} lacks fuel for {t['job']}: {fuel:.0f} min left, needs "
                             f"{dur:.0f} work + {travel(mem, t['field'], YARD):.0f} back to the yard")
                    if battery < dur - 1e-6:
                        hard(f"{tid} lacks battery for {t['job']}: {battery:.0f} min left, needs {dur:.0f}")
                    fuel -= dur
                    battery -= dur
                if fuel < -1e-6:
                    hard(f"{tid} would run out of fuel during {t['job']}")
            pos, clock = t["field"], t["end"]

    # ---- implements: never two holders at once ----
    holds = {}
    for tid, seq in by_tr.items():
        cur = mem.tractors[tid]["implement"]
        since = mem.now
        for t in sorted(seq, key=lambda t: t["depart"]):
            if t["swap"]:
                if cur:
                    holds.setdefault(cur, []).append((since, t["yard_at"], tid))
                cur, since = t["unit"], t["take_at"]
                u = mem.implements.get(cur)
                if u and t["take_at"] < u["free_at"] - 1e-6:
                    hard(f"{tid} takes {cur} at {mem.clock(t['take_at'])} before it is free "
                         f"({mem.clock(u['free_at'])})")
        if cur:
            holds.setdefault(cur, []).append((since, INF, tid))
    for tid, tr in mem.tractors.items():
        if tid not in by_tr and tr["implement"]:
            holds.setdefault(tr["implement"], []).append((mem.now, INF, tid))
    for uid, spans in holds.items():
        spans.sort()
        for (a0, a1, ta), (b0, b1, tb) in zip(spans, spans[1:]):
            if ta != tb and b0 < a1 - 1e-6:
                hard(f"{uid} held by {ta} and {tb} at the same time")

    # ---- fields: never two tractors working one field at once ----
    spans = [(a["start"], a["end"], a["field"], a["tractor"]) for a in mem.active.values()]
    spans += [(t["start"], t["end"], t["field"], t["tractor"]) for t in tasks]
    by_field = {}
    for s in spans:
        by_field.setdefault(s[2], []).append(s)
    for fid, ss in by_field.items():
        ss.sort()
        for a, b in zip(ss, ss[1:]):
            if b[0] < a[1] - 1e-6 and a[3] != b[3]:
                hard(f"{a[3]} and {b[3]} would both work in {fid} around {mem.clock(b[0])}")

    # ---- single-lane roads: never two tractors on one at the same time ----
    road = {}
    for owner in list(mem.active.values()) + list(tasks):
        for key, r0, r1, tid in owner.get("reservations", ()):
            road.setdefault(key, []).append((r0, r1, tid))
    for key, ss in road.items():
        ss.sort()
        for a, b in zip(ss, ss[1:]):
            if b[0] < a[1] - 1e-6 and a[2] != b[2]:
                hard(f"{a[2]} and {b[2]} would both be on single-lane road {key[1]}-{key[2]} "
                     f"around {mem.clock(b[0])}")

    # ---- every plannable job is planned or explained ----
    busy = {a["job"] for a in mem.active.values()}
    for jid, job in mem.jobs.items():
        if job["status"] in PLANNABLE and jid not in busy and job["remaining"] > 0:
            if jid not in seen_jobs and not unassigned.get(jid):
                hard(f"{jid} is neither planned nor explained")
    return out
