#!/usr/bin/env python3
"""Recovery agent: runs every time a tractor agent becomes faulty (broken down, out of fuel,
battery flat, or stopped reporting) and decides who takes over its unfinished passes.

Inputs, all taken from the tractor agents' latest reports:
  * each healthy tractor's fuel and battery remaining, minus what it will still use on its
    own unfinished passes;
  * where each healthy tractor will be when its own path is finished;
  * where the faulty tractor stopped, and the unfinished passes it leaves behind.

For every (unfinished pass, healthy tractor) pair it works out a cost:

    cost = W_DIST * metres to drive from the tractor's finishing point to the pass
                    (for the interrupted pass: to the exact spot where the faulty tractor stopped)
         + W_FUEL * (1 - fuel left afterwards)       fuel left after driving there and spraying it
         + W_BATT * (1 - battery left afterwards)

A pair that would leave the tractor below RESERVE_PCT fuel or battery is not allowed.

The Hungarian algorithm then picks the assignment with the lowest total cost, at most one
pass per tractor per round. Each pass may also "wait" for a later round, at a cost of
WAIT_MARGIN above its cheapest tractor, so a tractor that would cost much more than the best
one (say it is nearly empty) is never forced to take a pass just because it is free. After
each round, a tractor that took a pass has its finishing point moved to the end of that pass,
and its fuel and battery reduced by what that pass costs. Rounds repeat until every pass is
placed or nothing more is affordable.

Pure functions, standard library only; the coordinator applies the result.
"""
import math

from tractor_agent import (BATTERY_MOVE_PCT_PER_KM, BATTERY_WORK_PCT_PER_KM, FUEL_MOVE_L_PER_KM,
                           FUEL_WORK_L_PER_KM, TANK_L)

W_DIST = 1.0         # cost per metre driven to reach the work
W_FUEL = 300.0       # an empty tank afterwards costs as much as 300 m of extra driving
W_BATT = 200.0       # a flat battery afterwards costs as much as 200 m of extra driving
RESERVE_PCT = 5.0    # never plan a tractor below this much fuel or battery
WAIT_MARGIN = 150.0  # a pass waits a round rather than go to a tractor this much dearer than its best
NOT_ALLOWED = 1e9


# --------------------------------------------------------------------------- Hungarian algorithm
def hungarian(cost):
    """Minimum-cost assignment for an n x m cost matrix (lists of numbers).

    Returns (row, col) pairs, each row and each column used at most once, min(n, m) pairs.
    The matrix is padded to square with zero-cost dummy rows/columns; pairs that land on a
    dummy are dropped. O(N^3), shortest augmenting path with potentials."""
    n = len(cost)
    m = len(cost[0]) if n else 0
    if n == 0 or m == 0:
        return []
    size = max(n, m)
    a = [[cost[i][j] if i < n and j < m else 0.0 for j in range(size)] for i in range(size)]
    inf = float("inf")
    u = [0.0] * (size + 1)
    v = [0.0] * (size + 1)
    p = [0] * (size + 1)          # p[j] = row matched to column j (1-based, 0 = none)
    way = [0] * (size + 1)
    for i in range(1, size + 1):
        p[0] = i
        j0 = 0
        minv = [inf] * (size + 1)
        used = [False] * (size + 1)
        while True:
            used[j0] = True
            i0, delta, j1 = p[j0], inf, 0
            for j in range(1, size + 1):
                if not used[j]:
                    cur = a[i0 - 1][j - 1] - u[i0] - v[j]
                    if cur < minv[j]:
                        minv[j], way[j] = cur, j0
                    if minv[j] < delta:
                        delta, j1 = minv[j], j
            for j in range(size + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while True:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
            if j0 == 0:
                break
    return [(p[j] - 1, j - 1) for j in range(1, size + 1) if 0 < p[j] <= n and j <= m]


# --------------------------------------------------------------------------- pricing
def price(item, tractor):
    """Cost of `tractor` taking over `item`, from where it finishes its own path.

    item:    {"options": [((u, v) entry, (u, v) exit), ...], "length": metres of spraying}
    tractor: {"finish": (u, v), "fuel_l": litres, "batt_pct": percent}
    Returns a dict; "cost" is NOT_ALLOWED when the tractor could not afford it."""
    fu, fv = tractor["finish"]
    entry, exit_ = min(item["options"], key=lambda o: math.hypot(o[0][0] - fu, o[0][1] - fv))
    dist = math.hypot(entry[0] - fu, entry[1] - fv)
    work = item["length"]
    fuel_after_l = tractor["fuel_l"] - dist / 1000 * FUEL_MOVE_L_PER_KM - work / 1000 * FUEL_WORK_L_PER_KM
    batt_after = tractor["batt_pct"] - dist / 1000 * BATTERY_MOVE_PCT_PER_KM - work / 1000 * BATTERY_WORK_PCT_PER_KM
    fuel_after = 100 * fuel_after_l / TANK_L
    ok = fuel_after >= RESERVE_PCT and batt_after >= RESERVE_PCT
    cost = (W_DIST * dist + W_FUEL * (1 - fuel_after / 100) + W_BATT * (1 - batt_after / 100)) if ok else NOT_ALLOWED
    return {"cost": cost, "dist": dist, "fuel_after": fuel_after, "fuel_after_l": fuel_after_l,
            "batt_after": batt_after, "exit": exit_, "ok": ok}


def plan(items, tractors):
    """Assign every item it can, in Hungarian rounds.

    Returns {"assignments": [{"item": i, "tractor": j, "round": r, **price}],
             "unassigned": [item indexes], "first_round": matrix of price dicts [item][tractor],
             "rounds": number of rounds, "total": summed cost}."""
    state = [dict(t) for t in tractors]
    pending = list(range(len(items)))
    assignments, first_round, rounds = [], None, 0
    while pending and state:
        rounds += 1
        table = [[price(items[i], s) for s in state] for i in pending]
        if first_round is None:
            first_round = table
        # One "wait" column per pass: waiting costs a little more than its cheapest tractor,
        # so assigning a pass to its best tractor always beats waiting (every round makes
        # progress), but a much dearer tractor is not forced to take it.
        matrix = []
        for row in table:
            allowed = [cell["cost"] for cell in row if cell["ok"]]
            wait = (min(allowed) + WAIT_MARGIN) if allowed else 0.0
            matrix.append([cell["cost"] for cell in row] + [wait] * len(pending))
        placed = []
        for r, c in hungarian(matrix):
            if c >= len(state):
                continue                       # this pass waits for a later round
            cell = table[r][c]
            if not cell["ok"]:
                continue                       # nobody can afford it
            i = pending[r]
            assignments.append(dict(cell, item=i, tractor=c, round=rounds))
            s = state[c]                       # this tractor's path now ends where that pass ends
            s["finish"], s["fuel_l"], s["batt_pct"] = cell["exit"], cell["fuel_after_l"], cell["batt_after"]
            placed.append(i)
        if not placed:
            break
        pending = [i for i in pending if i not in placed]
    return {"assignments": assignments, "unassigned": pending, "first_round": first_round or [],
            "rounds": rounds, "total": sum(a["cost"] for a in assignments)}
