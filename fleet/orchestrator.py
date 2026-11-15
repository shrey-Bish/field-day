"""Orchestrator: runs the agents in a fixed order and moves virtual time forward.

    event -> advance(time) -> apply event -> Fleet (plan) -> Navigation (costs) -> Safety (check)
          -> save plan (only if safe) -> Explainer (plain words)

Time is virtual minutes after the scenario start. Everything is deterministic: the same
scenario and policy always give the same run.

Memory-preserving recovery: jobs are split into sections. When a tractor stops mid-job, the
sections it confirmed are kept forever; only the unfinished sections are re-planned.
"""
import copy, time
from . import navigation, safety, explainer, belief
from .navigation import INF, edges_of
from .memory import (Memory, PLANNABLE, ASSIGNED, TRAVELING, IN_PROGRESS, COMPLETED, PARTIAL,
                     REASSIGN, BLOCKED, FAILED, PAUSED)
from .events import EventBus, from_scenario
from .execution import phase_state, confirmed_segments, time_at_work
from .planner import make_plan

EPS = 1e-6


# ======================================================================
# setup
# ======================================================================
def new_run(scenario, policy="smart", extra_events=None):
    mem = Memory(scenario)
    mem.policy = policy
    mem.bus = EventBus()
    mem.comm = {}
    mem.block_times = {}
    mem.recoveries = []
    mem.prev_owner = {}
    mem.frames = []
    mem.pending_delivery = set()
    navigation.rebuild(mem)
    for msg in from_scenario(list(scenario.get("events", [])) + list(extra_events or [])):
        mem.bus.publish(msg)
    mem.record("RUN_STARTED", policy=policy, tractors=list(mem.tractors), jobs=list(mem.jobs))
    replan(mem, dict(type="initial", t=0))
    return mem


def _internal(mem, t, type_, **payload):
    mem._internal_n = getattr(mem, "_internal_n", 0) + 1
    mem.bus.publish(dict(id=f"I{mem._internal_n:03d}", t=t, source="coordinator", seq=None,
                         internal=True, type=type_, payload=payload))


# ======================================================================
# executing the plan
# ======================================================================
def _seg(mem, job_id, sid):
    return next(s for s in mem.jobs[job_id]["segments"] if s["id"] == sid)


def _activate(mem, task):
    tid = task["tractor"]
    tr = mem.tractors[tid]
    mem.plan.remove(task)
    task["before"] = dict(fuel=tr["fuel"], battery=tr["battery"], km=tr["distance_km"],
                          unit=tr["implement"], pos=tr["pos"])
    task["reported"] = set()
    task["work_started"] = False
    mem.active[tid] = task
    if task["swap"]:
        if task["prev_unit"]:
            mem.implements[task["prev_unit"]].update(holder=None, free_at=task["yard_at"])
        mem.implements[task["unit"]]["holder"] = tid
    # planning projection: where the tractor will be when this task is done
    tr.update(pos=task["field"], available_at=task["end"], fuel=task["fuel_after"],
              battery=task["battery_after"], distance_km=task["odometer_km"], implement=task["unit"])
    for sid in task["segments"]:
        _seg(mem, task["job"], sid)["owner"] = task["assignment"]
    mem.set_status(task["job"], TRAVELING, f"{tid} left for {task['field']}", at=task["depart"])
    _tractor_event(mem, tid, "JOB_STARTED", task["depart"], job=task["job"], field=task["field"],
                   assignment=task["assignment"], route=task["legs"])


def _tractor_event(mem, tid, kind, at, **data):
    tr = mem.tractors[tid]
    tr["seq"] += 1
    tr["last_heard"] = at
    return mem.record(kind, source=tid, at=at, tractor_seq=tr["seq"], **data)


def _complete(mem, tid):
    task = mem.active.pop(tid)
    job = mem.jobs[task["job"]]
    for sid in task["segments"]:
        s = _seg(mem, task["job"], sid)
        if s["status"] != "done":
            s.update(status="done", owner=None, done_by=task["assignment"], done_at=task["end"])
    mem.refresh_remaining(task["job"])
    ps = phase_state(mem, task, task["end"])
    mem.history.append(dict(_strip(task), actual_end=task["end"], interrupted=None,
                            work_done=task["work"], travel_done=task["travel"], fuel_end=ps["fuel"]))
    if job["remaining"] <= EPS:
        job["completed_at"] = task["end"]
        mem.set_status(task["job"], COMPLETED, f"finished by {tid}", at=task["end"])
        _tractor_event(mem, tid, "JOB_COMPLETED", task["end"], job=task["job"], field=task["field"],
                       on_time=task["end"] <= job["deadline"] + EPS)


def _strip(task):
    return {k: v for k, v in task.items() if k not in ("reported",)}


