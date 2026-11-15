"""Fleet agent: decides which tractor does which job, in what order.
policy="smart" is our coordinator. policy="fcfs" is the simple baseline
(first come, first served) used for the comparison."""
import copy
from .navigation import travel, path, YARD, INF


def _views(mem):
    return {tid: dict(id=tid, pos=t["pos"], t=max(t["available_at"], mem.now), fuel=t["fuel"],
                      unit=t["implement"], caps=t["capabilities"],
                      compat=t["compatible_implements"], tank=t["tank_minutes"])
            for tid, t in mem.tractors.items() if t["status"] == "ok"}


def build_task(mem, tv, job, units, field_free):
    """Work out everything tractor `tv` must do to complete `job`. None = cannot."""
    need = job["implement"]
    if job["type"] not in tv["caps"] or need not in tv["compat"]:
        return None
    st = dict(t=tv["t"], pos=tv["pos"], fuel=tv["fuel"], trav=0)
    legs = []

    def go(dest):
        d = travel(mem, st["pos"], dest)
        if d == INF:
            return False
        if d > 0:
            legs.append(path(mem, st["pos"], dest))
        st["t"] += d; st["fuel"] -= d; st["trav"] += d; st["pos"] = dest
        return True

    unit, prev, swap, yard_at, take_at = tv["unit"], tv["unit"], False, None, None
    if unit is None or units[unit]["type"] != need:
        free = [u for u in units.values() if u["type"] == need and u["holder"] is None]
        if not free:
            return None
        u = min(free, key=lambda u: (u["free_at"], u["id"]))
        if not go(YARD):
            return None
        yard_at = st["t"]
        take_at = max(st["t"], u["free_at"])
        st["t"] = take_at + mem.swap_minutes
        unit, swap = u["id"], True

    field, work = job["field"], job["remaining"]
    back = travel(mem, field, YARD)
    refuel = False
    if st["fuel"] < travel(mem, st["pos"], field) + work + back:
        if not go(YARD):
            return None
        st["t"] += mem.refuel_minutes
        st["fuel"] = tv["tank"]
        refuel = True
        if st["fuel"] < travel(mem, st["pos"], field) + work + back:
            return None
    if not go(field):
        return None
    start = max(st["t"], field_free.get(field, 0))
    return dict(tractor=tv["id"], job=job["id"], field=field, type=job["type"], depart=tv["t"],
                legs=legs, travel=st["trav"], swap=swap, refuel=refuel, unit=unit, prev_unit=prev,
                yard_at=yard_at, take_at=take_at, start=start, end=start + work, work=work,
                fuel_after=st["fuel"] - work, deadline=job["deadline"])


def _apply(task, tv, units, field_free):
    if task["swap"]:
        if task["prev_unit"]:
            units[task["prev_unit"]].update(holder=None, free_at=task["yard_at"])
        units[task["unit"]]["holder"] = tv["id"]
    tv.update(t=task["end"], pos=task["field"], fuel=task["fuel_after"], unit=task["unit"])
    field_free[task["field"]] = task["end"]


def _score(mem, task, job):
    """Lower is better. Every part is kept so the 'why?' button can show it."""
    parts = dict(travel=task["travel"],
                 swap=mem.swap_minutes if task["swap"] else 0,
                 refuel=mem.refuel_minutes if task["refuel"] else 0,
                 wait=0.3 * (task["start"] - mem.now),
                 priority=-20 * job["priority"],
                 slack=0.1 * (job["deadline"] - task["end"]))
    return sum(parts.values()), parts


def _reason(mem, tvs, job, units, field_free):
    able = [tv for tv in tvs.values() if job["type"] in tv["caps"] and job["implement"] in tv["compat"]]
    if not able:
        return f"no working tractor can do a {job['type']} job"
    built = [build_task(mem, tv, job, units, field_free) for tv in able]
    if not any(built):
        return f"no free {job['implement']}, or not enough fuel"
    return f"no tractor can finish it before the {mem.clock(job['deadline'])} deadline"


def make_plan(mem, policy="smart"):
    units = copy.deepcopy(mem.implements)
    tvs = _views(mem)
    field_free = {a["field"]: a["end"] for a in mem.active.values()}
    pending = sorted((j for j in mem.jobs.values() if j["status"] == "pending"), key=lambda j: j["order"])
    tasks, unassigned, why = [], {}, {}

    if policy == "fcfs":
        for job in pending:
            opts = [(tv, build_task(mem, tv, job, units, field_free)) for tv in tvs.values()]
            opts = [o for o in opts if o[1]]
            if not opts:
                unassigned[job["id"]] = _reason(mem, tvs, job, units, field_free)
                continue
            tv, task = min(opts, key=lambda o: (o[0]["t"], o[0]["id"]))
            _apply(task, tv, units, field_free)
            tasks.append(task)
        return tasks, unassigned, why

    while pending:
        best, options = None, {}
        for job in pending:
            for tv in tvs.values():
                task = build_task(mem, tv, job, units, field_free)
                if not task or task["end"] > job["deadline"]:
                    continue
                s, parts = _score(mem, task, job)
                options.setdefault(job["id"], []).append(dict(tractor=tv["id"], score=round(s, 1), parts=parts,
                                                              start=task["start"]))
                if best is None or s < best[0]:
                    best = (s, tv, task, job)
        if not best:
            for job in pending:
                unassigned[job["id"]] = _reason(mem, tvs, job, units, field_free)
            break
        _, tv, task, job = best
        why[job["id"]] = sorted(options[job["id"]], key=lambda o: o["score"])
        _apply(task, tv, units, field_free)
        tasks.append(task)
        pending.remove(job)
    return tasks, unassigned, why
