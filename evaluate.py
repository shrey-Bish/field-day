"""Accuracy evaluation of the coordinator. Writes:

  out/optimality.md / .json   how close SYNTERRA's search gets to the TRUE optimum (exhaustive
                              search) on small farms, and which candidate source won
  out/adversarial.md / .json  seeded adversarial campaign: invariant checks on what actually ran,
                              and SYNTERRA vs both baselines on identical scenarios

Usage:  python evaluate.py [--small 30] [--seeds 200]
"""
import argparse, json, os, time
from fleet.memory import Memory
from fleet import navigation
from fleet.adversarial import generate, campaign
from fleet import planner


def small_instance(seed):
    sc = generate(seed)
    sc["events"] = []
    sc["jobs"] = sc["jobs"][: 4 + seed % 5]          # 4 to 8 jobs: exact branch-and-bound is feasible
    sc["name"] = f"small seed {seed}"
    return sc


def optimality(n_req):
    rows = []
    for seed in range(1, n_req + 1):
        sc = small_instance(seed)
        m = Memory(sc); navigation.rebuild(m)
        t0 = time.perf_counter()
        try:
            _, ek, ex = planner.exact_best(m)
        except RuntimeError:
            continue                      # too big to prove optimal: left out, never guessed
        t_exact = time.perf_counter() - t0
        m2 = Memory(sc); navigation.rebuild(m2)
        t0 = time.perf_counter()
        jobs, finals, _, _ = planner.candidates(m2)
        tasks, left, info, _ = planner._smart(m2)
        t_beam = time.perf_counter() - t0
        chosen = dict(tasks=tasks, left=list(left), cost=sum(planner._task_cost(m2, x, {}) for x in tasks))
        finals.sort(key=lambda s: planner._key(s, jobs))
        bk = planner._key(chosen, jobs)
        by_origin = {}
        for s in finals:
            k = planner._key(s, jobs)
            o = s.get("origin", "beam")
            if o not in by_origin or k < by_origin[o]:
                by_origin[o] = k
        rows.append(dict(seed=seed, jobs=len(sc["jobs"]), tractors=len(sc["tractors"]),
                         exact_key=list(ek), synterra_key=list(bk),
                         goals_match=list(bk[:3]) == list(ek[:3]),
                         cost_gap_pct=round(100 * (bk[3] - ek[3]) / ek[3], 2) if ek[3] else 0.0,
                         exact_states=ex, exact_s=round(t_exact, 3), synterra_s=round(t_beam, 3),
                         origin_optimal={o: list(k[:3]) == list(ek[:3]) and abs(k[3] - ek[3]) < 1e-6
                                         for o, k in by_origin.items()},
                         origin_goal_optimal={o: list(k[:3]) == list(ek[:3]) for o, k in by_origin.items()}))
    n = len(rows)
    match = sum(r["goals_match"] for r in rows)
    exact_hit = sum(r["goals_match"] and abs(r["cost_gap_pct"]) < 1e-6 for r in rows)
    origins = sorted({o for r in rows for o in r["origin_goal_optimal"]})
    summary = dict(instances=n, attempted=n_req, goals_optimal=match, fully_optimal=exact_hit,
                   avg_cost_gap_pct=round(sum(r["cost_gap_pct"] for r in rows) / n, 3),
                   max_cost_gap_pct=max(r["cost_gap_pct"] for r in rows),
                   goal_optimal_rate_by_source={o: sum(r["origin_goal_optimal"].get(o, False) for r in rows)
                                                for o in origins})
    return summary, rows


