"""Automated checks for the coordinator. Run:  python -m pytest -q tests"""
import copy, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fleet.memory import Memory, COMPLETED
from fleet import navigation, safety, metrics, explainer
from fleet import orchestrator as orch
from fleet.planner import make_plan, build_task, _views
from fleet.events import EventBus, from_scenario

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCEN = json.load(open(os.path.join(ROOT, "scenario.json")))
STRESS = json.load(open(os.path.join(ROOT, "scenarios", "stress.json")))


def fresh(sc=SCEN, events=False):
    sc = copy.deepcopy(sc)
    if not events:
        sc["events"] = []
    m = Memory(sc)
    navigation.rebuild(m)
    return m


def run_to(m, t):
    """Process scenario events up to and including minute t."""
    while m.bus.peek_time() <= t:
        msg = m.bus.pop()
        if msg:
            orch.handle(m, msg)
    orch.advance(m, t)


# 1
def test_tractor_without_sprayer_cannot_get_spray_job():
    m = fresh()
    tasks, _, _ = make_plan(m, "smart")
    for t in tasks:
        if t["type"] == "spray":
            assert t["tractor"] != "T2"
    # and the safety agent rejects it if someone tries
    tv = _views(m)["T2"]
    assert build_task(m, tv, m.jobs["J2"], copy.deepcopy(m.implements), {}) is None
    forged = dict(tasks[0], tractor="T2") if tasks[0]["type"] == "spray" else \
        dict(next(t for t in tasks if t["type"] == "spray"), tractor="T2")
    assert any("cannot do spray" in v for v in safety.validate(m, [forged], {}))


# 2
def test_insufficient_energy_is_infeasible():
    m = fresh()
    m.tractors["T1"].update(pos="F8", fuel=5)      # far from the yard with almost no fuel
    tv = _views(m)["T1"]
    task = build_task(m, tv, m.jobs["J2"], copy.deepcopy(m.implements), {})
    assert task is None  # cannot even drive to the yard to refuel
    m2 = fresh()
    tasks, _, _ = make_plan(m2, "smart")
    t = tasks[0]
    m2.tractors[t["tractor"]]["fuel"] = 1
    assert any("fuel" in v for v in safety.validate(m2, [t], {}))


# 3
def test_no_segment_has_two_owners():
    for sc in (SCEN, STRESS):
        m = orch.simulate(sc, "smart", frames=False)
        for e in m.log:
            assert e["event"] != "PLAN_REJECTED"
    m = fresh()
    tasks, un, _ = make_plan(m, "smart")
    dup = tasks + [dict(tasks[0], tractor=tasks[1]["tractor"])]
    assert any("two owners" in v or "given twice" in v for v in safety.validate(m, dup, un))


# 4 + 5
def test_breakdown_keeps_finished_sections_and_replacement_gets_only_the_rest():
    # break the first tractor to start work, 22 minutes into its job (mid-section)
    first = min(orch.new_run(dict(SCEN, events=[]), "smart").plan, key=lambda t: t["start"])
    t_fail = first["start"] + 22
    sc = dict(SCEN, events=[dict(t=t_fail, type="breakdown", tractor=first["tractor"])])
    m = orch.new_run(sc, "smart")
    run_to(m, t_fail)
    rec = [e for e in m.log if e["event"] == "WORK_PRESERVED"]
    assert rec, "the breakdown must interrupt a job"
    info = rec[-1]
    job = m.jobs[info["job"]]
    done = [s for s in job["segments"] if s["status"] == "done"]
    assert len(done) == info["done_segments"] > 0
    new_task = next(t for t in m.plan if t["job"] == info["job"])
    assert new_task["tractor"] != first["tractor"]
    assert new_task["work"] == job["work_minutes"] - sum(s["minutes"] for s in done)
    assert set(new_task["segments"]) == {s["id"] for s in job["segments"] if s["status"] != "done"}
    orch.run(m, frames=False)
    assert all(s["status"] == "done" for s in done)       # still done at the end
    assert m.jobs[info["job"]]["status"] == COMPLETED


