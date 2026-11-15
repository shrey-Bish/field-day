"""Hungarian algorithm (Kuhn-Munkres, O(n^3) with potentials / shortest augmenting paths).

hungarian(cost) -> list of (row, col) pairs minimising total cost; works for rectangular
matrices (rows <= cols or rows > cols) and treats INF entries as forbidden pairs.

Used by the planner for "sequential optimal assignment" (a standard multi-robot task-allocation
scheme): in each round every free tractor gets at most one job, chosen so that the whole round
is optimal, not tractor-by-tractor greedy. Rounds repeat until all jobs are placed.
"""
INF = float("inf")
FORBID = 1e15


def hungarian(cost):
    """Minimum-cost assignment. cost: list of rows (list of numbers, INF = not allowed).
    Returns [(r, c)] for every row that gets a column with a finite cost."""
    n_rows = len(cost)
    if n_rows == 0:
        return []
    n_cols = len(cost[0])
    if n_cols == 0:
        return []
    transpose = n_rows > n_cols
    a = [list(r) for r in cost]
    if transpose:
        a = [list(col) for col in zip(*a)]
    n, m = len(a), len(a[0])                       # n <= m
    a = [[FORBID if x == INF else x for x in row] for row in a]
    # 1-indexed potentials algorithm (e-maxx formulation)
    u = [0.0] * (n + 1)
    v = [0.0] * (m + 1)
    p = [0] * (m + 1)
    way = [0] * (m + 1)
    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = [INF] * (m + 1)
        used = [False] * (m + 1)
        while True:
            used[j0] = True
            i0, delta, j1 = p[j0], INF, 0
            for j in range(1, m + 1):
                if not used[j]:
                    cur = a[i0 - 1][j - 1] - u[i0] - v[j]
                    if cur < minv[j]:
                        minv[j], way[j] = cur, j0
                    if minv[j] < delta:
                        delta, j1 = minv[j], j
            for j in range(m + 1):
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
    pairs = []
    for j in range(1, m + 1):
        if p[j]:
            r, c = p[j] - 1, j - 1
            if a[r][c] >= FORBID:
                continue
            pairs.append((c, r) if transpose else (r, c))
    return sorted(pairs)


def assignment_cost(cost, pairs):
    return sum(cost[r][c] for r, c in pairs)