def advance(mem, t):
    """Run the plan forward to minute t: start trips, report finished sections, finish jobs."""
    while True:
        best = None   # (time, order, action, payload)
        for tid, task in sorted(mem.active.items()):
            if mem.tractors[tid]["status"] != "ok":
                continue          # silent tractor: the coordinator hears nothing from it
            if not task["work_started"]:
                best = _min(best, (task["start"], 1, "work", tid))
            acc = 0.0
            for sid in task["segments"]:
                acc += _seg(mem, task["job"], sid)["minutes"]
                if sid not in task["reported"]:
                    best = _min(best, (time_at_work(task, acc), 2, "segment", (tid, sid)))
                    break
            best = _min(best, (task["end"], 3, "complete", tid))
        firsts = {}
        for task in sorted(mem.plan, key=lambda x: (x["depart"], x["tractor"])):
            firsts.setdefault(task["tractor"], task)
        for tid, task in sorted(firsts.items()):
            if tid in mem.active or mem.tractors[tid]["status"] != "ok":
                continue
            if task["swap"] and mem.implements[task["unit"]]["holder"] not in (None, tid):
                continue      # hand-over not committed yet: never claim an implement someone holds
            best = _min(best, (task["depart"], 0, "depart", task))
        if best is None or best[0] > t + EPS:
            break
        when, _, action, payload = best
        mem.now = max(mem.now, when)
        if action == "depart":
            # depart only the first planned task of each tractor
            _activate(mem, payload)
        elif action == "work":
            task = mem.active[payload]
            task["work_started"] = True
            mem.set_status(task["job"], IN_PROGRESS, f"{payload} working in {task['field']}", at=task["start"])
        elif action == "segment":
            tid, sid = payload
            task = mem.active[tid]
            task["reported"].add(sid)
            s = _seg(mem, task["job"], sid)
            if s["status"] != "done":
                s.update(status="done", owner=None, done_by=task["assignment"], done_at=when)
            mem.refresh_remaining(task["job"])
            job = mem.jobs[task["job"]]
            _tractor_event(mem, tid, "SEGMENT_COMPLETED", when, job=task["job"], segment=sid,
                           done_minutes=job["work_minutes"] - job["remaining"], total_minutes=job["work_minutes"])
        elif action == "complete":
            _complete(mem, payload)
        # pending departures for other tractors are picked up in the next loop round
    mem.now = max(mem.now, t)


def _min(a, b):
    return b if a is None or b[:2] < a[:2] else a


