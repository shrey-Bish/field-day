"""NVIDIA cuOpt adapter (GPU vehicle-routing solver), used as one candidate schedule.

cuOpt is published for Linux only (x86_64 / aarch64 wheels, CUDA 12 or 13). On Windows run it
inside WSL2 (the GPU is passed through):

    wsl --install                       # once, as administrator, then reboot
    # inside Ubuntu:
    pip install --extra-index-url=https://pypi.nvidia.com cuopt-cu12   # or cuopt-cu13
    python run_demo.py                  # cuOpt is picked up automatically

If cuOpt is not installed, available() is False and SYNTERRA uses its own solvers only.

How the farm maps onto cuOpt's routing model:
  locations   yard + field entrances; cost and transit-time matrices = shortest road minutes
  vehicles    working tractors; start where they are, end at the yard; time window from when
              each tractor is free
  orders      plannable jobs at their field; service time = remaining work minutes
  windows     work must START by (deadline - work) -> finishing on time is a hard window
  prizes      1000 x priority: cuOpt may drop an order it cannot fit, and maximises prize first,
              i.e. "most priority on time" like our objective
  matching    add_vehicle_order_match: a tractor only gets jobs it is capable of
Not modelled inside cuOpt (handled when the route is turned into real trips by build_task):
implement swaps at the yard, refuel/recharge stops, single-lane roads, field queues. cuOpt's
output is therefore a CANDIDATE: it is rebuilt exactly, scored with the same objective as every
other candidate, and checked by the safety agent before anything is saved.
"""
from .navigation import YARD, INF

BIG = 10 ** 7
_STATUS = {"checked": False, "ok": False, "error": None}


def available():
    if not _STATUS["checked"]:
        _STATUS["checked"] = True
        try:
            import cudf  # noqa: F401
            from cuopt import routing  # noqa: F401
            _STATUS["ok"] = True
        except Exception as ex:      # not installed / no GPU / not Linux
            _STATUS["ok"], _STATUS["error"] = False, repr(ex)
    return _STATUS["ok"]


def status():
    available()
    return dict(_STATUS)


def build_model(mem, tvs, jobs):
    """Plain-Python description of the cuOpt model (also used by the tests)."""
    nodes = [YARD] + sorted(mem.fields)
    idx = {n: i for i, n in enumerate(nodes)}
    matrix = [[(mem.dist[a][b] if mem.dist[a][b] != INF else BIG) for b in nodes] for a in nodes]
    tids = sorted(tvs)
    horizon = int(max([j["deadline"] for j in jobs] + [mem.now]) + 24 * 60)
    orders = []
    for j in jobs:
        latest_start = j["deadline"] - j["remaining"]
        orders.append(dict(job=j["id"], location=idx[j["field"]], service=int(round(j["remaining"])),
                           earliest=int(mem.now), latest=int(max(mem.now, latest_start)),
                           prize=1000 * j["priority"], on_time_possible=latest_start >= mem.now))
    vehicles = []
    for v, tid in enumerate(tids):
        tv = tvs[tid]
        able = [o for o, j in enumerate(jobs) if j["type"] in tv["caps"] and j["implement"] in tv["compat"]]
        vehicles.append(dict(tractor=tid, start=idx[tv["pos"]], end=idx[YARD],
                             earliest=int(tv["t"]), latest=horizon, orders=able))
    return dict(nodes=nodes, matrix=matrix, orders=orders, vehicles=vehicles, horizon=horizon)


def solve(mem, tvs, jobs, time_limit=2.0, modules=None):
    """Run cuOpt. Returns [(job_id, tractor_id)] in service order, or None.
    `modules` = (cudf, routing) lets the tests inject a stand-in."""
    if not jobs or not tvs:
        return []
    if modules is None:
        if not available():
            return None
        import cudf
        from cuopt import routing
    else:
        cudf, routing = modules
    md = build_model(mem, tvs, jobs)
    n_loc, n_veh, n_ord = len(md["nodes"]), len(md["vehicles"]), len(md["orders"])
    dm = routing.DataModel(n_loc, n_veh, n_ord)
    dm.add_cost_matrix(cudf.DataFrame(md["matrix"]))
    dm.add_transit_time_matrix(cudf.DataFrame(md["matrix"]))
    dm.set_order_locations(cudf.Series([o["location"] for o in md["orders"]]))
    dm.set_order_service_times(cudf.Series([o["service"] for o in md["orders"]]))
    dm.set_order_time_windows(cudf.Series([o["earliest"] for o in md["orders"]]),
                              cudf.Series([o["latest"] for o in md["orders"]]))
    dm.set_order_prizes(cudf.Series([o["prize"] for o in md["orders"]]))
    dm.set_vehicle_locations(cudf.Series([v["start"] for v in md["vehicles"]]),
                             cudf.Series([v["end"] for v in md["vehicles"]]))
    dm.set_vehicle_time_windows(cudf.Series([v["earliest"] for v in md["vehicles"]]),
                                cudf.Series([v["latest"] for v in md["vehicles"]]))
    for v, veh in enumerate(md["vehicles"]):
        if veh["orders"]:
            dm.add_vehicle_order_match(v, cudf.Series(veh["orders"]))
    ss = routing.SolverSettings()
    ss.set_time_limit(time_limit)
    sol = routing.Solve(dm, ss)
    if sol.get_status() != 0:
        return None
    return parse_route(sol.get_route(), md, jobs)


def parse_route(routes, md, jobs):
    df = routes.to_pandas() if hasattr(routes, "to_pandas") else routes
    col = lambda name: list(df[name].tolist() if hasattr(df[name], "tolist") else df[name])
    trucks, route, types, stamps = col("truck_id"), col("route"), col("type"), col("arrival_stamp")
    rows = []
    for tr, r, ty, st in zip(trucks, route, types, stamps):
        if str(ty).lower() == "depot":
            continue
        r = int(r)
        if not 0 <= r < len(jobs):
            continue
        rows.append((float(st), int(tr), r))
    rows.sort()
    seen, pairs = set(), []
    for _, tr, r in rows:
        if r in seen:
            continue
        seen.add(r)
        pairs.append((jobs[r]["id"], md["vehicles"][tr]["tractor"]))
    return pairs
