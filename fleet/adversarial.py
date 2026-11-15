"""Adversarial scenario testing (failure-driven self-evaluation).

Generates seeded, reproducible hard scenarios (breakdowns mid-job, the only implement holder
failing, road closures, urgent jobs, low fuel, single-lane congestion, communication loss,
delays), runs every coordinator on each, and checks invariants on what actually ran:

  - no finished work lost (finished sections never reset)
  - no incompatible tractor did a job
  - no two tractors worked one field at the same time
  - no road driven while closed
  - no tractor ran out of fuel
  - no plan rejected by the safety agent (would mean the planner proposed something unsafe)
  - SYNTERRA not worse than the FCFS baseline on priority-weighted on-time work

This is systematic test generation, not machine learning. Failures are recorded and the
clearest one (fewest jobs + events) is reported.
"""
import json, random
from .orchestrator import simulate
from . import metrics

TYPES = [("spray", "sprayer"), ("mow", "mower"), ("seed", "seeder")]


def generate(seed):
    r = random.Random(seed)
    n_fields = r.randint(5, 10)
    cells = r.sample([(x, y) for x in range(0, 10) for y in range(0, 10) if (x, y) != (0, 0)], n_fields)
    fields = [dict(id=f"F{i + 1}", x=x, y=y) for i, (x, y) in enumerate(cells)]
    implements = [dict(id="SP1", type="sprayer"), dict(id="MW1", type="mower"), dict(id="SD1", type="seeder")]
    if r.random() < 0.5:
        implements.append(dict(id="MW2", type="mower"))
    if r.random() < 0.3:
        implements.append(dict(id="SP2", type="sprayer"))
    n_tr = r.randint(2, 3)
    tractors = []
    for i in range(n_tr):
        caps = r.sample([t for t, _ in TYPES], r.randint(1, 3))
        if i == 0 and "spray" not in caps:
            caps.append("spray")
        tank = r.choice([180, 220, 260, 320])
        tractors.append(dict(id=f"T{i + 1}", capabilities=sorted(caps),
                             compatible_implements=sorted(dict(TYPES)[c] for c in caps),
                             tank_minutes=tank, battery_minutes=tank // 2, implement=None))
    n_jobs = r.randint(5, 10)
    jobs = []
    for i in range(n_jobs):
        typ, imp = r.choice(TYPES)
        work = r.choice([20, 30, 35, 40, 45, 50, 60])
        jobs.append(dict(id=f"J{i + 1}", type=typ, implement=imp, field=r.choice(fields)["id"],
                         priority=r.randint(1, 3), deadline=r.randint(120, 600), work_minutes=work))
    events = []
    t_b = r.randint(20, 200)
    events.append(dict(t=t_b, type="breakdown", tractor=r.choice(tractors)["id"]))
    for _ in range(r.randint(0, 2)):
        a, b = r.sample(["YARD"] + [f["id"] for f in fields], 2)
        events.append(dict(t=r.randint(10, 300), type=r.choice(["blocked_route", "obstacle_reported"]),
                           between=[a, b], actual=r.choice(["clear", "blocked"])))
    if r.random() < 0.6:
        typ, imp = r.choice(TYPES)
        events.append(dict(t=r.randint(30, 250), type="new_job",
                           job=dict(id=f"J{n_jobs + 1}", type=typ, implement=imp, field=r.choice(fields)["id"],
                                    priority=3, deadline=r.randint(250, 500), work_minutes=r.choice([20, 30, 40]))))
    if r.random() < 0.5:
        events.append(dict(t=r.randint(30, 250), type="fuel_drop", tractor=r.choice(tractors)["id"],
                           drop=r.choice([30, 60, 100])))
    if r.random() < 0.5:
        tid = r.choice(tractors)["id"]
        t0 = r.randint(30, 250)
        events.append(dict(t=t0, type="comm_lost", tractor=tid, actual=r.choice(["working", "broken"])))
        if r.random() < 0.7:
            events.append(dict(t=t0 + r.choice([5, 10, 30, 60]), type="comm_restored", tractor=tid))
    if r.random() < 0.4:
        events.append(dict(t=r.randint(20, 250), type="delay", tractor=r.choice(tractors)["id"],
                           minutes=r.choice([5, 10, 20])))
    if r.random() < 0.3:
        events.append(dict(t=r.randint(20, 250), type="priority_change", job=r.choice(jobs)["id"], priority=3))
    narrow = []
    if r.random() < 0.5:
        for _ in range(r.randint(1, 3)):
            narrow.append(r.sample(["YARD"] + [f["id"] for f in fields], 2))
    return dict(name=f"adversarial seed {seed}", start_clock="06:00", yard=[0, 0], minutes_per_cell=4,
                swap_minutes=10, refuel_minutes=15, recharge_minutes=20, km_per_cell=1, recovery_minutes=30,
                fields=fields, implements=implements, tractors=tractors, jobs=jobs,
                events=sorted(events, key=lambda e: e["t"]), narrow_roads=narrow)


def check(sc, policies=("smart", "fcfs", "nearest")):
    failures, res = [], {}
    for p in policies:
        try:
            m = simulate(sc, p, frames=False)
        except Exception as ex:  # a crash is a failure we want to see
            failures.append(dict(policy=p, kind="crash", detail=repr(ex)))
            continue
        r = metrics.compute(m)
        res[p] = r
        for a in metrics.audit(m):
            failures.append(dict(policy=p, kind="audit", detail=a))
        if r["plans_rejected"]:
            failures.append(dict(policy=p, kind="unsafe plan proposed",
                                 detail=[e["violations"] for e in m.log if e["event"] == "PLAN_REJECTED"][:2]))
        # finished work never lost: every section marked done stays done and has an owner
        for j in m.jobs.values():
            for s in j["segments"]:
                if s["status"] == "done" and s["done_at"] is None:
                    failures.append(dict(policy=p, kind="lost work", detail=s["id"]))
    if "smart" in res and "fcfs" in res:
        if res["smart"]["priority_weighted_on_time"] < res["fcfs"]["priority_weighted_on_time"]:
            failures.append(dict(policy="smart", kind="worse than baseline",
                                 detail=f"on-time priority {res['smart']['priority_weighted_on_time']} < "
                                        f"{res['fcfs']['priority_weighted_on_time']} (FCFS)"))
    return failures, res


def campaign(n=40, start_seed=1):
    report = dict(scenarios=n, failures=[], totals={})
    agg = {}
    for seed in range(start_seed, start_seed + n):
        sc = generate(seed)
        fails, res = check(sc)
        for f in fails:
            report["failures"].append(dict(seed=seed, jobs=len(sc["jobs"]), events=len(sc["events"]), **f))
        for p, r in res.items():
            a = agg.setdefault(p, dict(on_time=0, pw_on_time=0, completed=0, travel=0.0, violations=0, runs=0))
            a["on_time"] += r["jobs_on_time"]; a["pw_on_time"] += r["priority_weighted_on_time"]
            a["completed"] += r["jobs_completed"]; a["travel"] += r["travel_minutes"]
            a["violations"] += r["audit_violations"]; a["runs"] += 1
    report["totals"] = agg
    if report["failures"]:
        report["clearest_failure"] = min(report["failures"], key=lambda f: (f["jobs"] + f["events"], f["seed"]))
        report["clearest_failure_scenario"] = generate(report["clearest_failure"]["seed"])
    wins = sum(1 for seed in range(start_seed, start_seed + n)
               if not any(f["seed"] == seed and f["kind"] == "worse than baseline" for f in report["failures"]))
    report["smart_not_worse_than_fcfs"] = f"{wins}/{n}"
    return report


if __name__ == "__main__":
    import sys
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    rep = campaign(n)
    print(json.dumps({k: v for k, v in rep.items() if k != "clearest_failure_scenario"}, indent=2)[:4000])