# ======================================================================
# stopping a tractor mid-task without losing finished work
# ======================================================================
def interrupt(mem, tid, t_phys, reason, status="ok", confirmed_t=None, blocked_edge=None, recovery=True):
    """Stop tractor `tid`'s current task at physical time t_phys.
    Sections finished by `confirmed_t` (default: t_phys) are kept as done. Sections the tractor
    finished physically after its last report are kept aside as 'unconfirmed' (never lost,
    never double-counted). The unfinished rest goes back to planning."""
    task = mem.active.pop(tid)
    tr = mem.tractors[tid]
    job = mem.jobs[task["job"]]
    ps = phase_state(mem, task, t_phys)
    cascade = []
    conf_t = t_phys if confirmed_t is None else min(confirmed_t, t_phys)
    work_conf = phase_state(mem, task, conf_t)["work"]
    done_ids, done_min = confirmed_segments(task, work_conf, mem)
    phys_ids, phys_min = confirmed_segments(task, ps["work"], mem)
    for sid in done_ids:
        s = _seg(mem, task["job"], sid)
        if s["status"] != "done":
            s.update(status="done", owner=None, done_by=task["assignment"],
                     done_at=time_at_work(task, sum(_seg(mem, task["job"], x)["minutes"]
                                                    for x in task["segments"][:task["segments"].index(sid) + 1])))
    unconf = [sid for sid in phys_ids if sid not in done_ids]
    if unconf:
        mem.unconfirmed.setdefault(tid, []).extend((task["job"], sid) for sid in unconf)
        # done physically but unknown to us: assume it will be redone until the tractor reports it
        mem.repeated_work += sum(_seg(mem, task["job"], sid)["minutes"] for sid in unconf)
    lost = ps["work"] - phys_min           # minutes into a section that was not finished
    mem.lost_work += max(0.0, lost)
    for s in job["segments"]:
        if s["status"] != "done" and s["owner"] == task["assignment"]:
            s["owner"] = None
    mem.refresh_remaining(task["job"])

    # where does the tractor end up?
    node, extra, xy = ps["node"], 0.0, ps["xy"]
    if ps["edge"] is not None and status == "ok":
        e = ps["edge"]
        back, ahead = e["elapsed"], e["minutes"] - e["elapsed"]
        if blocked_edge is not None and frozenset((e["a"], e["b"])) == blocked_edge:
            node, extra = e["a"], back                         # never continue along a closed road
        elif back < ahead:
            node, extra = e["a"], back                         # nearer to turn back
        else:
            node, extra = e["b"], ahead                        # nearer to carry on
        xy = navigation.points(mem)[node]
    unit = ps["unit"]
    if task["swap"] and t_phys < next((p["t1"] for p in task["phases"] if p["kind"] == "swap"), INF):
        if mem.implements[task["unit"]]["holder"] == tid:     # only release what we actually held
            mem.implements[task["unit"]].update(holder=None, free_at=max(t_phys, mem.implements[task["unit"]]["free_at"]))
        if task["yard_at"] is not None and t_phys >= task["yard_at"]:
            unit = None                                        # old implement already left at the yard
        elif task["prev_unit"]:
            mem.implements[task["prev_unit"]]["holder"] = tid
            unit = task["prev_unit"]
            # The hand-over of prev_unit at the yard will not happen now: any tractor whose trip
            # counted on receiving it must stop and be re-planned too (never two holders).
            for oid, other in sorted(mem.active.items()):
                if other["swap"] and other["unit"] == task["prev_unit"]:
                    sw = next((ph["t1"] for ph in other["phases"] if ph["kind"] == "swap"), INF)
                    if t_phys < sw:
                        cascade.append(oid)
    tr.update(pos=node, available_at=t_phys + extra, fuel=ps["fuel"] - extra, battery=ps["battery"],
              distance_km=round(ps["km"] + extra / mem.cell_minutes * mem.km_per_cell, 2),
              implement=unit, xy=xy)
    if status != "ok" and unit:
        # a stopped tractor's implement is recovered to the yard after recovery_minutes
        mem.implements[unit].update(holder=None, free_at=t_phys + mem.recovery_minutes)
        tr["implement"] = None
    tr["status"] = status

    mem.history.append(dict(_strip(task), actual_end=t_phys, interrupted=reason, work_done=ps["work"],
                            travel_done=ps["drive"] + extra, fuel_end=ps["fuel"] - extra))
    if job["remaining"] > EPS:
        new = PARTIAL if any(s["status"] == "done" for s in job["segments"]) else REASSIGN
        mem.set_status(task["job"], new, f"{tid} stopped ({reason}); "
                       f"{job['work_minutes'] - job['remaining']:.0f} of {job['work_minutes']} min kept", at=t_phys)
        if recovery:
            mem.recoveries.append(dict(job=task["job"], t=t_phys, reason=reason, from_tractor=tid))
    else:
        job["completed_at"] = t_phys
        mem.set_status(task["job"], COMPLETED, f"all sections confirmed by {tid}", at=t_phys)
    info = dict(job=task["job"], field=task["field"], type=task["type"], tractor=tid,
                done_segments=sum(1 for s in job["segments"] if s["status"] == "done"),
                total_segments=len(job["segments"]), done_minutes=job["work_minutes"] - job["remaining"],
                remaining=job["remaining"], total=job["work_minutes"], lost_minutes=round(max(0.0, lost), 1),
                unconfirmed=len(unconf), stopped_at=node)
    mem.record("WORK_PRESERVED", at=t_phys, **info)
    for oid in cascade:
        if oid in mem.active:
            sub = interrupt(mem, oid, t_phys, f"implement hand-over from {tid} cancelled", status="ok",
                            recovery=recovery)
            info.setdefault("cascade", []).append(sub)
    if task["swap"] and task["prev_unit"] and mem.implements[task["prev_unit"]]["holder"] == tid and status == "ok":
        tr["implement"] = task["prev_unit"]
    return info


# ======================================================================
# events
# ======================================================================
def _affected_by_edge(mem, edge, t):
    """Active tractors that are on, or still have to drive, the given road."""
    hit = []
    for tid, task in sorted(mem.active.items()):
        for ph in task["phases"]:
            if ph["kind"] != "drive" or ph["t1"] <= t:
                continue
            clock = ph["t0"]
            p = ph["path"]
            for i in range(len(p) - 1):
                d = navigation.edge_minutes(mem, p[i], p[i + 1])
                if clock + d > t and frozenset((p[i], p[i + 1])) == edge:
                    hit.append(tid)
                clock += d
    return sorted(set(hit))


