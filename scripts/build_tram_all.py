"""Builds backend/data/network_tram.json for ALL tram lines.

For every tram line: most common stop pattern per direction (GTFS), geometry (most common shape), stops mapped to live
RBLs by coordinates (GTFS stop ids are not reliably DIVA numbers), segment times from the timetable, refined with live
samples (forecasts are schedule-propagated, multiples of 30 s).

Run from the project root (needs data/gtfs from `make setup`):  python3 -I scripts/build_tram_all.py
"""
import csv, json, math, shutil, statistics, sys, time, urllib.request
from collections import Counter, defaultdict
sys.path.insert(0, 'backend')
sys.path.insert(0, 'scripts')
from engine import align, parse_monitor
from build_network_lib import m, project, simplify

G = 'data/gtfs/'
DWELL = 15
H = {'Accept': 'application/json', 'User-Agent': 'fmatsch-ubahn-livemap/1.0 (+https://fmatsch.ist/wienerlinien; network build)'}
rd = lambda f: csv.DictReader(open(G + f, encoding='utf-8-sig'))
sec = lambda s: int(s[:2]) * 3600 + int(s[3:5]) * 60 + int(s[6:8])

import colorsys
def palette(names):
    """Wiener Linien paint every tram line red; the map needs distinguishable colours (golden-angle hues)."""
    out = {}
    for i, n in enumerate(names):
        h = (i * 137.508) % 360 / 360
        l = (0.38, 0.46, 0.32)[i % 3]
        r, g, b = colorsys.hls_to_rgb(h, l, 0.72)
        out[n] = '#%02X%02X%02X' % (round(r * 255), round(g * 255), round(b * 255))
    return out

# ---- which lines
live_lines = {r['LineText'] for r in csv.DictReader(open('data/raw/wienerlinien-ogd-linien.csv'), delimiter=';')
              if r['MeansOfTransport'] == 'ptTram' and r['Realtime'] == '1'}
routes = {r['route_id']: r for r in rd('routes.txt') if r['route_type'] == '0' and r['route_short_name'] in live_lines}
color = {}
for r in routes.values():
    color.setdefault(r['route_short_name'], '#' + (r['route_color'] or 'C00808'))
trips = {t['trip_id']: (routes[t['route_id']]['route_short_name'], t['direction_id'], t['shape_id'], t['trip_headsign'])
         for t in rd('trips.txt') if t['route_id'] in routes}
print(len(routes), 'tram routes,', len(trips), 'trips,', len({v[0] for v in trips.values()}), 'lines', file=sys.stderr)

# ---- stop sequences (+ times of midday trips only, to keep memory small)
seq = defaultdict(list)
for r in rd('stop_times.txt'):
    if r['trip_id'] in trips:
        seq[r['trip_id']].append((int(r['stop_sequence']), sys.intern(r['stop_id']), sec(r['departure_time'])))
pat = defaultdict(Counter)
for tid, v in seq.items():
    v.sort()
    line, d = trips[tid][0], trips[tid][1]
    pat[(line, d)][tuple(x[1] for x in v)] += 1
master = {k: max(c, key=lambda s: (c[s], len(s))) for k, c in pat.items()}
mtrips = defaultdict(list)
for tid, v in seq.items():
    k = (trips[tid][0], trips[tid][1])
    if tuple(x[1] for x in v) == master[k]:
        mtrips[k].append(tid)
stops = {r['stop_id']: (r['stop_name'], float(r['stop_lat']), float(r['stop_lon'])) for r in rd('stops.txt')}
shape_ids = {Counter(trips[t][2] for t in v).most_common(1)[0][0] for v in mtrips.values()}
sh = defaultdict(list)
for r in rd('shapes.txt'):
    if r['shape_id'] in shape_ids:
        sh[r['shape_id']].append((int(r['shape_pt_sequence']), float(r['shape_pt_lat']), float(r['shape_pt_lon'])))

