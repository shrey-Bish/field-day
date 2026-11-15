"""Fleet agent: decides which tractor does which job, in what order.

policy="smart"   our coordinator: bounded beam search over whole-fleet schedules (look-ahead),
                 lexicographic objective, plan-stability penalty, fragility tie-break.
policy="greedy"  the original one-step greedy coordinator (kept for comparison / as beam seed).
policy="fcfs"    baseline A: jobs in submission order -> first compatible tractor that is free first.
policy="nearest" baseline B: jobs in submission order -> compatible tractor closest to the field.

All policies use the same build_task(), so the same hard rules (capability, implement,
fuel, battery, reachable roads) apply to every one of them. Baselines do not look at deadlines.
"""
import copy
from .navigation import travel, path, edge_minutes, YARD, INF
from .memory import PLANNABLE

BEAM_WIDTH = 30
STABILITY_PENALTY = 15      # minutes-equivalent cost of moving an already-planned job to another tractor
FRAGILITY_CANDIDATES = 6     # how many top schedules get the "what if a tractor fails" check
FRAGILITY_TOLERANCE = 0.05   # schedules within 5% of the best cost count as "similar"
SLACK_BUFFER = 15            # minutes of deadline slack we like to keep, to absorb delays
SLACK_WEIGHT = 0.3


def _views(mem):
    return {tid: dict(id=tid, pos=t["pos"], t=max(t["available_at"], mem.now), fuel=t["fuel"],
                      battery=t["battery"], km=t["distance_km"],
                      unit=t["implement"], caps=t["capabilities"],
                      compat=t["compatible_implements"], tank=t["tank_minutes"],
                      battery_full=t["battery_minutes"])
            for tid, t in sorted(mem.tractors.items()) if t["status"] == "ok"}


def _km(mem, minutes):
    return round(minutes / mem.cell_minutes * mem.km_per_cell, 1)


def build_task(mem, tv, job, units, field_free):
    """Work out everything tractor `tv` must do to complete `job`. None = cannot."""
    need = job["implement"]
    if job["type"] not in tv["caps"] or need not in tv["compat"]:
        return None
    narrow = narrow_roads(mem)
    st = dict(t=tv["t"], pos=tv["pos"], fuel=tv["fuel"], battery=tv["battery"], trav=0)
    legs, phases = [], []

    reservations = []

    def go(dest):
        d = travel(mem, st["pos"], dest)
        if d == INF or st["fuel"] < d:
            return False          # unreachable, or the tank cannot cover the drive
        if d > 0:
            p = path(mem, st["pos"], dest)
            legs.append(p)
            seg = [p[0]]
            seg_t0 = st["t"]
            for a, b in zip(p, p[1:]):
                e_min = edge_minutes(mem, a, b)
                key = road_key(a, b)
                if key in narrow:
                    # single-lane road: wait at the junction until nobody else is on it
                    enter = _free_slot(field_free.get(key, ()), st["t"], e_min, tv["id"])
                    if enter > st["t"]:
                        if len(seg) > 1:
                            phases.append(dict(kind="drive", t0=seg_t0, t1=st["t"], path=seg))
                        phases.append(dict(kind="wait_road", t0=st["t"], t1=enter, at=a, road="-".join(sorted(key))))
                        st["t"] = enter
                        seg, seg_t0 = [a], enter
                    reservations.append((key, st["t"], st["t"] + e_min))
                seg.append(b)
                st["t"] += e_min
            phases.append(dict(kind="drive", t0=seg_t0, t1=st["t"], path=seg))
        st["fuel"] -= d; st["trav"] += d; st["pos"] = dest
        return True

    def hold(kind, until):
        if until > st["t"]:
            phases.append(dict(kind=kind, t0=st["t"], t1=until, at=st["pos"]))
            st["t"] = until

    unit, prev, swap, yard_at, take_at = tv["unit"], tv["unit"], False, None, None
    if unit is not None and units[unit].get("out_of_service"):
        unit = None
    if unit is None or units[unit]["type"] != need:
        free = [u for u in units.values() if u["type"] == need and u["holder"] is None
                and not u.get("out_of_service")]
        if not free:
            return None
        u = min(free, key=lambda u: (u["free_at"], u["id"]))
        if not go(YARD):
            return None
        yard_at = st["t"]
        take_at = max(st["t"], u["free_at"])
        hold("wait_implement", take_at)
        hold("swap", take_at + mem.swap_minutes)
        unit, swap = u["id"], True

    field, work = job["field"], job["remaining"]
    back = travel(mem, field, YARD)
    if back == INF or travel(mem, st["pos"], field) == INF:
        return None
    # Fuel burns while driving and working; the battery drains only while working.
    # If either is too low for this job, stop at the yard first (one trip covers both).
    refuel = st["fuel"] < travel(mem, st["pos"], field) + work + back
    recharge = st["battery"] < work
    if refuel or recharge:
        if not go(YARD):
            return None
        if refuel:
            hold("refuel", st["t"] + mem.refuel_minutes)
            st["fuel"] = tv["tank"]
        if recharge:
            hold("recharge", st["t"] + mem.recharge_minutes)
            st["battery"] = tv["battery_full"]
        if st["fuel"] < travel(mem, st["pos"], field) + work + back or st["battery"] < work:
            return None
    if not go(field):
        return None
    start = max(st["t"], field_free.get(field, 0))
    hold("wait_field", start)
    phases.append(dict(kind="work", t0=start, t1=start + work, at=field))
    segs = [s["id"] for s in job.get("segments", []) if s["status"] != "done"]
    return dict(tractor=tv["id"], job=job["id"], field=field, type=job["type"], depart=tv["t"],
                legs=legs, phases=phases, travel=st["trav"], distance_km=_km(mem, st["trav"]),
                swap=swap, refuel=refuel, recharge=recharge, unit=unit, prev_unit=prev,
                yard_at=yard_at, take_at=take_at, start=start, end=start + work, work=work,
                segments=segs, total_work=job["work_minutes"],
                fuel_after=st["fuel"] - work, battery_after=st["battery"] - work,
                odometer_km=round(tv["km"] + _km(mem, st["trav"]), 1), deadline=job["deadline"],
                priority=job["priority"], late=start + work > job["deadline"],
                reservations=[(k, a, b, tv["id"]) for k, a, b in reservations])


