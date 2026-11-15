"""Work Function Algorithm (WFA) for the k-server problem: the algorithm behind k-Vac.

k-server view of our farm:
  servers  = working tractors (labelled: each has its own capabilities)
  points   = yard + field entrances, distance = shortest road travel minutes (navigation table)
  requests = jobs, visited in earliest-deadline order; a request at field r must be served by a
             tractor able to do that job type

Work function  w_t(C) = cheapest total movement that starts at the initial configuration,
serves requests 1..t in order, and ends in configuration C.
  w_0(C) = d(C0, C)
  w_t(C) = min over C' that serve r_t (some compatible tractor i stands on r_t) of  w_{t-1}(C') + d(C', C)
with d(C', C) = sum over tractors of travel(C'_i, C_i). Because d is a sum over tractors, the inner
minimum is computed axis by axis (a separable min-plus transform), so with 3 tractors and 9 points
it is 9^3 = 729 configurations: instant with numpy.

WFA rule: serve r_t with the tractor i minimising  w_t(C with i moved to r_t) + travel(C_i, r_t).

What this does and does NOT give us:
  - It is the real WFA recursion (not a renamed greedy rule).
  - Its known competitive ratio (2k-1) is for the pure k-server problem. Our problem adds work time,
    implements, fuel, battery and deadlines, so that guarantee does NOT carry over.
  - We use WFA's tractor-per-job choice as one candidate schedule inside SYNTERRA's search; the
    lexicographic objective and the safety agent decide whether it is used.
"""
import numpy as np
from .navigation import YARD, INF

BIG = 1e7


def _minplus_axis(A, D, axis):
    """B[..., c, ...] = min_{c'} A[..., c', ...] + D[c', c] along one axis."""
    A2 = np.moveaxis(A, axis, -1)                      # (..., n)
    B = np.min(A2[..., :, None] + D, axis=-2)          # (..., n)
    return np.moveaxis(B, -1, axis)


def _transform(A, D, k):
    for ax in range(k):
        A = _minplus_axis(A, D, ax)
    return A


def wfa_run(D, start, requests):
    """Pure WFA. D: (n x n) distances, start: server positions, requests: [(node, [able servers])].
    Returns (chosen server per request, min_C w_final(C)). The final minimum of the work function
    equals the offline optimum movement cost (used in the tests to check the recursion)."""
    D = np.asarray(D, dtype=float)
    n, k = D.shape[0], len(start)
    w = np.zeros((n,) * k)
    for i in range(k):
        shape = [1] * k
        shape[i] = n
        w = w + D[start[i]].reshape(shape)
    cur, chosen = list(start), []
    for r, able in requests:
        if not able:
            chosen.append(None)
            continue
        new_w = np.full_like(w, BIG * 10)
        for i in able:
            A = np.full_like(w, BIG * 10)
            sl = [slice(None)] * k
            sl[i] = r
            A[tuple(sl)] = w[tuple(sl)]
            new_w = np.minimum(new_w, _transform(A, D, k))
        w = new_w
        best, best_i = None, None
        for i in able:
            c = list(cur)
            c[i] = r
            val = w[tuple(c)] + D[cur[i], r]
            if best is None or val < best - 1e-9:
                best, best_i = val, i
        cur[best_i] = r
        chosen.append(best_i)
    return chosen, float(w.min())


def offline_opt(D, start, requests):
    """Brute-force optimum (for tests): try every server choice per request."""
    import itertools
    best = None
    choices = [able for _, able in requests if able]
    reqs = [r for r, able in requests if able]
    for combo in itertools.product(*choices):
        pos, cost = list(start), 0.0
        for r, i in zip(reqs, combo):
            cost += D[pos[i]][r]
            pos[i] = r
        best = cost if best is None else min(best, cost)
    return best if best is not None else 0.0


def wfa_assign(mem, tvs, jobs):
    """Return [(job_id, tractor_id), ...] in the order WFA serves them, or [] if not applicable."""
    tids = sorted(tvs)
    if not tids or not jobs:
        return []
    nodes = [YARD] + sorted(mem.fields)
    idx = {n: i for i, n in enumerate(nodes)}
    D = [[mem.dist[a][b] if mem.dist[a][b] != INF else BIG for b in nodes] for a in nodes]
    start = [idx[tvs[t]["pos"]] for t in tids]
    order = sorted(jobs, key=lambda j: (j["deadline"], -j["priority"], j["order"]))
    reqs = [(idx[j["field"]], [i for i, t in enumerate(tids)
                                if j["type"] in tvs[t]["caps"] and j["implement"] in tvs[t]["compat"]])
            for j in order]
    chosen, _ = wfa_run(D, start, reqs)
    return [(j["id"], tids[c]) for j, c in zip(order, chosen) if c is not None]