# ---- live candidates for all master stops
hp = [(int(r['StopID']), float(r['Latitude']), float(r['Longitude'])) for r in
      csv.DictReader(open('data/raw/wienerlinien-ogd-haltepunkte.csv'), delimiter=';') if r['Latitude']]
cand = set()
for k, ms in master.items():
    for sid in ms:
        _, la, lo = stops[sid]
        for rbl, a, b in hp:
            if abs(a - la) < 0.0012 and abs(b - lo) < 0.002 and m(la, lo, a, b) < 130:
                cand.add(rbl)
cand = sorted(cand)
print(len(cand), 'candidate haltepunkte', file=sys.stderr)


def fetch(ids):
    url = 'https://www.wienerlinien.at/ogd_realtime/monitor?' + '&'.join('stopId=%d' % x for x in ids)
    for attempt in range(6):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=H), timeout=40))
        except Exception as e:  # noqa: BLE001 - the API occasionally answers 403 under load; back off
            print('retry', attempt, e, file=sys.stderr)
            time.sleep(20 * (attempt + 1))
    raise SystemExit('API failed')


pool = defaultdict(list)    # rbl -> [{line, dir, lat, lon}]
for i in range(0, len(cand), 100):
    for mon in fetch(cand[i:i + 100])['data']['monitors']:
        rbl = mon['locationStop']['properties']['attributes']['rbl']
        c = mon['locationStop']['geometry']['coordinates']
        for l in mon['lines']:
            if l['name'] in live_lines:
                pool[rbl].append({'line': l['name'], 'dir': l['direction'], 'lat': c[1], 'lon': c[0]})
    time.sleep(1.5)


def map_rbl(line, D, lat, lon):
    best = None
    for rbl, es in pool.items():
        for e in es:
            if e['line'] == line and e['dir'] == D:
                dist = m(lat, lon, e['lat'], e['lon'])
                if dist < 130 and (best is None or dist < best[0]):
                    best = (dist, rbl)
    return best[1] if best else None


# ---- build every line
net = {'generated': time.strftime('%Y-%m-%d'), 'lines': {}}
report = []
for line in sorted({k[0] for k in master}, key=lambda x: (not x.isdigit(), int(x) if x.isdigit() else 0, x)):
    if (line, '0') not in master or (line, '1') not in master:
        report.append((line, 'skipped: only one direction in GTFS'))
        continue
    dirs = {}
    # GTFS direction 0/1 is not guaranteed to be H/R: use the assignment that finds more live RBLs
    score = {}
    for assign in (('H', 'R'), ('R', 'H')):
        n = 0
        for gd, D in zip(('0', '1'), assign):
            for sid in master[(line, gd)]:
                _, la, lo = stops[sid]
                n += map_rbl(line, D, la, lo) is not None
        score[assign] = n
    assign = max(score, key=score.get)
    for gd, D in zip(('0', '1'), assign):
        ms = master[(line, gd)]
        tr = mtrips[(line, gd)]
        rows = []
        for sid in ms:
            nm, la, lo = stops[sid]
            if rows and (nm == rows[-1]['name'] or m(la, lo, rows[-1]['lat'], rows[-1]['lon']) < 25):
                rbl = map_rbl(line, D, la, lo)
                if rbl and not rows[-1]['rbl']:
                    rows[-1]['rbl'] = rbl
                continue
            rows.append({'name': nm, 'lat': la, 'lon': lo, 'rbl': map_rbl(line, D, la, lo), 'sid': sid})
        shape_id = Counter(trips[t][2] for t in tr).most_common(1)[0][0]
        path = simplify([(a, b) for _, a, b in sorted(sh[shape_id])], 3.0)
        cum = [0.0]
        for i in range(1, len(path)):
            cum.append(cum[-1] + m(*path[i - 1], *path[i]))
        stations, s_prev = [], 0.0
        for r in rows:
            s = project(path, cum, r['lat'], r['lon'], s_prev)
            s_prev = s
            stations.append({'name': r['name'], 'rbl': r['rbl'], 'lat': r['lat'], 'lon': r['lon'], 's': round(s, 1), 'diva': 0, 'alt_rbls': []})
        # timetable guess: median departure difference of consecutive stations over midday trips
        idx = {r['sid']: i for i, r in enumerate(rows)}
        pos = {}   # sid -> index in master seq (first occurrence)
        for i, sid in enumerate(ms):
            pos.setdefault(sid, i)
        diffs = defaultdict(list)
        for t in tr[:400]:
            v = seq[t]
            if not 8 * 3600 <= v[0][2] <= 18 * 3600:
                continue
            tt = {x[1]: x[2] for x in v}
            for a, b in zip(rows, rows[1:]):
                if a['sid'] in tt and b['sid'] in tt:
                    diffs[a['sid']].append(tt[b['sid']] - tt[a['sid']])
        for i, st in enumerate(stations):
            if i < len(stations) - 1:
                dd = diffs.get(rows[i]['sid'])
                st['dep2dep'] = float(max(30.0, statistics.median(dd))) if dd else 60.0
            else:
                st['dep2dep'] = None
        dirs[D] = {'towards': trips[tr[0]][3].replace('Wien ', ''), 'dwell': DWELL, 'stations': stations,
                   'path': [[round(a, 6), round(b, 6)] for a, b in path], 'cum': [round(c, 1) for c in cum]}
    mapped = sum(1 for D in dirs.values() for s in D['stations'] if s['rbl'])
    total = sum(len(D['stations']) for D in dirs.values())
    if mapped < 0.5 * total:
        report.append((line, 'dropped: only %d/%d stops mapped' % (mapped, total)))
        continue
    # shared ids for the schematic view
    for k, s in enumerate(dirs['H']['stations']):
        s['diva'] = k
    for s in dirs['R']['stations']:
        s['diva'] = min(dirs['H']['stations'], key=lambda h: m(s['lat'], s['lon'], h['lat'], h['lon']))['diva']
    net['lines'][line] = {'color': '#C00808', 'dirs': dirs}
    report.append((line, 'ok %d/%d stops mapped, %d+%d stations' % (mapped, total, len(dirs['H']['stations']), len(dirs['R']['stations']))))

