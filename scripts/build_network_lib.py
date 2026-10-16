import math


def m(lat1, lon1, lat2, lon2):
    k = 111320.0
    return math.hypot((lat2 - lat1) * k, (lon2 - lon1) * k * math.cos(math.radians(lat1)))


def project(path, cum, lat, lon, s_min):
    """Projects a point onto the polyline, search starts at arc length s_min."""
    best = (1e18, s_min)
    for i in range(len(path) - 1):
        if cum[i + 1] < s_min - 1:
            continue
        (a, b), (c, d) = path[i], path[i + 1]
        kx = 111320.0 * math.cos(math.radians(a))
        ax, ay, bx, by = 0, 0, (d - b) * kx, (c - a) * 111320.0
        px, py = (lon - b) * kx, (lat - a) * 111320.0
        L2 = bx * bx + by * by
        t = 0 if L2 == 0 else max(0, min(1, ((px - ax) * bx + (py - ay) * by) / L2))
        dist = math.hypot(px - t * bx, py - t * by)
        s = cum[i] + t * (cum[i + 1] - cum[i])
        if s < s_min:
            continue
        if dist < best[0]:
            best = (dist, s)
    return best[1]


def simplify(pts, eps=2.5):
    """Douglas-Peucker in metres."""
    if len(pts) < 3:
        return pts
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        lo, hi = stack.pop()
        a, b = pts[lo], pts[hi]
        kx = 111320.0 * math.cos(math.radians(a[0]))
        bx, by = (b[1] - a[1]) * kx, (b[0] - a[0]) * 111320.0
        L = math.hypot(bx, by) or 1e-9
        far, fi = 0, None
        for i in range(lo + 1, hi):
            px, py = (pts[i][1] - a[1]) * kx, (pts[i][0] - a[0]) * 111320.0
            dd = abs(px * by - py * bx) / L
            if dd > far:
                far, fi = dd, i
        if fi is not None and far > eps:
            keep[fi] = True
            stack += [(lo, fi), (fi, hi)]
    return [p for p, k in zip(pts, keep) if k]