def write(out, opt, rows, adv):
    os.makedirs(out, exist_ok=True)
    json.dump(dict(summary=opt, rows=rows), open(os.path.join(out, "optimality.json"), "w"), indent=2)
    L = ["# How close is SYNTERRA to the best possible plan?", "",
         f"{opt['instances']} seeded farms (4-8 jobs, 2-3 tractors, no events). For each, the TRUE optimum of the "
         "same objective was found by exact branch-and-bound (admissible bound, exact duplicate merging; cross-checked "
         "against plain exhaustive search in the tests) and compared with what SYNTERRA commits in normal operation.", "",
         "Objective, in strict order: (1) priority finished on time, (2) priority finished, (3) priority-weighted "
         "lateness, (4) cost (travel, idle, service stops, plan changes, early finish of priority work, deadline slack).", "",
         f"- Optimal on goals 1-3 (the work that matters): **{opt['goals_optimal']}/{opt['instances']}**",
         f"- Optimal on all four goals including cost: **{opt['fully_optimal']}/{opt['instances']}**",
         f"- Average cost gap to optimum: {opt['avg_cost_gap_pct']}% (worst {opt['max_cost_gap_pct']}%)", "",
         f"- Average time: SYNTERRA {sum(r['synterra_s'] for r in rows) / max(1, len(rows)):.2f} s per plan, exact solver "
         f"{sum(r['exact_s'] for r in rows) / max(1, len(rows)):.2f} s (exact is only used here, never in the live demo)", "",
         "How often each candidate source alone reached the optimum on goals 1-3:", "",
         "| Source | Optimal on goals 1-3 |", "|---|---|"]
    for o, c in sorted(opt["goal_optimal_rate_by_source"].items(), key=lambda x: -x[1]):
        L.append(f"| {o} | {c}/{opt['instances']} |")
    L += ["", "Note: the plan SYNTERRA finally commits may trade a little cost for robustness to a tractor failure "
          "(hindsight fragility check); goals 1-3 are never traded.", ""]
    open(os.path.join(out, "optimality.md"), "w", encoding="utf-8").write("\n".join(L) + "\n")

    json.dump({k: v for k, v in adv.items()}, open(os.path.join(out, "adversarial.json"), "w"), indent=2, default=str)
    t = adv["totals"]
    L = ["# Adversarial scenario campaign", "",
         f"{adv['scenarios']} seeded random farms, each with hard events (breakdown mid-job, road closures and uncertain "
         "obstacles, urgent jobs, fuel drops, communication loss, delays, single-lane roads). Every coordinator runs the "
         "identical scenario. Invariants are checked on what actually ran.", "",
         "| Coordinator | Jobs completed | Jobs on time | Priority-weighted on time | Travel (min) | Rule violations |",
         "|---|---|---|---|---|---|"]
    for p in ("smart", "fcfs", "nearest"):
        a = t[p]
        L.append(f"| {p} | {a['completed']} | {a['on_time']} | {a['pw_on_time']} | {a['travel']:.0f} | {a['violations']} |")
    kinds = {}
    for f in adv["failures"]:
        kinds.setdefault((f["policy"], f["kind"]), []).append(f["seed"])
    L += ["", f"SYNTERRA not worse than FCFS on priority-weighted on-time work: **{adv['smart_not_worse_than_fcfs']}**", "",
          "## Findings", ""]
    if not kinds:
        L.append("- No failures.")
    for (p, k), seeds in sorted(kinds.items()):
        L.append(f"- {p}: {k} in {len(seeds)} scenario(s): seeds {seeds[:15]}")
    L += ["", "Bugs this campaign found during development (all fixed, and covered by tests): implement hand-over claimed "
          "before it was committed; two queued jobs in one field tracked wrongly; a drive longer than the fuel left; "
          "unreported blackout work not counted as repeated work; tractors only driving to a low-priority job could not be "
          "redirected after a breakdown; a cancelled implement hand-over left the receiving tractor's trip active; a "
          "stopped tractor cleared an implement it did not hold; a tractor stranded without fuel locked the only "
          "sprayer (now: fuel delivery after recovery_minutes); a delay pushed back only the first tractor queued behind, "
          "not the whole chain."]
    if "clearest_failure" in adv:
        L += ["", f"Clearest remaining failure: seed {adv['clearest_failure']['seed']} - {adv['clearest_failure']['detail']}"]
    open(os.path.join(out, "adversarial.md"), "w", encoding="utf-8").write("\n".join(L) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--small", type=int, default=60)
    ap.add_argument("--seeds", type=int, default=200)
    ap.add_argument("--out", default="out")
    a = ap.parse_args()
    opt, rows = optimality(a.small)
    print("optimality:", opt)
    adv = campaign(a.seeds)
    print("adversarial:", adv["smart_not_worse_than_fcfs"], {p: (v["pw_on_time"], v["violations"]) for p, v in adv["totals"].items()})
    write(a.out, opt, rows, adv)


if __name__ == "__main__":
    main()
