"""Efficient algorithms verified against brute force, the cuOpt adapter, and one check per
safety rule. Run:  python -m pytest -q tests"""
import heapq, itertools, os, random, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from test_coordinator import fresh, run_to, STRESS  # noqa: E402
from fleet.memory import Memory, COMPLETED  # noqa: E402
from fleet import navigation, safety  # noqa: E402
from fleet import orchestrator as orch  # noqa: E402
from fleet.planner import make_plan, _views  # noqa: E402


# ---------------- Hungarian ----------------
def test_hungarian_matches_brute_force():
    from fleet.assignment import hungarian, assignment_cost, INF
    rnd = random.Random(11)
    for _ in range(400):
        n, m = rnd.randint(1, 5), rnd.randint(1, 5)
        C = [[INF if rnd.random() < 0.2 else rnd.randint(0, 40) for _ in range(m)] for _ in range(n)]
        best = None
        for size in range(min(n, m), -1, -1):
            for rs in itertools.combinations(range(n), size):
                for cs in itertools.permutations(range(m), size):
                    if all(C[r][c] != INF for r, c in zip(rs, cs)):
                        k = (-size, sum(C[r][c] for r, c in zip(rs, cs)))
                        best = k if best is None or k < best else best
            if best is not None and best[0] == -size:
                break
        got = hungarian(C)
        assert (-len(got), assignment_cost(C, got)) == best
        assert len({r for r, _ in got}) == len(got) and len({c for _, c in got}) == len(got)


def test_hungarian_candidate_in_search():
    m = fresh()
    make_plan(m, "smart")
    assert "hungarian" in m.search_info["best_key_by_origin"]


# ---------------- exact solver ----------------
def test_branch_and_bound_equals_exhaustive():
    from evaluate import small_instance
    from fleet import planner
    for seed in range(1, 9):
        sc = small_instance(seed)
        sc["jobs"] = sc["jobs"][:6]
        a = Memory(sc); navigation.rebuild(a)
        b = Memory(sc); navigation.rebuild(b)
        assert list(planner.exact_exhaustive(a)[1]) == list(planner.exact_best(b)[1])


def test_synterra_reaches_exact_optimum_on_goals():
    from evaluate import small_instance
    from fleet import planner
    for seed in (13, 28, 32):          # the three farms that were missed before the upgrades
        sc = small_instance(seed)
        a = Memory(sc); navigation.rebuild(a)
        _, ek, _ = planner.exact_best(a)
        b = Memory(sc); navigation.rebuild(b)
        tasks, left, _, _ = planner._smart(b)
        assert list(planner._key(dict(tasks=tasks, left=left, cost=0.0), [])[:3]) == list(ek[:3])


# ---------------- k-Vac ----------------
def test_kvac_pure_python_equals_numpy():
    from fleet import kvac
    rnd = random.Random(9)
    for _ in range(15):
        n, k = rnd.randint(3, 7), rnd.randint(1, 3)
        pts = [(rnd.randint(0, 9), rnd.randint(0, 9)) for _ in range(n)]
        D = [[abs(a[0] - b[0]) + abs(a[1] - b[1]) for b in pts] for a in pts]
        st = [rnd.randrange(n) for _ in range(k)]
        rq = [(rnd.randrange(n), sorted(rnd.sample(range(k), rnd.randint(1, k)))) for _ in range(5)]
        a, b = kvac._wfa_run_py(D, st, rq), kvac._wfa_run_np(D, st, rq)
        assert a[0] == b[0] and abs(a[1] - b[1]) < 1e-6


# ---------------- shortest paths ----------------
def test_shortest_paths_match_dijkstra():
    m = orch.new_run(STRESS, "smart")
    run_to(m, 200)
    pts = navigation.points(m)
    closed = navigation.avoid(m)
    for src in pts:
        dist = {n: float("inf") for n in pts}
        dist[src] = 0
        pq = [(0, src)]
        while pq:
            d, u = heapq.heappop(pq)
            if d > dist[u]:
                continue
            for v in pts:
                if v != u and frozenset((u, v)) not in closed:
                    nd = d + navigation.edge_minutes(m, u, v)
                    if nd < dist[v]:
                        dist[v] = nd
                        heapq.heappush(pq, (nd, v))
        for v in pts:
            assert m.dist[src][v] == dist[v]
            if dist[v] < float("inf") and v != src:
                p = navigation.path(m, src, v)
                assert sum(navigation.edge_minutes(m, a, b) for a, b in zip(p, p[1:])) == dist[v]
                assert not set(navigation.edges_of(p)) & closed


# ---------------- cuOpt adapter (stand-in, since cuOpt is Linux-only) ----------------
class _FakeSeries(list):
    pass


class _FakeCudf:
    Series = _FakeSeries

    @staticmethod
    def DataFrame(x):
        return x


class _FakeRouting:
    last = None

    class DataModel:
        def __init__(self, n_loc, n_veh, n_ord):
            self.n = (n_loc, n_veh, n_ord)
            self.match = {}
            self.calls = {}
            _FakeRouting.last = self

        def __getattr__(self, name):
            def rec(*args):
                self.calls[name] = args
            return rec

        def add_vehicle_order_match(self, v, orders):
            self.match[v] = list(orders)

    class SolverSettings:
        def set_time_limit(self, t):
            self.t = t

    class _Sol:
        def __init__(self, dm):
            self.dm = dm

        def get_status(self):
            return 0

        def get_route(self):
            trucks, route, types, stamps = [0], [0], ["Depot"], [0]
            for o in range(self.dm.n[2]):
                v = next((v for v, os_ in sorted(self.dm.match.items()) if o in os_), None)
                if v is not None:
                    trucks.append(v); route.append(o); types.append("Delivery"); stamps.append(10 * o + 1)
            return {"truck_id": trucks, "route": route, "type": types, "arrival_stamp": stamps}

    @staticmethod
    def Solve(dm, ss):
        return _FakeRouting._Sol(dm)


