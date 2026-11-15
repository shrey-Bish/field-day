"""Where is a tractor, and how much has it used, at virtual minute t of a task?

Pure function of a task's phase list. Used by the orchestrator (breakdowns, interruptions)
and for the dashboard timeline. A tractor never teleports: positions are interpolated
along the road edges it is actually driving."""
from .navigation import points, edge_minutes

ACTIVITY = {"drive": "driving", "wait_implement": "waiting for implement", "swap": "changing implement",
            "refuel": "refuelling", "recharge": "recharging", "wait_field": "waiting for field",
            "wait_road": "waiting for single-lane road", "delay": "delayed"}
WORKING = {"spray": "spraying", "mow": "mowing", "seed": "seeding"}


def phase_state(mem, task, t):
    b = task["before"]
    pts = points(mem)
    fuel, battery = b["fuel"], b["battery"]
    drive, work = 0.0, 0.0
    node = b["pos"]
    xy = pts[node]
    unit = b["unit"]
    activity, edge = "idle", None
    for ph in task["phases"]:
        if t <= ph["t0"]:
            break
        upto = min(t, ph["t1"])
        el = upto - ph["t0"]
        k = ph["kind"]
        if k == "drive":
            p = ph["path"]
            clock = ph["t0"]
            for i in range(len(p) - 1):
                d = edge_minutes(mem, p[i], p[i + 1])
                if upto >= clock + d:
                    node, xy = p[i + 1], pts[p[i + 1]]
                    clock += d
                    continue
                f = (upto - clock) / d if d else 1
                a, c = pts[p[i]], pts[p[i + 1]]
                xy = (a[0] + (c[0] - a[0]) * f, a[1] + (c[1] - a[1]) * f)
                edge = dict(a=p[i], b=p[i + 1], elapsed=upto - clock, minutes=d)
                break
            fuel -= el
            drive += el
        elif k == "work":
            fuel -= el
            battery -= el
            work += el
            node, xy = task["field"], pts[task["field"]]
        elif k == "refuel" and upto >= ph["t1"]:
            fuel = mem.tractors[task["tractor"]]["tank_minutes"]
        elif k == "recharge" and upto >= ph["t1"]:
            battery = mem.tractors[task["tractor"]]["battery_minutes"]
        elif k == "swap" and upto >= ph["t1"]:
            unit = task["unit"]
        if t < ph["t1"]:
            activity = WORKING.get(task["type"], "working") if k == "work" else ACTIVITY[k]
            if k != "drive":
                edge = None
            break
        edge = None
    else:
        activity = "idle"
    if t >= task["end"]:
        activity = "idle"
    km = b["km"] + drive / mem.cell_minutes * mem.km_per_cell
    return dict(xy=(round(xy[0], 3), round(xy[1], 3)), node=node, activity=activity,
                drive=drive, work=work, fuel=fuel, battery=battery, km=round(km, 2),
                unit=unit, edge=edge)


def confirmed_segments(task, work_minutes, mem):
    """Which of the task's sections are fully finished after `work_minutes` of work."""
    done, acc = [], 0.0
    for sid in task["segments"]:
        seg = next(s for s in mem.jobs[task["job"]]["segments"] if s["id"] == sid)
        if acc + seg["minutes"] <= work_minutes + 1e-6:
            acc += seg["minutes"]
            done.append(sid)
        else:
            break
    return done, acc


def time_at_work(task, w):
    """Clock time at which the task has done `w` minutes of work (work may be split by delays)."""
    acc = 0.0
    for ph in task["phases"]:
        if ph["kind"] != "work":
            continue
        dur = ph["t1"] - ph["t0"]
        if acc + dur >= w - 1e-9:
            return ph["t0"] + (w - acc)
        acc += dur
    return task["end"]