# 6
def test_blocked_road_never_in_approved_route():
    m = orch.new_run(SCEN, "smart")
    run_to(m, 150)
    closed = frozenset(("F4", "F7"))
    assert closed in m.blocked
    for t in m.plan:
        for leg in t["legs"]:
            assert closed not in navigation.edges_of(leg)
    orch.run(m, frames=False)
    assert metrics.audit(m) == []


# 7
def test_replanning_preserves_completed_jobs():
    m = orch.new_run(SCEN, "smart")
    run_to(m, 140)
    finished = {j for j, job in m.jobs.items() if job["status"] == COMPLETED}
    assert finished
    orch.replan(m, dict(type="manual", t=m.now))
    assert not finished & {t["job"] for t in m.plan}
    assert all(m.jobs[j]["status"] == COMPLETED for j in finished)


# 8
def test_duplicate_event_delivery_does_not_duplicate_work():
    bus = EventBus()
    msgs = from_scenario(SCEN["events"])
    for msg in msgs + msgs:
        bus.publish(dict(msg))
    got = []
    while not bus.empty():
        x = bus.pop()
        if x:
            got.append(x["id"])
    assert sorted(got) == sorted(m["id"] for m in msgs)
    dup = orch.simulate(SCEN, "smart", extra_events=list(SCEN["events"]), frames=False)
    one = orch.simulate(SCEN, "smart", frames=False)
    assert metrics.compute(dup)["work_minutes"] == metrics.compute(one)["work_minutes"]


# 9
def test_repeated_failure_events_do_not_create_repeated_assignments():
    extra = [dict(t=96, type="breakdown", tractor="T2"), dict(t=97, type="breakdown", tractor="T2")]
    m = orch.simulate(SCEN, "smart", extra_events=extra, frames=False)
    assert sum(e["event"] == "TRACTOR_FAILED" for e in m.log) == 1
    assert sum(e["event"] == "IGNORED" for e in m.log) == 2
    m0 = orch.simulate(SCEN, "smart", frames=False)
    assert metrics.compute(m)["work_minutes"] == metrics.compute(m0)["work_minutes"]


# 10
def test_unreachable_job_reported_infeasible():
    m = fresh()
    for other in ["YARD"] + [f for f in m.fields if f != "F8"]:
        m.blocked.add(frozenset((other, "F8")))
    navigation.rebuild(m)
    tasks, un, _ = make_plan(m, "smart")
    assert "J1" in un and "cannot be reached" in un["J1"]
    assert "J1" not in {t["job"] for t in tasks}
    assert not [v for v in safety.validate(m, tasks, un) if safety.is_hard(v)]


# 11
def test_communication_loss_does_not_release_ownership():
    sc = copy.deepcopy(SCEN)
    sc["events"] = [dict(t=60, type="comm_lost", tractor="T1", actual="working")]
    m = orch.new_run(sc, "smart")
    run_to(m, 60)
    task = m.active.get("T1")
    assert task is not None
    owned = [s for s in m.jobs[task["job"]]["segments"] if s["owner"] == task["assignment"]]
    run_to(m, 60 + m.lease_minutes - 1)            # before the lease runs out
    assert "T1" in m.active                         # still its work
    assert task["job"] not in {t["job"] for t in m.plan}
    assert all(s["owner"] == task["assignment"] or s["status"] == "done" for s in owned)
    orch.run(m, frames=False)
    assert metrics.audit(m) == []


# 12
def test_deadlines_and_finish_times_consistent():
    m = fresh()
    tasks, _, _ = make_plan(m, "smart")
    for t in tasks:
        work = [p for p in t["phases"] if p["kind"] == "work"]
        assert work[0]["t0"] == t["start"] and work[-1]["t1"] == t["end"]
        assert t["end"] - t["start"] == t["work"]
        assert t["late"] == (t["end"] > t["deadline"])
        for a, b in zip(t["phases"], t["phases"][1:]):
            assert abs(a["t1"] - b["t0"]) < 1e-9          # no gaps, no teleporting
    mm = orch.simulate(SCEN, "smart", frames=False)
    for j in mm.jobs.values():
        h = [x for x in mm.history if x["job"] == j["id"]]
        assert j["completed_at"] == max(x["actual_end"] for x in h)


