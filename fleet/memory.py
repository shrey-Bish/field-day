"""Shared memory: the ONE place facts live. Agents keep no memory of their own."""
import copy, json


class Memory:
    def __init__(self, scenario):
        s = copy.deepcopy(scenario)
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
        self.fields = {f["id"]: f for f in s["fields"]}
        self.implements = {u["id"]: dict(u, holder=None, free_at=0) for u in s["implements"]}
        self.tractors = {}
        for t in s["tractors"]:
            unit = t.get("implement")
            # fuel: minutes of driving + working left. battery: minutes of working left.
            # distance_km: total driven so far (the odometer).
            self.tractors[t["id"]] = dict(t, pos="YARD", available_at=0, status="ok",
                                          fuel=t["tank_minutes"], battery=t["battery_minutes"],
                                          distance_km=0, implement=unit)
            if unit:
                self.implements[unit]["holder"] = t["id"]
        self.jobs = {j["id"]: dict(j, remaining=j["work_minutes"], status="pending", order=i)
                     for i, j in enumerate(s["jobs"])}
        self.blocked = set()      # frozenset({a, b}) pairs that cannot be driven
        self.plan = []            # tasks not started yet
        self.active = {}          # tractor id -> task in progress
        self.history = []         # tasks that ran (fully or partly)
        self.unassigned = {}      # job id -> reason it cannot be done
        self.why = {}             # job id -> why that tractor was chosen
        self.log = []             # append-only event log

    def record(self, kind, **data):
        entry = {"t": self.now, "clock": self.clock(self.now), "event": kind, **data}
        self.log.append(entry)
        return entry

    def clock(self, minutes):
        h, m = map(int, self.start_clock.split(":"))
        total = h * 60 + m + int(round(minutes))
        return f"{total // 60 % 24:02d}:{total % 60:02d}"

    def save_log(self, path):
        with open(path, "w") as f:
            for e in self.log:
                f.write(json.dumps(e) + "\n")