def test_cuopt_adapter_builds_model_and_respects_capabilities():
    from fleet import cuopt_solver
    from fleet.planner import plannable_jobs, _seed_from_choices
    m = fresh()
    tvs, jobs = _views(m), plannable_jobs(m)
    md = cuopt_solver.build_model(m, tvs, jobs)
    for o, j in zip(md["orders"], jobs):
        assert o["latest"] == j["deadline"] - j["remaining"]
        assert o["prize"] == 1000 * j["priority"]
    for v in md["vehicles"]:
        for o in v["orders"]:
            assert jobs[o]["type"] in m.tractors[v["tractor"]]["capabilities"]
    pairs = cuopt_solver.solve(m, tvs, jobs, modules=(_FakeCudf, _FakeRouting))
    dm = _FakeRouting.last
    assert dm.n == (len(md["nodes"]), len(tvs), len(jobs))
    for name in ("add_cost_matrix", "set_order_locations", "set_order_time_windows",
                 "set_order_prizes", "set_vehicle_locations", "set_order_service_times"):
        assert name in dm.calls
    assert len(pairs) == len(jobs)
    for jid, tid in pairs:
        assert m.jobs[jid]["type"] in m.tractors[tid]["capabilities"]
    s = _seed_from_choices(m, pairs, jobs, {}, "cuopt")
    unassigned = {jid: "left out by this candidate" for jid in s["left"]}
    assert not [v for v in safety.validate(m, s["tasks"], unassigned) if safety.is_hard(v)]
    assert len(s["tasks"]) >= len(jobs) - 1


def test_planner_works_without_cuopt_installed():
    from fleet import cuopt_solver
    m = fresh()
    tasks, _, _ = make_plan(m, "smart")
    assert len(tasks) == 10
    if not cuopt_solver.available():
        assert "cuopt" not in m.search_info["best_key_by_origin"]


# ---------------- safety agent: every rule is enforced ----------------
def _plan():
    m = fresh()
    tasks, un, _ = make_plan(m, "smart")
    return m, tasks, un


def _hard(m, tasks, un):
    return [v for v in safety.validate(m, tasks, un) if safety.is_hard(v)]


def test_safety_accepts_clean_plan():
    m, tasks, un = _plan()
    assert _hard(m, tasks, un) == []


def _spray(tt):
    return next(t for t in tt if t["type"] == "spray")


def test_safety_rule_capability():
    m, tt, un = _plan()
    assert _hard(m, [dict(_spray(tt), tractor="T2")], un)


def test_safety_rule_implement():
    m, tt, un = _plan()
    assert _hard(m, [dict(_spray(tt), unit="MW1")], un)


def test_safety_rule_remaining_work_only():
    m, tt, un = _plan()
    s = _spray(tt)
    assert _hard(m, [dict(s, work=s["work"] - 5)], un)


def test_safety_rule_no_double_assignment():
    m, tt, un = _plan()
    s = _spray(tt)
    assert _hard(m, [s, dict(s)], un)


def test_safety_rule_broken_tractor_gets_nothing():
    m, tt, un = _plan()
    m.tractors[tt[0]["tractor"]]["status"] = "broken"
    assert _hard(m, tt, un)


def test_safety_rule_closed_road():
    m, tt, un = _plan()
    leg = next(l for t in tt for l in t["legs"] if len(l) > 1)
    m.blocked.add(frozenset(leg[:2]))
    assert _hard(m, tt, un)


def test_safety_rule_finished_job_not_reassigned():
    m, tt, un = _plan()
    m.jobs[tt[0]["job"]]["status"] = COMPLETED
    assert _hard(m, tt, un)


def test_safety_rule_every_job_planned_or_explained():
    m, tt, un = _plan()
    assert _hard(m, tt[1:], un)


def test_safety_rule_one_tractor_per_field():
    m, tt, un = _plan()
    a = tt[0]
    b = next(t for t in tt if t["tractor"] != a["tractor"])
    assert _hard(m, [a, dict(b, field=a["field"], start=a["start"], end=a["end"])], un)


def test_safety_rule_battery():
    m, tt, un = _plan()
    m.tractors[tt[0]["tractor"]]["battery"] = 1
    assert _hard(m, tt, un)


def test_safety_rule_fuel():
    m, tt, un = _plan()
    m.tractors[tt[0]["tractor"]]["fuel"] = 1
    assert _hard(m, tt, un)


def test_safety_rule_implement_single_holder():
    m, tt, un = _plan()
    a = next(t for t in tt if t["swap"])
    b = next(t for t in tt if t["tractor"] != a["tractor"])
    forged = dict(b, unit=a["unit"], swap=True, take_at=a["take_at"], yard_at=a["take_at"], prev_unit=None)
    assert _hard(m, [a, forged], un)


def test_delay_chain_pushes_everyone_queued_behind():
    from fleet.adversarial import generate
    from fleet import metrics
    for pol in ("smart", "fcfs", "nearest"):
        m = orch.simulate(generate(231), pol, frames=False)   # delay -> T2 behind T1 -> T3 behind T2
        assert metrics.audit(m) == [], pol
        assert not any(e["event"] == "PLAN_REJECTED" for e in m.log), pol