def _apply(task, tv, units, field_free):
    if task["swap"]:
        if task["prev_unit"] and task["prev_unit"] in units:
            units[task["prev_unit"]].update(holder=None, free_at=max(units[task["prev_unit"]]["free_at"],
                                                                     task["yard_at"]))
        units[task["unit"]]["holder"] = tv["id"]
    tv.update(t=task["end"], pos=task["field"], fuel=task["fuel_after"],
              battery=task["battery_after"], km=task["odometer_km"], unit=task["unit"])
    field_free[task["field"]] = task["end"]
    for key, a, b, tid in task.get("reservations", ()):
        field_free[key] = tuple(field_free.get(key, ())) + ((a, b, tid),)


def road_key(a, b):
    return ("road",) + tuple(sorted((a, b)))


def narrow_roads(mem):
    return {road_key(a, b) for a, b in mem.scenario.get("narrow_roads", [])}


def _free_slot(spans, t, dur, me):
    """Earliest time >= t at which a single-lane road is free for `dur` minutes (RHCR-style
    time reservation table; whoever was planned first holds the slot)."""
    for a, b, who in sorted(spans):
        if who == me:
            continue
        if t < b and a < t + dur:
            t = b
    return t


def initial_free(mem):
    """Fields and single-lane roads already taken by work that is under way."""
    ff = {a["field"]: a["end"] for a in mem.active.values()}
    for a in mem.active.values():
        for key, r0, r1, tid in a.get("reservations", ()):
            if r1 > mem.now:
                ff[key] = tuple(ff.get(key, ())) + ((r0, r1, tid),)
    return ff


def _score(mem, task, job):
    """Lower is better. Every part is kept so the 'why?' button can show it."""
    parts = dict(travel=task["travel"],
                 swap=mem.swap_minutes if task["swap"] else 0,
                 refuel=mem.refuel_minutes if task["refuel"] else 0,
                 recharge=mem.recharge_minutes if task["recharge"] else 0,
                 wait=0.3 * (task["start"] - mem.now),
                 priority=-20 * job["priority"],
                 slack=0.1 * (job["deadline"] - task["end"]))
    return sum(parts.values()), parts


def _reason(mem, tvs, job, units, field_free):
    able = [tv for tv in tvs.values() if job["type"] in tv["caps"] and job["implement"] in tv["compat"]]
    if not able:
        return f"no working tractor can do a {job['type']} job"
    if travel(mem, YARD, job["field"]) == INF:
        return f"field {job['field']} cannot be reached: every road to it is blocked"
    if not any(u["type"] == job["implement"] and not u.get("out_of_service") for u in units.values()):
        return f"no working {job['implement']} is left on the farm"
    built = [build_task(mem, tv, job, units, field_free) for tv in able]
    if not any(built):
        return f"no free {job['implement']}, or not enough fuel or battery"
    return f"no tractor can finish it before the {mem.clock(job['deadline'])} deadline"