def handle(mem, msg):
    t, typ, p = msg["t"], msg["type"], msg["payload"]
    advance(mem, t)
    mem.now = t
    if not msg.get("internal"):
        mem.record("MESSAGE_RECEIVED", source=msg["source"], msg_id=msg["id"], msg_type=typ, msg_seq=msg["seq"])
    cause = dict(type=typ, t=t, lead="", recovered={})

    if typ == "TRACTOR_FAILED":
        tid = p["tractor"]
        tr = mem.tractors[tid]
        if tr["status"] == "broken":
            mem.record("IGNORED", reason=f"{tid} already marked broken (repeated failure report)", msg_id=msg["id"])
            return
        mem.record("TRACTOR_FAILED", source=tid, tractor=tid)
        if tid in mem.active:
            info = interrupt(mem, tid, t, "breakdown", status="broken")
            cause["recovered"][info["job"]] = info
            cause["lead"] = (f"Tractor {tid} broke down at {mem.clock(t)} while {_verb(info['type'])} "
                             f"{info['field']} ({info['job']}). {info['done_segments']} of {info['total_segments']} "
                             f"sections ({info['done_minutes']:.0f} of {info['total']} min) were confirmed done and are kept.")
        else:
            tr["status"] = "broken"
            if tr["implement"]:
                mem.implements[tr["implement"]].update(holder=None, free_at=t + mem.recovery_minutes)
                tr["implement"] = None
            cause["lead"] = f"Tractor {tid} broke down at {mem.clock(t)} while not working; nothing was lost."
        mem.record("TRACTOR_STOP_CONFIRMED", source=tid, tractor=tid, how="breakdown report")

    elif typ == "IMPLEMENT_FAILED":
        tid = p["tractor"]
        tr = mem.tractors[tid]
        uid = tr["implement"] if tid not in mem.active else phase_state(mem, mem.active[tid], t)["unit"]
        if uid is None or mem.implements[uid]["out_of_service"]:
            mem.record("IGNORED", reason=f"{tid} has no working implement to fail", msg_id=msg["id"])
            return
        info = interrupt(mem, tid, t, "implement failure") if tid in mem.active else None
        mem.implements[uid].update(out_of_service=True, holder=None, free_at=INF)
        tr["implement"] = None
        mem.record("IMPLEMENT_FAILED", source=tid, tractor=tid, implement=uid)
        cause["lead"] = f"The {mem.implements[uid]['type']} {uid} on {tid} failed at {mem.clock(t)} and is out of service."
        if info:
            cause["recovered"][info["job"]] = info
            cause["lead"] += (f" {info['done_segments']} of {info['total_segments']} sections of {info['job']} "
                              f"were already done and are kept.")

    elif typ in ("ROUTE_BLOCKED", "OBSTACLE_DETECTED"):
        edge = frozenset(p["between"])
        old_dist = copy.deepcopy(mem.dist)
        confirmed = typ == "ROUTE_BLOCKED"
        if confirmed:
            mem.blocked.add(edge); mem.uncertain.discard(edge)
        else:
            mem.uncertain.add(edge)
        spans = mem.block_times.setdefault(edge, [])
        if not spans or spans[-1][1] is not None:
            spans.append([t, None])
        navigation.rebuild(mem)
        name = "-".join(sorted(edge))
        mem.record(typ, source=p.get("tractor", "operator"), road=name, confirmed=confirmed)
        cause["lead"] = (f"The road {name} was {'blocked' if confirmed else 'reported as possibly blocked'} "
                         f"at {mem.clock(t)}; it is closed for planning{'' if confirmed else ' until confirmed clear'}.")
        for tid in _affected_by_edge(mem, edge, t):
            info = interrupt(mem, tid, t, f"road {name} closed", status="ok", blocked_edge=edge)
            cause["recovered"][info["job"]] = info
            cause["lead"] += f" {tid} was on its way and holds at {info['stopped_at']}."
        if not confirmed:
            detour = 0.0
            for task in mem.plan:
                for leg in task["legs"]:
                    if edge in edges_of(leg):
                        detour += mem.dist[leg[0]][leg[-1]] - old_dist[leg[0]][leg[-1]]
            dec = belief.obstacle_decision(mem, edge, detour)
            dec["t"] = t
            mem.decisions.append(dec)
            mem.record("VERIFICATION_DECISION", **dec)
            cause["lead"] += f" Decision: {dec['decision']} ({dec['reason']})."
            if dec["decision"] == "verify":
                _internal(mem, t + mem.verify_minutes, "OBSTACLE_VERIFIED", between=p["between"],
                          actual=p.get("actual", "blocked"))

    elif typ == "OBSTACLE_VERIFIED":
        edge = frozenset(p["between"])
        name = "-".join(sorted(edge))
        if edge not in mem.uncertain:
            return
        mem.uncertain.discard(edge)
        if p["actual"] == "clear":
            _reopen(mem, edge, t)
            cause["lead"] = f"The scout check at {mem.clock(t)} found road {name} clear; it is open again."
        else:
            mem.blocked.add(edge)
            cause["lead"] = f"The scout check at {mem.clock(t)} confirmed road {name} is blocked."
        navigation.rebuild(mem)
        mem.record("OBSTACLE_VERIFIED", road=name, result=p["actual"])

    elif typ == "ROUTE_CLEARED":
        edge = frozenset(p["between"])
        mem.blocked.discard(edge); mem.uncertain.discard(edge)
        _reopen(mem, edge, t)
        navigation.rebuild(mem)
        mem.record("ROUTE_CLEARED", road="-".join(sorted(edge)))
        cause["lead"] = f"The road {'-'.join(sorted(edge))} was reopened at {mem.clock(t)}."

    elif typ == "PRIORITY_CHANGED":
        job = mem.jobs[p["job"]]
        old = job["priority"]
        job["priority"] = p["priority"]
        mem.record("PRIORITY_CHANGED", job=p["job"], frm=old, to=p["priority"])
        cause["lead"] = f"The operator changed {p['job']}'s priority from {old} to {p['priority']} at {mem.clock(t)}."

    elif typ == "JOB_SUBMITTED":
        j = dict(p["job"]); j["arrival"] = t
        if j["id"] in mem.jobs:
            mem.record("IGNORED", reason=f"job {j['id']} already exists", msg_id=msg["id"])
            return
        mem.add_job(j)
        cause["lead"] = (f"A new {'urgent ' if j['priority'] >= 3 else ''}{j['type']} job {j['id']} for {j['field']} "
                         f"arrived at {mem.clock(t)} (deadline {mem.clock(j['deadline'])}).")

    elif typ == "LOW_ENERGY":
        tid = p["tractor"]
        if mem.tractors[tid]["status"] != "ok":
            return
        info = interrupt(mem, tid, t, "low fuel", status="ok") if tid in mem.active else None
        tr = mem.tractors[tid]
        drop = p.get("drop", 0)
        tr["fuel"] = max(0.0, (p["fuel"] if "fuel" in p else tr["fuel"] - drop))
        mem.record("LOW_ENERGY", source=tid, tractor=tid, fuel=tr["fuel"])
        cause["lead"] = f"{tid} reported low fuel ({tr['fuel']:.0f} min left) at {mem.clock(t)}."
        if info:
            cause["recovered"][info["job"]] = info
            cause["lead"] += f" It paused {info['job']} with {info['done_minutes']:.0f} of {info['total']} min done (kept)."

    elif typ == "COMMUNICATION_LOST":
        tid = p["tractor"]
        tr = mem.tractors[tid]
        if tr["status"] != "ok":
            return
        tr["status"] = "comm_lost"
        mem.comm[tid] = dict(t0=t, truth=p.get("actual", "working"), lease_t=t + mem.lease_minutes)
        mem.record("COMMUNICATION_LOST", tractor=tid, last_heard=tr["last_heard"])
        dec = belief.comm_loss_decision(mem, tid)
        dec["t"] = t
        mem.decisions.append(dec)
        mem.record("VERIFICATION_DECISION", **dec)
        if tid in mem.active:
            mem.set_status(mem.active[tid]["job"], PAUSED, f"{tid} silent; it still owns this work")
        _internal(mem, t + mem.lease_minutes, "LEASE_EXPIRED", tractor=tid)
        if dec["decision"] == "verify":
            _internal(mem, t + mem.verify_minutes, "COMM_VERIFIED", tractor=tid)
        cause["lead"] = (f"No messages from {tid} since {mem.clock(t)}. Its work is NOT handed over yet: "
                         f"{tid} stops itself if it hears nothing for {mem.lease_minutes} min. "
                         f"Decision: {dec['decision'].replace('_', ' ')} ({dec['reason']}). Other jobs carry on.")

    elif typ == "COMM_VERIFIED":
        tid = p["tractor"]
        c = mem.comm.get(tid)
        if not c or mem.tractors[tid]["status"] != "comm_lost":
            return
        if c["truth"] == "working":
            mem.record("VERIFICATION_RESULT", tractor=tid, result="still working; ownership kept")
            return
        info = interrupt(mem, tid, c["t0"], "breakdown (found by scout check)", status="broken",
                         confirmed_t=c["t0"]) if tid in mem.active else None
        mem.tractors[tid]["status"] = "broken"
        mem.record("TRACTOR_STOP_CONFIRMED", tractor=tid, how="scout check")
        cause["lead"] = f"A scout check at {mem.clock(t)} found {tid} broken down (stopped since {mem.clock(c['t0'])})."
        if info:
            cause["recovered"][info["job"]] = info

    elif typ == "LEASE_EXPIRED":
        tid = p["tractor"]
        c = mem.comm.get(tid)
        if not c or mem.tractors[tid]["status"] != "comm_lost":
            return
        info = None
        if tid in mem.active:
            task = mem.active[tid]
            t_phys = min(c["lease_t"], task["end"]) if c["truth"] == "working" else c["t0"]
            info = interrupt(mem, tid, t_phys, "no signal; safety lease expired",
                             status="unreachable", confirmed_t=c["t0"])
        else:
            mem.tractors[tid]["status"] = "unreachable"
        mem.record("TRACTOR_STOP_CONFIRMED", tractor=tid, how=f"authorisation lease expired ({mem.lease_minutes} min)")
        cause["lead"] = (f"{tid} has been silent for {mem.lease_minutes} min, so by protocol it has stopped. "
                         f"Work it confirmed before going silent is kept; the rest can now be reassigned safely.")
        if info:
            cause["recovered"][info["job"]] = info

    elif typ == "COMMUNICATION_RESTORED":
        tid = p["tractor"]
        tr = mem.tractors[tid]
        c = mem.comm.pop(tid, None)
        if tr["status"] == "comm_lost":
            if c and c["truth"] != "working":
                if tid in mem.active:
                    info = interrupt(mem, tid, c["t0"], "breakdown (reported on reconnect)", status="broken")
                    cause["recovered"][info["job"]] = info
                tr["status"] = "broken"
                cause["lead"] = f"{tid} reconnected at {mem.clock(t)} and reported it had broken down."
            else:
                tr["status"] = "ok"
                if tid in mem.active:
                    mem.set_status(mem.active[tid]["job"], IN_PROGRESS, f"{tid} back in contact")
                advance(mem, t)       # catch up on the sections it reports now
                cause["lead"] = f"{tid} reconnected at {mem.clock(t)} before its lease ran out and carried on."
        elif tr["status"] == "unreachable":
            tr["status"] = "ok"
            kept, repeated = 0, 0.0
            for jid, sid in mem.unconfirmed.pop(tid, []):
                s = _seg(mem, jid, sid)
                owner_active = any(a["job"] == jid and sid in a["segments"] for a in mem.active.values())
                if s["status"] == "done" or owner_active:
                    repeated += s["minutes"]
                else:
                    s.update(status="done", owner=None, done_by=f"{tid} (reported late)", done_at=t)
                    kept += 1
                    mem.repeated_work -= s["minutes"]     # it was not redone after all
                mem.refresh_remaining(jid)
                if mem.jobs[jid]["remaining"] <= EPS and mem.jobs[jid]["status"] != COMPLETED:
                    mem.jobs[jid]["completed_at"] = t
                    mem.set_status(jid, COMPLETED, "finished sections reported after reconnect")
            cause["lead"] = (f"{tid} reconnected at {mem.clock(t)}. It reported {kept} extra finished "
                             f"section(s), now counted as done" + (f"; {repeated:.0f} min had already been redone."
                                                                     if repeated else "."))
        else:
            return
        mem.record("COMMUNICATION_RESTORED", tractor=tid)
    elif typ == "FUEL_DELIVERED":
        tid = p["tractor"]
        tr = mem.tractors[tid]
        mem.pending_delivery.discard(tid)
        if tr["status"] != "ok":
            return
        tr["fuel"] = tr["tank_minutes"]
        mem.record("FUEL_DELIVERED", tractor=tid, at_node=tr["pos"])
        cause["lead"] = f"A fuel delivery reached {tid} at {tr['pos']} at {mem.clock(t)}; it is back in service."

    elif typ == "TRACTOR_DELAYED":
        tid, d = p["tractor"], float(p["minutes"])
        task = mem.active.get(tid)
        if task is None or mem.tractors[tid]["status"] != "ok":
            mem.record("IGNORED", reason=f"{tid} has no task under way to delay", msg_id=msg["id"])
            return
        old_end = task["end"]
        _shift(task, t, d)
        mem.tractors[tid]["available_at"] = task["end"]
        mem.record("TRACTOR_DELAYED", source=tid, tractor=tid, minutes=d, job=task["job"],
                   new_end=task["end"])
        cause["lead"] = (f"{tid} is running {d:.0f} min late on {task['job']} (now done at "
                         f"{mem.clock(task['end'])} instead of {mem.clock(old_end)}).")
        # knock-on effects on work already under way: anyone queued behind it in the same field
        # or on the same single-lane road waits longer; everything not started is re-timed by the re-plan.
        for oid, o in sorted(mem.active.items()):
            if oid == tid:
                continue
            push = 0.0
            if o["field"] == task["field"] and o["start"] >= old_end - EPS and o["start"] < task["end"]:
                push = task["end"] - o["start"]
            for key, r0, r1, who in task.get("reservations", ()):
                for k2, s0, s1, w2 in o.get("reservations", ()):
                    if k2 == key and s0 < r1 and r0 < s1 and s0 >= t:
                        push = max(push, r1 - s0)
            if push > 0:
                _shift(o, max(t, o["depart"]), push)
                mem.tractors[oid]["available_at"] = o["end"]
                cause["lead"] += f" {oid} has to wait {push:.0f} min behind it."
    else:
        mem.record("IGNORED", reason=f"unknown event type {typ}", msg_id=msg["id"])
        return
    replan(mem, cause)