# 13
def test_same_seeded_scenario_is_reproducible():
    a = orch.simulate(STRESS, "smart", frames=False)
    b = orch.simulate(STRESS, "smart", frames=False)
    strip = lambda m: [(e["t"], e["event"], e.get("job"), e.get("tractor")) for e in m.log]
    assert strip(a) == strip(b)
    ma, mb = metrics.compute(a), metrics.compute(b)
    ma.pop("planning_ms_total"); ma.pop("planning_ms_max"); mb.pop("planning_ms_total"); mb.pop("planning_ms_max")
    assert ma == mb
    from fleet.adversarial import generate
    assert generate(7) == generate(7)


# 14
def test_reset_restores_initial_scenario():
    before = copy.deepcopy(SCEN)
    orch.simulate(SCEN, "smart", frames=False)
    assert SCEN == before                  # the scenario itself is never modified
    m1 = orch.new_run(SCEN, "smart")
    m2 = orch.new_run(SCEN, "smart")
    run_to(m1, 300)
    assert [(t["job"], t["tractor"]) for t in m2.plan] == \
        [(t["job"], t["tractor"]) for t in orch.new_run(SCEN, "smart").plan]
    assert all(j["status"] != COMPLETED for j in m2.jobs.values())


# 15
def test_benchmark_values_come_from_executed_events():
    m = orch.simulate(SCEN, "smart", frames=False)
    r = metrics.compute(m)
    assert r["travel_minutes"] == sum(h["travel_done"] for h in m.history)
    assert r["jobs_completed"] == sum(1 for e in m.log if e["event"] == "JOB_COMPLETED")
    seg_events = sum(1 for e in m.log if e["event"] == "SEGMENT_COMPLETED")
    assert seg_events >= sum(1 for j in m.jobs.values() for s in j["segments"]
                             if s["done_by"] and "reported late" not in s["done_by"]) - 50
    # tampering with history changes the numbers -> they are not hard-coded
    m.history[0]["travel_done"] += 100
    assert metrics.compute(m)["travel_minutes"] == r["travel_minutes"] + 100


# extra checks
def test_safety_reports_baseline_late_job():
    m = fresh()
    tasks, un, _ = make_plan(m, "fcfs")
    v = safety.validate(m, tasks, un)
    assert any("J7" in x and x.startswith("[deadline]") for x in v)
    m2 = fresh()
    t2, u2, _ = make_plan(m2, "smart")
    assert safety.validate(m2, t2, u2) == []


def test_smart_beats_baseline_on_demo_scenario():
    s = metrics.compute(orch.simulate(SCEN, "smart", frames=False))
    b = metrics.compute(orch.simulate(SCEN, "fcfs", frames=False))
    assert s["jobs_on_time"] >= b["jobs_on_time"]
    assert s["travel_minutes"] < b["travel_minutes"]
    assert s["audit_violations"] == 0


def test_stress_scenario_all_event_types_clean():
    for pol in ("smart", "fcfs", "nearest"):
        m = orch.simulate(STRESS, pol, frames=False)
        assert metrics.audit(m) == [], pol
        assert not any(e["event"] == "PLAN_REJECTED" for e in m.log), pol


def test_why_answer_is_grounded():
    m = orch.new_run(SCEN, "smart")
    ans = explainer.why(m, "J2")
    assert "T1" in ans or "T3" in ans
    assert "cannot do this job type" in ans    # T2 cannot spray, and the answer says so


def test_single_lane_road_never_shared():
    m = orch.simulate(STRESS, "smart", frames=False)
    spans = {}
    for h in m.history:
        for k, a, b, tid in h.get("reservations", ()):
            spans.setdefault(k, []).append((a, min(b, h["actual_end"] + 1e9), tid))
    for k, ss in spans.items():
        ss.sort()
        for x, y in zip(ss, ss[1:]):
            assert not (y[0] < x[1] - 1e-6 and x[2] != y[2]), (k, x, y)


