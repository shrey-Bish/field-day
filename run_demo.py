"""Run SYNTERRA and both baselines through the SAME farm and the SAME events, then write:

  out/event_log.jsonl          full event log of our coordinator on scenario.json (hand-in)
  out/event_log_fcfs.jsonl     same for the first-come-first-served baseline
  out/event_log_nearest.jsonl  same for the nearest-tractor baseline
  out/comparison.md / .json    scoreboard (all numbers computed from what actually ran)
  out/timeline.json            frames for the dashboard (positions, fuel, battery, km, spraying)
  out/stress/...               the same for scenarios/stress.json (every event type)

Usage:  python run_demo.py [scenario.json] [--out out] [--step 5]
"""
import argparse, json, os, sys
from fleet.orchestrator import simulate
from fleet import metrics, explainer

POLICIES = [("smart", "SYNTERRA (ours)"), ("fcfs", "Baseline A: first come, first served"),
            ("nearest", "Baseline B: nearest compatible tractor")]

ROWS = [
    ("jobs_completed", "Jobs completed", "high"),
    ("jobs_on_time", "Jobs finished on time", "high"),
    ("priority_weighted_on_time", "Priority-weighted work on time", "high"),
    ("jobs_late", "Jobs finished late", "low"),
    ("jobs_unfinished", "Jobs not finished", "low"),
    ("travel_minutes", "Travel time (min)", "low"),
    ("distance_km", "Distance driven (km)", "low"),
    ("idle_minutes", "Idle while on duty (min)", "low"),
    ("spare_capacity_minutes", "Spare capacity after own work (min)", None),
    ("makespan_minutes", "All work finished after (min)", "low"),
    ("fuel_used_minutes", "Fuel used (min of engine time)", "low"),
    ("refuel_stops", "Refuel stops", "low"),
    ("recharge_stops", "Recharge stops", "low"),
    ("implement_swaps", "Implement changes", "low"),
    ("repeated_or_lost_work_minutes", "Repeated or lost work (min)", "low"),
    ("recovery_minutes_avg", "Avg time until interrupted work resumes (min)", None),
    ("audit_violations", "Rule violations in what actually ran", "low"),
    ("deadline_warnings_in_plans", "Deadline warnings raised in plans", None),
    ("plans_rejected", "Plans rejected by the safety agent", None),
    ("replans", "Re-plans", None),
    ("planning_ms_max", "Slowest re-plan (ms)", None),
]


def run_all(scenario, out_dir, step, timeline=True):
    os.makedirs(out_dir, exist_ok=True)
    runs, res = {}, {}
    for pol, _ in POLICIES:
        m = simulate(scenario, pol, step=step)
        runs[pol] = m
        res[pol] = metrics.compute(m)
        m.save_log(os.path.join(out_dir, "event_log.jsonl" if pol == "smart" else f"event_log_{pol}.jsonl"))
    with open(os.path.join(out_dir, "comparison.json"), "w") as f:
        json.dump(dict(scenario=scenario.get("name"), results=res,
                       audit={p: metrics.audit(m) for p, m in runs.items()}), f, indent=2)
    with open(os.path.join(out_dir, "comparison.md"), "w", encoding="utf-8") as f:
        f.write(comparison_md(scenario, runs, res))
    if timeline:
        with open(os.path.join(out_dir, "timeline.json"), "w") as f:
            json.dump(timeline_json(scenario, runs, res, step), f)
    return runs, res


