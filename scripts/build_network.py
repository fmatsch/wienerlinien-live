"""Builds the compact per-line network description from the extracted GTFS subset
(scripts/extract_gtfs.py) and the live RBL scan (scripts/scan_ubahn.py).

Output: backend/data/network.json (also copied to web/network.json)
"""
import json, math, statistics, shutil
from collections import Counter, defaultdict

COLORS = {'U1': '#E3000F', 'U2': '#A862A4', 'U3': '#EF7C00', 'U4': '#00A04A', 'U5': '#2FB8B2', 'U6': '#9C6B3C'}
DWELL = 25  # s, GTFS is minute-granular and carries no dwell
g = json.load(open('data/raw/gtfs_u.json'))
rbls = json.load(open('data/raw/ubahn_rbls.json'))


def diva(stop_id):
    return 60200000 + int(stop_id.split(':')[2])


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


by_ld = defaultdict(list)
for tid, t in g['trips'].items():
    by_ld[(t['line'], t['dir'])].append(t)

net = {'generated': None, 'lines': {}}
for (line, d), trips in sorted(by_ld.items()):
    D = 'H' if d == '0' else 'R'
    pats = Counter(tuple(diva(s[1]) for s in t['st']) for t in trips)
    master = max(pats, key=lambda s: (len(s), pats[s]))
    mtrips = [t for t in trips if tuple(diva(s[1]) for s in t['st']) == master]
    # geometry: most common shape of master trips
    shape_id = Counter(t['shape'] for t in mtrips).most_common(1)[0][0]
    raw = g['shapes'][shape_id]
    path = simplify([tuple(p) for p in raw])
    cum = [0.0]
    for i in range(1, len(path)):
        cum.append(cum[-1] + m(*path[i - 1], *path[i]))
    # stations
    ref = mtrips[0]['st']
    rb_by_diva = defaultdict(list)
    for rbl, entries in rbls.items():
        for e in entries:
            if e['line'] == line and e['dir'] == D:
                rb_by_diva[int(e['diva'])].append((int(rbl), e))
    stations, s_prev = [], 0.0
    for idx, dv in enumerate(master):
        sid = ref[idx][1]
        name, lat, lon = g['stops'][sid]
        cands = rb_by_diva.get(dv, [])
        rbl = None
        if cands:
            # several RBLs at one DIVA (platform variants): take the one whose live coordinate is closest to the GTFS platform
            rbl, e = min(cands, key=lambda c: m(lat, lon, c[1]['coord'][1], c[1]['coord'][0]))
            name = e['title'] or name
        s = project(path, cum, lat, lon, s_prev)
        s_prev = s
        stations.append({'diva': dv, 'name': name, 'rbl': rbl, 'lat': lat, 'lon': lon, 's': round(s, 1),
                         'alt_rbls': [c[0] for c in cands if c[0] != rbl]})
    # total daytime time first->last station (GTFS, minute granular, median over trips)
    tot = []
    for t in mtrips:
        if 7 * 3600 <= t['st'][0][3] <= 21 * 3600:
            tot.append(t['st'][-1][2] - t['st'][0][3])
    total = statistics.median(tot) if tot else statistics.median([t['st'][-1][2] - t['st'][0][3] for t in mtrips])
    n = len(stations)
    # dep->dep seconds per segment: distribute total time by (dwell + length / v)
    lens = [stations[i + 1]['s'] - stations[i]['s'] for i in range(n - 1)]
    inner_dwell = DWELL * (n - 2)
    moving = max(total - inner_dwell, 60)
    vsum = sum(lens) or 1
    dep2dep = []
    for i, L in enumerate(lens):
        run = moving * L / vsum
        dep2dep.append(round(run + (DWELL if i + 1 < n - 1 else 0), 1))
    for i, st in enumerate(stations):
        st['dep2dep'] = dep2dep[i] if i < n - 1 else None
    net['lines'].setdefault(line, {'color': COLORS[line], 'dirs': {}})['dirs'][D] = {
        'towards': trips[0]['head'].replace('Wien ', ''),
        'dwell': DWELL,
        'stations': stations,
        'path': [[round(a, 6), round(b, 6)] for a, b in path],
        'cum': [round(c, 1) for c in cum],
        'total_gtfs_s': total,
    }
    print(line, D, n, 'stations', len(path), 'pts', 'total', total, 's', 'no rbl:', [s['name'] for s in stations if not s['rbl']],
          'multi:', [(s['name'], s['alt_rbls']) for s in stations if s['alt_rbls']])

import datetime
net['generated'] = datetime.date.today().isoformat()
json.dump(net, open('backend/data/network.json', 'w'), ensure_ascii=False, separators=(',', ':')) if __import__('os').makedirs('backend/data', exist_ok=True) is None else None
shutil.copy('backend/data/network.json', 'web/network.json')