# ---- refine segment times with live samples
rbls = sorted({s['rbl'] for v in net['lines'].values() for D in v['dirs'].values() for s in D['stations'] if s['rbl']})
names = set(net['lines'])
acc = defaultdict(list)
for k in range(3):
    now = time.time()
    obs = {}
    for i in range(0, len(rbls), 100):
        for key, v in parse_monitor(fetch(rbls[i:i + 100]), lambda n: n in names).items():
            obs.setdefault(key, []).extend(v)
        time.sleep(1.5)
    for line, v in net['lines'].items():
        for D, td in v['dirs'].items():
            sts = td['stations']
            for a in range(len(sts) - 1):
                ra, rb = sts[a]['rbl'], sts[a + 1]['rbl']
                if ra and rb:
                    A = sorted(x[0] for x in obs.get((ra, line, D), []) if x[0] > now)
                    B = sorted(x[0] for x in obs.get((rb, line, D), []))
                    for ai, bj in align(A, B, sts[a]['dep2dep']):
                        acc[(line, D, a)].append(B[bj] - A[ai])
    if k < 2:
        time.sleep(25)
refined = 0
for line, v in net['lines'].items():
    for D, td in v['dirs'].items():
        for a, s in enumerate(td['stations'][:-1]):
            vals = acc.get((line, D, a), [])
            if len(vals) >= 4:
                med = statistics.median(vals)
                if abs(med - s['dep2dep']) <= 90:
                    s['dep2dep'] = float(med)
                    refined += 1
for ln, cc in palette(list(net['lines'])).items():
    net['lines'][ln]['color'] = cc
for line, msg in report:
    print('line', line.ljust(4), msg)
print('segments refined from live data:', refined)
json.dump(net, open('backend/data/network_tram.json', 'w'), ensure_ascii=False, separators=(',', ':'))
shutil.copy('backend/data/network_tram.json', 'web/network_tram.json')
print('rbls to poll:', len(rbls))