def plannable_jobs(mem):
    busy = {a["job"] for a in mem.active.values()}
    return sorted((j for j in mem.jobs.values() if j["status"] in PLANNABLE and j["id"] not in busy
                   and j["remaining"] > 0), key=lambda j: j["order"])


# ---------------- baselines ----------------
def _plan_in_order(mem, pick):
    units = copy.deepcopy(mem.implements)
    tvs = _views(mem)
    field_free = initial_free(mem)
    tasks, unassigned = [], {}
    for job in plannable_jobs(mem):
        opts = [(tv, build_task(mem, tv, job, units, field_free)) for tv in tvs.values()]
        opts = [o for o in opts if o[1]]
        if not opts:
            unassigned[job["id"]] = _reason(mem, tvs, job, units, field_free)
            continue
        tv, task = min(opts, key=lambda o: pick(o, job))
        _apply(task, tv, units, field_free)
        tasks.append(task)
    return tasks, unassigned, {}


# ---------------- original greedy coordinator ----------------
def _greedy(mem):
    units = copy.deepcopy(mem.implements)
    tvs = _views(mem)
    field_free = initial_free(mem)
    pending = plannable_jobs(mem)
    tasks, unassigned = [], {}
    while pending:
        best = None
        for job in pending:
            for tv in tvs.values():
                task = build_task(mem, tv, job, units, field_free)
                if not task or task["late"]:
                    continue
                s, _ = _score(mem, task, job)
                if best is None or s < best[0]:
                    best = (s, tv, task, job)
        if not best:
            for job in pending:
                unassigned[job["id"]] = _reason(mem, tvs, job, units, field_free)
            break
        _, tv, task, job = best
        _apply(task, tv, units, field_free)
        tasks.append(task)
        pending.remove(job)
    return tasks, unassigned


# ---------------- smart: beam search ----------------
def _task_cost(mem, task, prev_owner):
    """Secondary cost in minutes. Hard rules and on-time priority work are handled before this."""
    service = (mem.swap_minutes if task["swap"] else 0) + (mem.refuel_minutes if task["refuel"] else 0) \
        + (mem.recharge_minutes if task["recharge"] else 0)
    idle = task["start"] - task["depart"] - task["travel"] - service      # waiting for implement/field
    change = STABILITY_PENALTY if prev_owner.get(task["job"]) not in (None, task["tractor"]) else 0
    slack = task["deadline"] - task["end"]
    thin = SLACK_WEIGHT * task["priority"] * max(0.0, SLACK_BUFFER - slack) if slack >= 0 else 0.0
    return task["travel"] + 0.5 * service + idle + change + 0.02 * task["priority"] * task["end"] + thin


def _key(state, all_jobs):
    """Lexicographic: (1) most priority finished on time, (2) most priority finished at all,
    (3) least priority-weighted lateness, (4) least cost. Lower tuple is better."""
    on_time = sum(t["priority"] for t in state["tasks"] if not t["late"])
    done = sum(t["priority"] for t in state["tasks"])
    late = sum(t["priority"] * max(0, t["end"] - t["deadline"]) for t in state["tasks"])
    return (-on_time, -done, late, round(state["cost"], 6))


def _copy_state(s):
    return dict(units={k: dict(v) for k, v in s["units"].items()},
                tvs={k: dict(v) for k, v in s["tvs"].items()},
                field_free=dict(s["field_free"]), tasks=list(s["tasks"]),
                left=list(s["left"]), cost=s["cost"])


def _replay(mem, tasks_order, jobs):
    """Rebuild a schedule from a list of (job, tractor) choices. Used for the greedy seed."""
    s = dict(units=copy.deepcopy(mem.implements), tvs=_views(mem),
             field_free=initial_free(mem),
             tasks=[], left=[j["id"] for j in jobs], cost=0.0)
    for jid, tid in tasks_order:
        task = build_task(mem, s["tvs"][tid], mem.jobs[jid], s["units"], s["field_free"])
        if task is None:
            return None
        _apply(task, s["tvs"][tid], s["units"], s["field_free"])
        s["tasks"].append(task); s["left"].remove(jid)
    return s