def _shift(task, t, d):
    """Push everything in a task that has not happened by minute t back by d minutes.
    The phase under way is split around an explicit 'delay' phase (a drive leg is finished
    first, then the delay), so work, fuel and battery accounting stay exact."""
    out, done = [], False
    for ph in task["phases"]:
        if done:
            out.append(dict(ph, t0=ph["t0"] + d, t1=ph["t1"] + d))
            continue
        if ph["t1"] <= t:
            out.append(ph)
            continue
        if ph["t0"] >= t:
            out.append(dict(kind="delay", t0=ph["t0"], t1=ph["t0"] + d, at=ph.get("at") or ph["path"][0]))
            out.append(dict(ph, t0=ph["t0"] + d, t1=ph["t1"] + d))
        elif ph["kind"] == "drive":
            out.append(ph)
            out.append(dict(kind="delay", t0=ph["t1"], t1=ph["t1"] + d, at=ph["path"][-1]))
        else:
            out.append(dict(ph, t1=t))
            out.append(dict(kind="delay", t0=t, t1=t + d, at=ph["at"]))
            out.append(dict(ph, t0=t + d, t1=ph["t1"] + d))
        done = True
    task["phases"] = out
    works = [ph for ph in out if ph["kind"] == "work"]
    task["start"], task["end"] = works[0]["t0"], works[-1]["t1"]
    for k in ("take_at", "yard_at"):
        if task.get(k) is not None and task[k] >= t:
            task[k] += d
    task["late"] = task["end"] > task["deadline"]
    task["reservations"] = [(k, a + (d if a >= t else 0), b + (d if b > t else 0), w)
                            for k, a, b, w in task.get("reservations", ())]