# ---------------- research algorithms ----------------
def test_kvac_work_function_equals_offline_optimum():
    """Defining property of the WFA recursion: min_C w_n(C) = offline optimal movement cost."""
    import random
    from fleet.kvac import wfa_run, offline_opt
    rnd = random.Random(3)
    for _ in range(25):
        n, k = rnd.randint(3, 6), rnd.randint(1, 3)
        pts = [(rnd.randint(0, 9), rnd.randint(0, 9)) for _ in range(n)]
        D = [[abs(a[0] - b[0]) + abs(a[1] - b[1]) for b in pts] for a in pts]
        start = [rnd.randrange(n) for _ in range(k)]
        reqs = []
        for _ in range(rnd.randint(1, 6)):
            able = sorted(rnd.sample(range(k), rnd.randint(1, k)))
            reqs.append((rnd.randrange(n), able))
        chosen, wmin = wfa_run(D, start, reqs)
        assert abs(wmin - offline_opt(D, start, reqs)) < 1e-6
        for (r, able), c in zip(reqs, chosen):
            assert c in able            # a request is only served by a compatible tractor


def test_kvac_candidate_is_used_in_search():
    m = fresh()
    make_plan(m, "smart")
    assert "kvac-wfa" in m.search_info["best_key_by_origin"]


def test_beam_matches_exact_optimum_on_small_farms():
    from evaluate import small_instance
    from fleet import planner
    hits = 0
    for seed in range(1, 11):
        sc = small_instance(seed)
        m = Memory(sc); navigation.rebuild(m)
        _, ek, _ = planner.exact_best(m)
        m2 = Memory(sc); navigation.rebuild(m2)
        tasks, left, _, _ = planner._smart(m2)
        k = planner._key(dict(tasks=tasks, left=left, cost=0.0), [])
        assert k[:3] >= ek[:3]             # nothing can beat the exact optimum
        hits += list(k[:3]) == list(ek[:3])
    assert hits >= 9                        # measured: 29/30 on goals 1-3 (see out/optimality.md)


def test_local_search_never_makes_plan_worse():
    from fleet import planner
    m = fresh()
    jobs, finals, prev, _ = planner.candidates(m)
    for s0 in finals[:5]:
        p, _ = planner._local_search(m, s0, jobs, prev)
        assert planner._key(p, jobs) <= planner._key(s0, jobs)


def test_redirect_only_moves_tractors_that_had_not_started_work():
    from fleet.adversarial import generate
    m = orch.simulate(generate(35), "smart", frames=False)
    red = [h for h in m.history if h["interrupted"] == "redirected by new plan"]
    assert red, "seed 35 needs a redirect for the urgent job"
    assert all(h["work_done"] == 0 for h in red)
    assert metrics.audit(m) == []


def test_cancelled_implement_handover_never_double_holds():
    from fleet.adversarial import generate
    for seed in (123, 126, 160):
        for pol in ("smart", "fcfs", "nearest"):
            m = orch.simulate(generate(seed), pol, frames=False)
            assert not any(e["event"] == "PLAN_REJECTED" for e in m.log), (seed, pol)
            assert metrics.audit(m) == [], (seed, pol)


def test_delay_pushes_downstream_work_and_keeps_accounting_exact():
    sc = copy.deepcopy(SCEN)
    sc["events"] = [dict(t=30, type="delay", tractor="T1", minutes=20)]
    m = orch.simulate(sc, "smart", frames=False)
    assert any(e["event"] == "TRACTOR_DELAYED" for e in m.log)
    for j in m.jobs.values():
        assert sum(h["work_done"] for h in m.history if h["job"] == j["id"]) == j["work_minutes"]
    assert metrics.audit(m) == []


def test_adversarial_campaign_has_no_rule_violations():
    from fleet.adversarial import campaign
    rep = campaign(25)
    assert not [f for f in rep["failures"] if f["kind"] != "worse than baseline"]
    t = rep["totals"]
    assert t["smart"]["pw_on_time"] > t["fcfs"]["pw_on_time"]
    assert t["smart"]["pw_on_time"] > t["nearest"]["pw_on_time"]
