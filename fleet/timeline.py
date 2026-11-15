"""Snapshots of the farm at a given minute, for out/timeline.json (read by the dashboard)."""
from .navigation import points
from .execution import phase_state

STATUS_LABEL = {"ok": None, "broken": "broken down", "comm_lost": "no signal", "unreachable": "no signal (stopped)"}


def snapshot(mem, t):
    pts = points(mem)
    tractors = {}
    for tid, tr in sorted(mem.tractors.items()):
        task = mem.active.get(tid)
        if task is not None:
            ps = phase_state(mem, task, t)
            d = dict(x=ps["xy"][0], y=ps["xy"][1], activity=ps["activity"], job=task["job"],
                     field=task["field"], fuel=round(ps["fuel"], 1), battery=round(ps["battery"], 1),
                     km=ps["km"], implement=ps["unit"])
        else:
            xy = tr.get("xy") or pts[tr["pos"]]
            d = dict(x=xy[0], y=xy[1], activity="idle", job=None, field=None, fuel=round(tr["fuel"], 1),
                     battery=round(tr["battery"], 1), km=round(tr["distance_km"], 2), implement=tr["implement"])
        label = STATUS_LABEL.get(tr["status"])
        if label:
            d["activity"] = label
        d["status"] = tr["status"]
        d["spraying"] = d["activity"] == "spraying"
        tractors[tid] = d
    fields = {fid: dict(job=None, covered=0.0, spraying=False, activity=None) for fid in mem.fields}
    for tid, task in mem.active.items():
        ps = phase_state(mem, task, t)
        if ps["activity"] in ("spraying", "mowing", "seeding") and mem.tractors[tid]["status"] == "ok":
            job = mem.jobs[task["job"]]
            before = job["work_minutes"] - sum(s["minutes"] for s in job["segments"]
                                               if s["status"] != "done" or s["id"] in task["segments"])
            fields[task["field"]] = dict(job=task["job"], covered=round(min(1.0, (before + ps["work"]) / job["work_minutes"]), 3),
                                         spraying=ps["activity"] == "spraying", activity=ps["activity"], tractor=tid)
    jobs = {jid: dict(status=j["status"], remaining=j["remaining"], priority=j["priority"],
                      done_segments=sum(s["status"] == "done" for s in j["segments"]),
                      total_segments=len(j["segments"]))
            for jid, j in mem.jobs.items()}
    return dict(t=t, clock=mem.clock(t), tractors=tractors, fields=fields, jobs=jobs,
                blocked=["-".join(sorted(e)) for e in mem.blocked],
                uncertain=["-".join(sorted(e)) for e in mem.uncertain], plan_version=mem.plan_version)
