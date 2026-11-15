"""Explainer: turns plan changes into plain sentences, built only from solver data.

No AI key is needed: every sentence comes from a template filled with numbers the planner
and safety agent actually produced (travel minutes, finish times, which tractors could not
do the job and why). An AI model may later re-word these, but must never invent reasons.
"""

VERB = {"spray": "spray", "mow": "mow", "seed": "seed"}


def _c(mem, m):
    return mem.clock(m)


def _reasons(mem, job_id, tractor):
    """Short grounded reasons why `tractor` got `job_id`, from mem.why."""
    opts = mem.why.get(job_id) or []
    job = mem.jobs[job_id]
    me = next((o for o in opts if o["tractor"] == tractor and o.get("feasible")), None)
    if me is None:
        return ""
    bits = [f"it can {VERB.get(job['type'], job['type'])} with a {job['implement']}"]
    others = [o for o in opts if o["tractor"] != tractor]
    unable = [o["tractor"] for o in others if not o["feasible"]]
    able = sorted((o for o in others if o["feasible"]), key=lambda o: (o["late"], o["end"]))
    if able:
        nb = able[0]
        if nb["late"]:
            bits.append(f"{nb['tractor']} could only finish after the deadline")
        elif nb["end"] > me["end"]:
            bits.append(f"it finishes at {_c(mem, me['end'])}, {nb['end'] - me['end']:.0f} min sooner than "
                        f"{nb['tractor']}")
        elif nb["travel"] > me["travel"]:
            bits.append(f"it drives {me['travel']:.0f} min instead of {nb['travel']:.0f} for {nb['tractor']}")
        else:
            bits.append(f"it keeps the fleet's total plan better (next best: {nb['tractor']})")
    if unable:
        bits.append(f"{' and '.join(unable)} cannot take it")
    slack = job["deadline"] - me["end"]
    if slack >= 0:
        bits.append(f"it is done {slack:.0f} min before the {_c(mem, job['deadline'])} deadline")
    else:
        bits.append(f"it can only finish {-slack:.0f} min after the {_c(mem, job['deadline'])} deadline")
    return "; ".join(bits)


def explain(mem, old_plan, new_plan, cause):
    out = []
    if cause.get("lead"):
        out.append(cause["lead"])
    old = {t["job"]: t for t in old_plan}
    new = {t["job"]: t for t in new_plan}

    if cause["type"] == "initial":
        travel = sum(t["travel"] for t in new_plan)
        late = [t["job"] for t in new_plan if t["late"]]
        out.append(f"Starting plan: {len(new_plan)} jobs on {len({t['tractor'] for t in new_plan})} tractors, "
                   f"{travel:.0f} min of driving, " +
                   ("every job before its deadline." if not late else f"{', '.join(late)} cannot meet the deadline."))
        for t in new_plan:
            out.append(f"{t['job']} ({t['type']}, {t['field']}) -> {t['tractor']}, {_c(mem, t['start'])}-"
                       f"{_c(mem, t['end'])}: {_reasons(mem, t['job'], t['tractor'])}.")
    else:
        unchanged = 0
        for jid, t in new.items():
            r = _reasons(mem, jid, t["tractor"])
            rec = cause.get("recovered", {}).get(jid)
            if rec:
                out.append(f"{jid} moved to {t['tractor']}; only the remaining {t['work']:.0f} of "
                           f"{rec['total']} minutes are needed ({rec['done_segments']} sections already done). "
                           f"{t['tractor']} was chosen because {r}.")
            elif jid not in old:
                job = mem.jobs[jid]
                done = job["work_minutes"] - job["remaining"]
                out.append(f"{jid} is now planned on {t['tractor']} at {_c(mem, t['start'])}"
                           + (f" for the remaining {t['work']:.0f} of {job['work_minutes']} min" if done else "")
                           + f" because {r}.")
            elif old[jid]["tractor"] != t["tractor"]:
                out.append(f"{jid} moved from {old[jid]['tractor']} to {t['tractor']} because {r}.")
            elif abs(old[jid]["start"] - t["start"]) >= 10:
                d = t["start"] - old[jid]["start"]
                out.append(f"{jid} stays with {t['tractor']} but now starts at {_c(mem, t['start'])} "
                           f"({'+' if d > 0 else ''}{d:.0f} min).")
            else:
                unchanged += 1
        for jid, reason in sorted(mem.unassigned.items()):
            out.append(f"{jid} cannot be scheduled right now: {reason}.")
        late = [t["job"] for t in new_plan if t["late"]]
        if late:
            out.append(f"Warning: {', '.join(late)} will finish after the deadline; this is reported, not hidden.")
        if unchanged:
            out.append(f"Everything else stays as planned ({unchanged} job{'s' if unchanged != 1 else ''}).")
        kept = [a["job"] for a in mem.active.values()]
        if kept:
            out.append(f"Work already under way is kept as it is ({', '.join(sorted(kept))}).")
    return out


def why(mem, job_id):
    """Answer the operator's 'why?' for one job, from stored solver data."""
    job = mem.jobs.get(job_id)
    if job is None:
        return f"There is no job called {job_id}."
    if job["status"] == "COMPLETED":
        done_by = sorted({s["done_by"] for s in job["segments"] if s["done_by"]})
        return (f"{job_id} is finished (at {_c(mem, job['completed_at'])}, deadline {_c(mem, job['deadline'])}). "
                f"Sections were done under: {', '.join(done_by)}.")
    if job_id in mem.unassigned:
        return f"{job_id} is not scheduled: {mem.unassigned[job_id]}."
    task = next((t for t in mem.plan if t["job"] == job_id), None) or \
        next((a for a in mem.active.values() if a["job"] == job_id), None)
    if task is None:
        return f"{job_id} is {job['status']}."
    opts = mem.why.get(job_id) or []
    lines = [f"{job_id} goes to {task['tractor']} ({_c(mem, task['start'])}-{_c(mem, task['end'])}) because "
             f"{_reasons(mem, job_id, task['tractor'])}."]
    me = next((o for o in opts if o["tractor"] == task["tractor"] and o.get("feasible")), None)
    if me:
        parts = ", ".join(f"{k} {v:+.0f}" for k, v in me["parts"].items() if abs(v) >= 0.5)
        lines.append(f"Score parts for {task['tractor']} (lower is better): {parts} = {me['score']:.0f}.")
    for o in opts:
        if o["tractor"] == task["tractor"]:
            continue
        if not o["feasible"]:
            lines.append(f"{o['tractor']}: not possible ({o['reason']}).")
        else:
            lines.append(f"{o['tractor']}: would finish at {_c(mem, o['end'])}, drive {o['travel']:.0f} min, "
                         f"score {o['score']:.0f}{' (late)' if o['late'] else ''}.")
    if getattr(mem, "policy", "smart") == "smart":
        lines.append("Note: the final choice also looks ahead at all other jobs (beam search), "
                     "so the single-job score is only part of the story.")
    return " ".join(lines)