def _reopen(mem, edge, t):
    spans = mem.block_times.get(edge) or []
    if spans and spans[-1][1] is None:
        spans[-1][1] = t


def _verb(kind):
    return {"spray": "spraying", "mow": "mowing", "seed": "seeding"}.get(kind, "working")


# ======================================================================
# re-planning: Fleet -> Navigation -> Safety -> save -> Explainer
# ======================================================================
def _outcome_key(m, tasks):
    """(1) priority finished on time, (2) priority finished, (3) priority-weighted lateness,
    counting both work under way and the new plan. Lower tuple is better."""
    allt = list(m.active.values()) + list(tasks)
    on_time = sum(m.jobs[t["job"]]["priority"] for t in allt if t["end"] <= t["deadline"] + EPS)
    done = sum(m.jobs[t["job"]]["priority"] for t in allt)
    late = sum(m.jobs[t["job"]]["priority"] * max(0.0, t["end"] - t["deadline"]) for t in allt)
    return (-on_time, -done, round(late, 6))


def _consider_redirect(mem, cause):
    """A tractor that is only DRIVING to a job (no work started) may be turned around if that gives
    a strictly better outcome on the first three goals. Work already started is never disturbed."""
    if mem.policy != "smart" or cause["type"] == "initial":
        return
    movers = sorted(tid for tid, a in mem.active.items()
                    if not a["work_started"] and mem.tractors[tid]["status"] == "ok")
    if not movers:
        return
    keep_tasks, _, _ = make_plan(mem, "smart")
    base = _outcome_key(mem, keep_tasks)
    saved = mem.frames, mem.log
    mem.frames, mem.log = [], []
    try:
        trial = copy.deepcopy(mem)
    finally:
        mem.frames, mem.log = saved
    for tid in movers:
        if tid in trial.active:      # may already be stopped by a cancelled implement hand-over
            interrupt(trial, tid, trial.now, "redirected by new plan", status="ok", recovery=False)
    new_tasks, _, _ = make_plan(trial, "smart")
    better = _outcome_key(trial, new_tasks)
    if better < base:
        names = []
        for tid in movers:
            if tid not in mem.active:
                continue
            job = mem.active[tid]["job"]
            interrupt(mem, tid, mem.now, "redirected by new plan", status="ok", recovery=False)
            names.append(f"{tid} (was driving to {job})")
        cause["lead"] = (cause.get("lead", "") + f" To finish more high-priority work on time, "
                         f"{', '.join(names)} changed course before starting work; nothing finished is affected.").strip()
        mem.record("REDIRECTED", tractors=movers, before=list(base), after=list(better))


