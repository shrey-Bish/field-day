"""Uncertainty-aware decisions (small value-of-information model).

Facts and hypotheses are kept apart. When a tractor goes silent, the FACT is "no message since t".
The hypotheses (still working / stopped / broken / implement failed) get explicit prior
probabilities from the scenario (or the defaults below). These numbers are ASSUMPTIONS for the
demo, not measured rates, and they are written into every decision record.

Rule that never depends on probabilities: work owned by a silent tractor is NOT handed to anyone
else until the tractor is known to have stopped (its own authorisation lease ran out, or a
verification confirmed it). That rule prevents two machines working the same rows.
"""

DEFAULT_COMM_PRIORS = {"still_working": 0.6, "communication_failure_stopped": 0.1,
                       "mechanical_breakdown": 0.25, "implement_failure": 0.05}
DEFAULT_OBSTACLE_CLEAR_PROB = 0.5
VERIFY_COST = 10.0   # minutes-equivalent cost of sending a scout check


def comm_loss_decision(mem, tid, priors=None):
    priors = dict(priors or mem.scenario.get("comm_loss_priors") or DEFAULT_COMM_PRIORS)
    task = mem.active.get(tid)
    p_stopped = 1.0 - priors.get("still_working", 0.0)
    if task is None:
        return dict(topic="comm_loss", tractor=tid, decision="wait",
                    facts=[f"no message from {tid}", f"{tid} holds no work"],
                    hypotheses=priors, reason="nothing is at risk; wait for the tractor to reconnect")
    job = mem.jobs[task["job"]]
    weight = job["priority"]
    lease, verify = mem.lease_minutes, mem.verify_minutes
    # If the tractor has really stopped, every minute we wait delays its unfinished work.
    loss_wait = p_stopped * weight * lease
    loss_verify = p_stopped * weight * verify + VERIFY_COST
    voi = round(loss_wait - loss_verify, 2)
    decision = "verify" if voi > 0 and verify < lease else "wait_for_lease"
    return dict(topic="comm_loss", tractor=tid, job=task["job"], decision=decision,
                facts=[f"no message from {tid} since {mem.clock(mem.now)}",
                       f"{tid} owns {task['job']} ({job['remaining']} min of work planned)"],
                hypotheses=priors, assumptions=dict(lease_minutes=lease, verify_minutes=verify,
                                                    verify_cost=VERIFY_COST, priority=weight),
                expected_loss_wait=round(loss_wait, 2), expected_loss_verify=round(loss_verify, 2),
                value_of_information=voi,
                reason=("a scout check is worth it: it could release the work "
                        f"{lease - verify} min sooner" if decision == "verify"
                        else "a check would cost more than it saves; wait for the safety lease"))


def obstacle_decision(mem, edge, detour_minutes, p_clear=None):
    p_clear = mem.scenario.get("obstacle_clear_prob", DEFAULT_OBSTACLE_CLEAR_PROB) if p_clear is None else p_clear
    gain = round(p_clear * detour_minutes - VERIFY_COST, 2)
    decision = "verify" if gain > 0 else "avoid"
    return dict(topic="obstacle", edge="-".join(sorted(edge)), decision=decision,
                facts=[f"possible obstacle on road {'-'.join(sorted(edge))} (not confirmed)",
                       "road closed for planning until confirmed clear"],
                hypotheses={"clear": p_clear, "blocked": round(1 - p_clear, 2)},
                assumptions=dict(verify_cost=VERIFY_COST, verify_minutes=mem.verify_minutes),
                detour_minutes=detour_minutes, value_of_information=gain,
                reason=("worth checking: planned trips would save about "
                        f"{detour_minutes:.0f} min if the road is clear" if decision == "verify"
                        else "the detour is cheap; keep avoiding the road without checking"))
