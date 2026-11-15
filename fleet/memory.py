"""Shared memory: the ONE place facts live. Agents keep no memory of their own.

Everything is in-process Python objects (no database). The event log is append-only:
every important change is written as an immutable record with an id, a per-source
sequence number, the virtual time, and the plan version it belongs to.
"""
import copy, json, math

# Job states (DispatchOS-style workflow).
PENDING = "PENDING"
ASSIGNED = "ASSIGNED"
TRAVELING = "TRAVELING"
IN_PROGRESS = "IN_PROGRESS"
COMPLETED = "COMPLETED"
PARTIAL = "PARTIALLY_COMPLETED"
REASSIGN = "REASSIGNMENT_PENDING"
BLOCKED = "BLOCKED"
FAILED = "FAILED"
PAUSED = "PAUSED"            # owner tractor unreachable; ownership still held

# Jobs the planner may (re)plan. In-flight jobs are never in this set.
PLANNABLE = {PENDING, ASSIGNED, PARTIAL, REASSIGN, BLOCKED}


def make_segments(job, seg_minutes):
    """Split a job's work into sections (think: rows of a field). The last one may be shorter."""
    n = max(1, math.ceil(job["work_minutes"] / seg_minutes))
    segs, left = [], job["work_minutes"]
    for i in range(n):
        m = min(seg_minutes, left)
        left -= m
        segs.append(dict(id=f"{job['id']}-S{i + 1}", minutes=m, status="pending",
                         owner=None, done_by=None, done_at=None))
    return segs


class Memory:
    def __init__(self, scenario):
        s = copy.deepcopy(scenario)
        self.scenario = copy.deepcopy(scenario)
        self.name = s.get("name", "farm")
        self.start_clock = s.get("start_clock", "06:00")
        self.now = 0
        self.yard = s["yard"]
        self.cell_minutes = s.get("minutes_per_cell", 4)
        self.swap_minutes = s.get("swap_minutes", 10)
        self.refuel_minutes = s.get("refuel_minutes", 15)
        self.recharge_minutes = s.get("recharge_minutes", 20)
        self.recovery_minutes = s.get("recovery_minutes", 30)
        self.km_per_cell = s.get("km_per_cell", 1)
        self.segment_minutes = s.get("segment_minutes", 5)
        self.lease_minutes = s.get("lease_minutes", 15)        # tractor stops itself if unheard this long
        self.verify_minutes = s.get("verify_minutes", 8)       # time for a scout check
        self.fields = {f["id"]: f for f in s["fields"]}
        self.implements = {u["id"]: dict(u, holder=None, free_at=0, out_of_service=False)
                           for u in s["implements"]}
        self.tractors = {}
        for t in s["tractors"]:
            unit = t.get("implement")
            # fuel: minutes of driving + working left. battery: minutes of working left.
            # distance_km: total driven so far (the odometer).
            self.tractors[t["id"]] = dict(t, pos="YARD", available_at=0, status="ok",
                                          fuel=t["tank_minutes"], battery=t["battery_minutes"],
                                          distance_km=0, implement=unit, seq=0,
                                          last_plan_version=0, last_heard=0)
            if unit:
                self.implements[unit]["holder"] = t["id"]
        self.jobs = {}
        for i, j in enumerate(s["jobs"]):
            self.add_job(j, order=i, log=False)
        self.blocked = set()      # frozenset({a, b}) confirmed blocked
        self.uncertain = set()    # frozenset({a, b}) reported, not yet confirmed: treated as blocked
        self.plan = []            # tasks not started yet
        self.plan_version = 0
        self.active = {}          # tractor id -> task in progress
        self.history = []         # tasks that ran (fully or partly)
        self.unassigned = {}      # job id -> reason it cannot be done
        self.why = {}             # job id -> scored options for that job
        self.explanations = []    # plain-language plan changes
        self.decisions = []       # structured decision records (verification, recovery, ...)
        self.unconfirmed = {}     # tractor id -> segments done physically but not yet reported
        self.lost_work = 0.0      # minutes of work done but not confirmed in a whole segment
        self.repeated_work = 0.0  # minutes of work done twice
        self.replans = 0
        self.plan_ms = []
        self.log = []             # append-only event log
        self._seq = {}
        self._eid = 0

    # ----- jobs -----
    def add_job(self, j, order=None, log=True):
        j = dict(j)
        j.setdefault("arrival", self.now)
        segs = make_segments(j, self.segment_minutes)
        self.jobs[j["id"]] = dict(j, remaining=j["work_minutes"], status=PENDING,
                                  order=len(self.jobs) if order is None else order,
                                  segments=segs, completed_at=None, history=[])
        self.jobs[j["id"]]["history"].append(dict(t=self.now, status=PENDING, note="submitted"))
        if log:
            self.record("JOB_SUBMITTED", job=j["id"], type=j["type"], field=j["field"],
                        priority=j["priority"], deadline=j["deadline"])

    def set_status(self, job_id, status, note="", at=None):
        job = self.jobs[job_id]
        if job["status"] == status:
            return
        t = self.now if at is None else at
        job["history"].append(dict(t=t, status=status, note=note))
        self.record("JOB_STATE", at=at, job=job_id, frm=job["status"], to=status, note=note)
        job["status"] = status

    def pending_segments(self, job_id):
        return [s for s in self.jobs[job_id]["segments"] if s["status"] != "done"]

    def refresh_remaining(self, job_id):
        job = self.jobs[job_id]
        job["remaining"] = sum(s["minutes"] for s in job["segments"] if s["status"] != "done")
        return job["remaining"]

    # ----- event log -----
    def record(self, kind, source="coordinator", at=None, **data):
        t = self.now if at is None else at
        self._eid += 1
        self._seq[source] = self._seq.get(source, 0) + 1
        entry = {"id": f"E{self._eid:05d}", "seq": self._seq[source], "t": t,
                 "clock": self.clock(t), "event": kind, "source": source,
                 "plan_version": self.plan_version, **data}
        self.log.append(entry)
        return entry

    def clock(self, minutes):
        if minutes is None or minutes == float("inf"):
            return "--:--"
        h, m = map(int, self.start_clock.split(":"))
        total = h * 60 + m + int(round(minutes))
        return f"{total // 60 % 24:02d}:{total % 60:02d}"

    def save_log(self, path):
        with open(path, "w") as f:
            for e in self.log:
                f.write(json.dumps(e, default=str) + "\n")