def _fmt(v):
    if v is None:
        return "-"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def comparison_md(scenario, runs, res):
    L = [f"# Comparison: {scenario.get('name', 'scenario')}", "",
         "Same farm, same jobs, same events, same hard rules (capability, implement, fuel, battery, roads) "
         "for every coordinator. Every number below is computed from the tasks that actually ran "
         "(`mem.history`) and the event log, never from plans.", "",
         "| Measure | " + " | ".join(name for _, name in POLICIES) + " |",
         "|---|" + "---|" * len(POLICIES)]
    for key, label, better in ROWS:
        vals = [res[p][key] for p, _ in POLICIES]
        cells = []
        nums = [v for v in vals if isinstance(v, (int, float))]
        best = (max(nums) if better == "high" else min(nums)) if better and nums else None
        for v in vals:
            s = _fmt(v)
            cells.append(f"**{s}**" if best is not None and v == best and nums.count(best) < len(nums) else s)
        L.append(f"| {label} | " + " | ".join(cells) + " |")
    s, b = res["smart"], res["fcfs"]
    L += ["", "## Against the first-come-first-served baseline", ""]
    L.append(f"- Jobs on time: {s['jobs_on_time']} vs {b['jobs_on_time']} "
             f"({s['jobs_on_time'] - b['jobs_on_time']:+d}).")
    if b["travel_minutes"]:
        L.append(f"- Travel: {s['travel_minutes']:.0f} vs {b['travel_minutes']:.0f} min "
                 f"({100 * (s['travel_minutes'] - b['travel_minutes']) / b['travel_minutes']:+.0f}%).")
    L.append(f"- All work finished after {s['makespan_minutes']:.0f} vs {b['makespan_minutes']:.0f} min.")
    L.append(f"- Idle while on duty: {s['idle_minutes']:.0f} vs {b['idle_minutes']:.0f} min.")
    L.append(f"- Rule violations in what actually ran: {s['audit_violations']} vs {b['audit_violations']}.")
    worse = [label for key, label, better in ROWS if better and isinstance(s[key], (int, float))
             and isinstance(b[key], (int, float)) and
             ((better == "high" and s[key] < b[key]) or (better == "low" and s[key] > b[key]))]
    if worse:
        L.append(f"- Where SYNTERRA did worse than the baseline here: {', '.join(worse)}.")
    else:
        L.append("- SYNTERRA was not worse than the baseline on any scored measure in this scenario.")
    L += ["", "## Plan changes and why (SYNTERRA)", ""]
    for e in runs["smart"].explanations:
        L.append(f"**{e['clock']} - {e['cause'].replace('_', ' ').lower()} (plan v{e['plan_version']})**")
        L.append("")
        for snt in e["sentences"]:
            L.append(f"- {snt}")
        L.append("")
    dec = runs["smart"].decisions
    if dec:
        L += ["## Decisions made under uncertainty", ""]
        for d in dec:
            L.append(f"- {runs['smart'].clock(d['t'])} {d['topic']}: **{d['decision']}** - {d['reason']} "
                     f"(value of information {d.get('value_of_information')}; assumptions: {d.get('assumptions')}, "
                     f"hypotheses: {d.get('hypotheses')})")
        L.append("")
    L += ["## Notes", "",
          "- Idle while on duty = time a tractor waited (for an implement, a field or a road) before its own "
          "last task finished. Spare capacity = time after its own work was done while others still worked.",
          "- Repeated or lost work = minutes inside a section that was interrupted before it finished, plus "
          "sections done twice after a communication loss. Finished sections are never reset.",
          "- Deadline warnings are reported by the safety agent and shown, never hidden."]
    return "\n".join(L) + "\n"


def timeline_json(scenario, runs, res, step):
    m = runs["smart"]
    return dict(
        format="field-day timeline v2",
        scenario=dict(name=scenario.get("name"), start_clock=m.start_clock, yard=m.yard,
                      fields=scenario["fields"], tractors=scenario["tractors"], jobs=scenario["jobs"],
                      implements=scenario["implements"], narrow_roads=scenario.get("narrow_roads", [])),
        step_minutes=step,
        frames=m.frames,
        baseline_frames=runs["fcfs"].frames,
        explanations=m.explanations,
        decisions=m.decisions,
        why={jid: explainer.why(m, jid) for jid in m.jobs},
        job_history={jid: j["history"] for jid, j in m.jobs.items()},
        events=[e for e in m.log if e["event"] not in ("JOB_STATE",)],
        comparison={p: res[p] for p, _ in POLICIES},
    )


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("scenario", nargs="?", default="scenario.json")
    ap.add_argument("--out", default="out")
    ap.add_argument("--step", type=int, default=5)
    ap.add_argument("--no-stress", action="store_true")
    a = ap.parse_args(argv)
    sc = json.load(open(a.scenario))
    _, res = run_all(sc, a.out, a.step)
    print(f"{a.scenario}: written to {a.out}/")
    for p, name in POLICIES:
        r = res[p]
        print(f"  {name:40s} completed {r['jobs_completed']}/{r['jobs_total']}, on time {r['jobs_on_time']}, "
              f"travel {r['travel_minutes']:.0f} min ({r['distance_km']} km), idle {r['idle_minutes']:.0f} min, "
              f"violations {r['audit_violations']}")
    if not a.no_stress and os.path.exists("scenarios/stress.json"):
        sc2 = json.load(open("scenarios/stress.json"))
        _, res2 = run_all(sc2, os.path.join(a.out, "stress"), a.step)
        print(f"scenarios/stress.json: written to {a.out}/stress/")
        for p, name in POLICIES:
            r = res2[p]
            print(f"  {name:40s} completed {r['jobs_completed']}/{r['jobs_total']}, on time {r['jobs_on_time']}, "
                  f"travel {r['travel_minutes']:.0f} min, idle {r['idle_minutes']:.0f} min, "
                  f"violations {r['audit_violations']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