def _fragility(mem, state):
    """Hindsight-style contingency check. For every tractor k and every task index i in k's
    schedule, imagine k fails just as task i begins: tasks before i are done, task i onward is
    lost and re-inserted greedily (best finish time) at the end of the other tractors' schedules.
    Damage = 100 x priority for each job that becomes late or impossible, plus extra driving.
    Fragility = average damage over all these failure scenarios. Lower = more robust."""
    scenarios, total = 0, 0.0
    tractors = sorted({t["tractor"] for t in state["tasks"]})
    for k in tractors:
        mine = [t for t in state["tasks"] if t["tractor"] == k]
        for i in range(len(mine)):
            lost = mine[i:]
            fail_t = lost[0]["depart"]
            others = {tid: dict(tv) for tid, tv in state["tvs"].items() if tid != k}
            scenarios += 1
            if not others:
                total += sum(100 * t["priority"] for t in lost)
                continue
            units = {uid: dict(u) for uid, u in state["units"].items()}
            for u in units.values():
                if u["holder"] == k:
                    u.update(holder=None, free_at=max(u["free_at"], fail_t + mem.recovery_minutes))
            ff = dict(state["field_free"])
            for t in sorted(lost, key=lambda t: (-t["priority"], t["deadline"], t["job"])):
                job = mem.jobs[t["job"]]
                opts = [(tv, build_task(mem, tv, job, units, ff)) for tv in others.values()]
                opts = [o for o in opts if o[1]]
                if not opts:
                    total += 100 * t["priority"]
                    continue
                tv, nt = min(opts, key=lambda o: (o[1]["late"], o[1]["end"], o[0]["id"]))
                total += max(0.0, nt["travel"] - t["travel"]) + (100 * t["priority"] if nt["late"] else 0)
                _apply(nt, tv, units, ff)
    return round(total / max(1, scenarios), 2)


def _repair(mem, jobs, prev_owner):
    """Plan REPAIR (vs full re-plan): keep every job on the tractor it already had, in the same
    order, wherever that is still feasible; then insert the displaced jobs one by one at the
    cheapest feasible spot (appending to a tractor's queue). Returns a schedule state or None."""
    s = dict(units=copy.deepcopy(mem.implements), tvs=_views(mem), field_free=initial_free(mem),
             tasks=[], left=[j["id"] for j in jobs], cost=0.0)
    old_order = [t for t in getattr(mem, "plan", []) if t["job"] in s["left"]]
    for ot in sorted(old_order, key=lambda t: (t["depart"], t["tractor"])):
        tv = s["tvs"].get(ot["tractor"])
        if tv is None:
            continue
        task = build_task(mem, tv, mem.jobs[ot["job"]], s["units"], s["field_free"])
        if task is None:
            continue
        _apply(task, tv, s["units"], s["field_free"])
        s["tasks"].append(task); s["left"].remove(ot["job"])
        s["cost"] += _task_cost(mem, task, prev_owner)
    for jid in sorted(s["left"], key=lambda j: (-mem.jobs[j]["priority"], mem.jobs[j]["deadline"], j)):
        best = None
        for tid, tv in s["tvs"].items():
            task = build_task(mem, tv, mem.jobs[jid], s["units"], s["field_free"])
            if task is None:
                continue
            k = (task["late"], _task_cost(mem, task, prev_owner), tid)
            if best is None or k < best[0]:
                best = (k, tid, task)
        if best:
            _, tid, task = best
            _apply(task, s["tvs"][tid], s["units"], s["field_free"])
            s["tasks"].append(task); s["left"].remove(jid)
            s["cost"] += _task_cost(mem, task, prev_owner)
    return s


def _beam(mem, width=BEAM_WIDTH):
    jobs = plannable_jobs(mem)
    prev_owner = dict(getattr(mem, "prev_owner", {}))
    root = dict(units=copy.deepcopy(mem.implements), tvs=_views(mem),
                field_free=initial_free(mem),
                tasks=[], left=[j["id"] for j in jobs], cost=0.0)
    beam, finals = [root], []
    expansions = 0
    while beam:
        children = []
        for s in beam:
            grown = False
            for jid in s["left"]:
                job = mem.jobs[jid]
                for tid, tv in s["tvs"].items():
                    task = build_task(mem, tv, job, s["units"], s["field_free"])
                    expansions += 1
                    if task is None:
                        continue
                    c = _copy_state(s)
                    _apply(task, c["tvs"][tid], c["units"], c["field_free"])
                    c["tasks"].append(task); c["left"].remove(jid)
                    c["cost"] += _task_cost(mem, task, prev_owner)
                    children.append(c); grown = True
            if not grown:
                finals.append(s)
        # de-duplicate states that reached the same situation by different orders
        seen, uniq = set(), []
        children.sort(key=lambda c: (_key(c, jobs), [(t["job"], t["tractor"]) for t in c["tasks"]]))
        for c in children:
            sig = (frozenset(c["left"]),
                   tuple(sorted((k, v["pos"], v["t"], v["unit"]) for k, v in c["tvs"].items())),
                   frozenset((t["job"], t["tractor"]) for t in c["tasks"]))
            if sig in seen:
                continue
            seen.add(sig); uniq.append(c)
        beam = uniq[:width]
    return jobs, finals, prev_owner, expansions