def _check_stranded(mem):
    """A tractor that cannot even reach the yard on the fuel it has is stranded. Call a fuel
    delivery to where it stands (arrives after recovery_minutes). Its implement stays with it."""
    for tid, tr in sorted(mem.tractors.items()):
        if tr["status"] != "ok" or tid in mem.active or tid in mem.pending_delivery:
            continue
        if tr["pos"] != "YARD" and tr["fuel"] < navigation.travel(mem, tr["pos"], "YARD"):
            mem.pending_delivery.add(tid)
            mem.record("LOW_ENERGY", source=tid, tractor=tid, fuel=tr["fuel"], stranded=True,
                       action=f"fuel delivery called, arrives in {mem.recovery_minutes} min")
            _internal(mem, mem.now + mem.recovery_minutes, "FUEL_DELIVERED", tractor=tid)


def replan(mem, cause):
    _check_stranded(mem)
    _consider_redirect(mem, cause)
    old = [dict(t) for t in mem.plan]
    for t in old:
        mem.prev_owner[t["job"]] = t["tractor"]
    mem.record("REPLAN_REQUESTED", cause=cause["type"])
    t0 = time.perf_counter()
    tasks, unassigned, why = make_plan(mem, mem.policy)
    violations = safety.validate(mem, tasks, unassigned)
    hard, deadline = safety.split(violations)
    if hard:
        mem.record("PLAN_REJECTED", policy=mem.policy, violations=hard)
        # fall back to the simple greedy planner; if that is unsafe too, plan nothing new
        tasks, unassigned, why = make_plan(mem, "greedy")
        violations = safety.validate(mem, tasks, unassigned)
        hard, deadline = safety.split(violations)
        if hard:
            mem.record("PLAN_REJECTED", policy="greedy", violations=hard)
            unassigned = {j["id"]: "held back: the safety agent rejected every plan"
                          for j in mem.jobs.values() if j["status"] in PLANNABLE and j["remaining"] > 0}
            tasks, why, deadline = [], {}, []
    mem.plan_ms.append((time.perf_counter() - t0) * 1000)
    mem.replans += 1
    mem.plan_version += 1
    v = mem.plan_version
    for task in tasks:
        task["assignment"] = f"A{v}-{task['job']}-{task['tractor']}"
        task["plan_version"] = v
    mem.plan = tasks
    mem.unassigned = unassigned
    mem.why.update(why)
    for task in tasks:
        job = mem.jobs[task["job"]]
        done = job["work_minutes"] - job["remaining"]
        mem.set_status(task["job"], ASSIGNED, f"to {task['tractor']}, plan v{v}"
                       + (f" ({done:.0f} of {job['work_minutes']} min already done)" if done else ""))
    for jid, reason in unassigned.items():
        mem.set_status(jid, BLOCKED, reason)
    mem.record("PLAN_APPROVED", policy=mem.policy, cause=cause["type"], jobs=len(tasks),
               travel=sum(t["travel"] for t in tasks), deadline_warnings=deadline,
               unassigned=unassigned, search=getattr(mem, "search_info", None) if mem.policy == "smart" else None)
    old_map = {t["job"]: t["tractor"] for t in old}
    for task in tasks:
        if old_map.get(task["job"]) != task["tractor"]:
            mem.record("JOB_ASSIGNED", job=task["job"], tractor=task["tractor"], assignment=task["assignment"],
                       segments=len(task["segments"]), work=task["work"], start=task["start"], end=task["end"])
    for tid in sorted({t["tractor"] for t in tasks}):
        mem.tractors[tid]["last_plan_version"] = v
        _tractor_event(mem, tid, "JOB_ACCEPTED", mem.now, plan_version=v,
                       jobs=[t["job"] for t in tasks if t["tractor"] == tid])
    sentences = explainer.explain(mem, old, tasks, cause)
    mem.explanations.append(dict(t=mem.now, clock=mem.clock(mem.now), cause=cause["type"],
                                 plan_version=v, sentences=sentences))
    mem.record("PLAN_EXPLAINED", sentences=sentences)


# ======================================================================
# the main loop
# ======================================================================
def run(mem, step=5, max_t=24 * 60, frames=True):
    from .timeline import snapshot
    ft = 0
    while True:
        nxt = mem.bus.peek_time()
        if nxt <= ft and nxt <= max_t:
            msg = mem.bus.pop()
            if msg is not None:
                handle(mem, msg)
            continue
        advance(mem, ft)
        if frames:
            mem.frames.append(snapshot(mem, ft))
        idle = not mem.active and not mem.plan and mem.bus.empty()
        if idle or ft >= max_t:
            break
        ft += step
    for j in mem.jobs.values():
        if j["status"] != COMPLETED:
            mem.set_status(j["id"], FAILED, mem.unassigned.get(j["id"], "not finished when the run ended"))
    mem.record("RUN_FINISHED", completed=sum(j["status"] == COMPLETED for j in mem.jobs.values()))
    return mem


def simulate(scenario, policy="smart", extra_events=None, step=5, frames=True):
    return run(new_run(scenario, policy, extra_events), step=step, frames=frames)
