"""Navigation agent: travel times between the yard and every field.
Worked out once and stored as a table; rebuilt only when a route is blocked.

The farm is a weighted graph: nodes are the yard and the field entrances, edges are farm
roads (Manhattan distance x minutes_per_cell). Confirmed-blocked AND uncertain roads are
removed, so a road that might be unsafe is never used. Shortest paths: Floyd-Warshall
(all pairs; 9 nodes, so this is instant)."""
INF = float("inf")
YARD = "YARD"


def points(mem):
    pts = {YARD: tuple(mem.yard)}
    pts.update({fid: (f["x"], f["y"]) for fid, f in mem.fields.items()})
    return pts


def edge_minutes(mem, a, b):
    pts = points(mem)
    return (abs(pts[a][0] - pts[b][0]) + abs(pts[a][1] - pts[b][1])) * mem.cell_minutes


def avoid(mem):
    return set(mem.blocked) | set(getattr(mem, "uncertain", set()))


def rebuild(mem):
    pts = points(mem)
    nodes = list(pts)
    closed = avoid(mem)
    dist = {a: {b: INF for b in nodes} for a in nodes}
    nxt = {a: {b: None for b in nodes} for a in nodes}
    for a in nodes:
        dist[a][a], nxt[a][a] = 0, a
        for b in nodes:
            if a != b and frozenset((a, b)) not in closed:
                dist[a][b] = (abs(pts[a][0] - pts[b][0]) + abs(pts[a][1] - pts[b][1])) * mem.cell_minutes
                nxt[a][b] = b
    for k in nodes:
        for i in nodes:
            for j in nodes:
                if dist[i][k] + dist[k][j] < dist[i][j]:
                    dist[i][j] = dist[i][k] + dist[k][j]
                    nxt[i][j] = nxt[i][k]
    mem.dist, mem.nxt = dist, nxt


def travel(mem, a, b):
    return mem.dist[a][b]


def path(mem, a, b):
    if mem.nxt[a][b] is None:
        return []
    p = [a]
    while a != b:
        a = mem.nxt[a][b]
        p.append(a)
    return p


def edges_of(p):
    return [frozenset((p[i], p[i + 1])) for i in range(len(p) - 1)]