def _smart(mem, width=BEAM_WIDTH):
    jobs, finals, prev_owner, expansions = _beam(mem, width)
    # Always also consider the greedy schedule, so the beam can never do worse than greedy.
    g_tasks, _ = _greedy(mem)
    seed = _replay(mem, [(t["job"], t["tractor"]) for t in g_tasks], jobs)
    if seed is not None:
        seed["cost"] = sum(_task_cost(mem, t, prev_owner) for t in seed["tasks"])
        finals.append(seed)
    if mem.plan:
        rep = _repair(mem, jobs, prev_owner)
        rep["origin"] = "repair"
        finals.append(rep)
    if not finals:
        return [], {}, {}, {}
    finals.sort(key=lambda s: (_key(s, jobs), [(t["job"], t["tractor"]) for t in s["tasks"]]))
    best = finals[0]
    bk = _key(best, jobs)
    # Contingency-aware tie-break: among schedules equal on the first three goals and within
    # 5% on cost, take the one that survives a single tractor failure best.
    similar = [s for s in finals[:FRAGILITY_CANDIDATES]
               if _key(s, jobs)[:3] == bk[:3] and s["cost"] <= best["cost"] * (1 + FRAGILITY_TOLERANCE) + 1e-9]
    scored = sorted(((_fragility(mem, s), s["cost"], i, s) for i, s in enumerate(similar)),
                    key=lambda x: (x[0], x[1], x[2]))
    chosen_frag, _, _, chosen = scored[0]
    best_frag = _fragility(mem, best)
    info = dict(beam_width=width, expansions=expansions, candidates=len(finals),
                cost=round(chosen["cost"], 1), fragility=chosen_frag,
                fragility_of_cheapest=best_frag, picked_for_robustness=chosen is not best,
                mode="repair" if chosen.get("origin") == "repair" else "replan",
                repair_available=any(f.get("origin") == "repair" for f in finals))
    return chosen["tasks"], chosen["left"], info, prev_owner


def _explain_options(mem, tasks):
    """Replay the chosen schedule and, at each step, score every tractor for that job.
    This is what the 'why?' button shows (chosen tractor vs the next best)."""
    units = copy.deepcopy(mem.implements)
    tvs = _views(mem)
    field_free = initial_free(mem)
    why = {}
    for task in tasks:
        job = mem.jobs[task["job"]]
        opts = []
        for tid, tv in tvs.items():
            alt = build_task(mem, tv, job, units, field_free)
            if alt is None:
                cap = job["type"] in tv["caps"] and job["implement"] in tv["compat"]
                opts.append(dict(tractor=tid, feasible=False,
                                 reason="cannot do this job type" if not cap
                                 else "no free implement, no road, or not enough fuel/battery"))
                continue
            s, parts = _score(mem, alt, job)
            opts.append(dict(tractor=tid, feasible=True, score=round(s, 1), parts=parts,
                             start=alt["start"], end=alt["end"], travel=alt["travel"],
                             late=alt["late"], chosen=tid == task["tractor"]))
        why[task["job"]] = sorted(opts, key=lambda o: (not o.get("chosen", False), not o["feasible"],
                                                       o.get("score", 0)))
        _apply(task, tvs[task["tractor"]], units, field_free)
    return why


def make_plan(mem, policy="smart"):
    """Returns (tasks, unassigned, why). `why` maps job -> scored options per tractor."""
    if policy == "fcfs":
        return _plan_in_order(mem, lambda o, job: (o[0]["t"], o[0]["id"]))
    if policy == "nearest":
        return _plan_in_order(mem, lambda o, job: (travel(mem, o[0]["pos"], job["field"]), o[0]["id"]))
    if policy == "greedy":
        tasks, unassigned = _greedy(mem)
        return tasks, unassigned, _explain_options(mem, tasks)
    tasks, left, info, _ = _smart(mem)
    units = copy.deepcopy(mem.implements)
    field_free = initial_free(mem)
    tvs = _views(mem)
    unassigned = {jid: _reason(mem, tvs, mem.jobs[jid], units, field_free) for jid in left}
    why = _explain_options(mem, tasks)
    mem.search_info = info
    return tasks, unassigned, why
