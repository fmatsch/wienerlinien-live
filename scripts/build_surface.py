"""Builds backend/data/network_<mode>.json for the surface modes ("tram" or "bus").

For every line: most common stop pattern per direction (GTFS), geometry (most common shape), stops mapped to live RBLs by
coordinates (GTFS stop ids are not reliably DIVA numbers), segment times from the timetable, refined with live samples
(forecasts are schedule-propagated, multiples of 30 s).

Gentle with the API: all live requests go through one limiter (IDS_PER_SEC), because the Wiener Linien answer
"Abfragelimit erreicht" (HTTP 403) when too many stopIds are asked in a short time. The live candidate answers are
cached in data/raw/<mode>_pool.json so a failed run can resume.

Run from the project root (needs data/gtfs, see scripts/setup_data.py):
    python3 -I scripts/build_surface.py tram
    python3 -I scripts/build_surface.py bus
"""
import array, colorsys, csv, json, os, shutil, statistics, sys, time, urllib.request
from collections import Counter, defaultdict
sys.path.insert(0, 'backend')
sys.path.insert(0, 'scripts')
from engine import align, parse_monitor
from build_network_lib import m, project, simplify

MODE = sys.argv[1] if len(sys.argv) > 1 else 'tram'
CFG = {
    'tram': {'route_type': '0', 'means': {'ptTram'}, 'samples': 3, 'min_vals': 4},
    'bus': {'route_type': '3', 'means': {'ptBusCity', 'ptBusNight'}, 'samples': 2, 'min_vals': 3},
}[MODE]
G = 'data/gtfs/'
DWELL = 15
IDS_PER_SEC = float(os.environ.get('BUILD_IDS_PER_SEC', '6'))
H = {'Accept': 'application/json', 'User-Agent': 'wienerlinien-live/1.0 (+https://fmatsch.github.io/wienerlinien-live/; network build)'}
rd = lambda f: csv.DictReader(open(G + f, encoding='utf-8-sig'))
sec = lambda s: int(s[:2]) * 3600 + int(s[3:5]) * 60 + int(s[6:8])


def palette(names):
    """Distinguishable colours per line (golden-angle hues); the official colour is the same for all lines of a mode."""
    out = {}
    for i, n in enumerate(names):
        h = (i * 137.508) % 360 / 360
        l = (0.38, 0.46, 0.32)[i % 3]
        r, g, b = colorsys.hls_to_rgb(h, l, 0.72)
        out[n] = '#%02X%02X%02X' % (round(r * 255), round(g * 255), round(b * 255))
    return out


# ---- which lines
live_lines = {r['LineText'] for r in csv.DictReader(open('data/raw/wienerlinien-ogd-linien.csv'), delimiter=';')
              if r['MeansOfTransport'] in CFG['means'] and r['Realtime'] == '1'}
ONLY = os.environ.get('BUILD_LINES')          # e.g. BUILD_LINES=13A,2A,48A for a quick subset while developing
if ONLY:
    live_lines &= set(ONLY.split(','))
routes = {r['route_id']: r for r in rd('routes.txt') if r['route_type'] == CFG['route_type'] and r['route_short_name'] in live_lines}
trips = {t['trip_id']: (routes[t['route_id']]['route_short_name'], t['direction_id'], t['shape_id'], t['trip_headsign'])
         for t in rd('trips.txt') if t['route_id'] in routes}
print(MODE, len(live_lines), 'live lines,', len(routes), 'routes,', len(trips), 'trips', file=sys.stderr)

# ---- stop sequences + midday departure times (compact: tuples of interned ids, array('i') of seconds)
seq, dep = {}, {}
cur, cur_ids, cur_t = None, [], array.array('i')
def flush():
    if cur is not None and cur in trips:
        seq[cur] = tuple(cur_ids)
        dep[cur] = cur_t
for r in rd('stop_times.txt'):
    tid = r['trip_id']
    if tid != cur:
        flush()
        cur, cur_ids, cur_t = tid, [], array.array('i')
    if tid in trips:
        cur_ids.append(sys.intern(r['stop_id']))
        cur_t.append(sec(r['departure_time']))
flush()
print(len(seq), 'trips with stop times', file=sys.stderr)

pat = defaultdict(Counter)
for tid, ids in seq.items():
    pat[(trips[tid][0], trips[tid][1])][ids] += 1
master = {k: max(c, key=lambda s: (c[s], len(s))) for k, c in pat.items()}
mtrips = defaultdict(list)
for tid, ids in seq.items():
    k = (trips[tid][0], trips[tid][1])
    if ids == master[k]:
        mtrips[k].append(tid)
stops = {r['stop_id']: (r['stop_name'], float(r['stop_lat']), float(r['stop_lon'])) for r in rd('stops.txt')}
shape_ids = {Counter(trips[t][2] for t in v).most_common(1)[0][0] for v in mtrips.values()}
sh = defaultdict(list)
for r in rd('shapes.txt'):
    if r['shape_id'] in shape_ids:
        sh[r['shape_id']].append((int(r['shape_pt_sequence']), float(r['shape_pt_lat']), float(r['shape_pt_lon'])))

# ---- live candidates for all master stops (Haltepunkt CSV, grid index)
hp = [(int(r['StopID']), float(r['Latitude']), float(r['Longitude'])) for r in
      csv.DictReader(open('data/raw/wienerlinien-ogd-haltepunkte.csv'), delimiter=';') if r['Latitude']]
cell = lambda la, lo: (round(la * 500), round(lo * 300))
grid = defaultdict(list)
for rbl, a, b in hp:
    grid[cell(a, b)].append((rbl, a, b))


def near(la, lo, radius=130):
    c = cell(la, lo)
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            for rbl, a, b in grid.get((c[0] + dx, c[1] + dy), ()):
                if m(la, lo, a, b) < radius:
                    yield rbl, a, b


cand = set()
for k, ms in master.items():
    for sid in ms:
        _, la, lo = stops[sid]
        cand.update(r for r, _, _ in near(la, lo))
cand = sorted(cand)
print(len(cand), 'candidate haltepunkte', file=sys.stderr)

_last = [0.0]
def fetch(ids):
    """Rate-limited monitor request (IDS_PER_SEC); backs off on 403 / code 316."""
    url = 'https://www.wienerlinien.at/ogd_realtime/monitor?' + '&'.join('stopId=%d' % x for x in ids)
    for attempt in range(8):
        wait = _last[0] - time.time()
        if wait > 0:
            time.sleep(wait)
        try:
            d = json.load(urllib.request.urlopen(urllib.request.Request(url, headers=H), timeout=60))
            _last[0] = time.time() + len(ids) / IDS_PER_SEC
            return d
        except Exception as e:  # noqa: BLE001
            print('retry', attempt, e, file=sys.stderr, flush=True)
            _last[0] = time.time() + 70 * (attempt + 1)
    raise SystemExit('API failed')


pool_file = 'data/raw/%s_pool%s.json' % (MODE, '_sub' if ONLY else '')
pool = defaultdict(list)    # rbl -> [{line, dir, lat, lon}]
if os.path.exists(pool_file):
    pool.update({int(k): v for k, v in json.load(open(pool_file)).items()})
    print('pool from cache:', len(pool), file=sys.stderr)
else:
    for i in range(0, len(cand), 100):
        for mon in fetch(cand[i:i + 100])['data']['monitors']:
            rbl = mon['locationStop']['properties']['attributes']['rbl']
            c = mon['locationStop']['geometry']['coordinates']
            for l in mon['lines']:
                if l['name'] in live_lines:
                    pool[rbl].append({'line': l['name'], 'dir': l['direction'], 'lat': c[1], 'lon': c[0]})
        if (i // 100) % 5 == 0:
            print('pool %d/%d' % (i, len(cand)), file=sys.stderr, flush=True)
    json.dump(pool, open(pool_file, 'w'))

pgrid = defaultdict(list)    # (line, dir, cell) -> [(rbl, lat, lon)]
for rbl, es in pool.items():
    for e in es:
        pgrid[(e['line'], e['dir'], cell(e['lat'], e['lon']))].append((rbl, e['lat'], e['lon']))


def map_rbl(line, D, lat, lon):
    best, c = None, cell(lat, lon)
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            for rbl, a, b in pgrid.get((line, D, (c[0] + dx, c[1] + dy)), ()):
                dist = m(lat, lon, a, b)
                if dist < 130 and (best is None or dist < best[0]):
                    best = (dist, rbl)
    return best[1] if best else None


# ---- build every line
net = {'generated': time.strftime('%Y-%m-%d'), 'lines': {}}
report = []
order = lambda x: (not x[0].isdigit(), int(''.join(ch for ch in x if ch.isdigit()) or 0), x)
for line in sorted({k[0] for k in master}, key=order):
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
        for i, sid in enumerate(ms):
            nm, la, lo = stops[sid]
            if rows and (nm == rows[-1]['name'] or m(la, lo, rows[-1]['lat'], rows[-1]['lon']) < 25):
                rbl = map_rbl(line, D, la, lo)
                if rbl and not rows[-1]['rbl']:
                    rows[-1]['rbl'] = rbl
                continue
            rows.append({'name': nm, 'lat': la, 'lon': lo, 'rbl': map_rbl(line, D, la, lo), 'idx': i})
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
        # timetable guess: median departure difference of consecutive kept stations over midday trips
        diffs = defaultdict(list)
        for t in tr[:300]:
            ts = dep[t]
            if not 8 * 3600 <= ts[0] <= 18 * 3600:
                continue
            for k, (a, b) in enumerate(zip(rows, rows[1:])):
                diffs[k].append(ts[b['idx']] - ts[a['idx']])
        for k, st in enumerate(stations):
            if k < len(stations) - 1:
                dd = diffs.get(k)
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
print('lines built:', len(net['lines']), file=sys.stderr, flush=True)

# ---- refine segment times with live samples
rbls = sorted({s['rbl'] for v in net['lines'].values() for D in v['dirs'].values() for s in D['stations'] if s['rbl']})
names = set(net['lines'])
acc = defaultdict(list)
for k in range(CFG['samples']):
    now = time.time()
    obs = {}
    for i in range(0, len(rbls), 100):
        for key, v in parse_monitor(fetch(rbls[i:i + 100]), lambda n: n in names).items():
            obs.setdefault(key, []).extend(v)
        if (i // 100) % 5 == 0:
            print('sample %d: %d/%d' % (k, i, len(rbls)), file=sys.stderr, flush=True)
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
refined = 0
for line, v in net['lines'].items():
    for D, td in v['dirs'].items():
        for a, s in enumerate(td['stations'][:-1]):
            vals = acc.get((line, D, a), [])
            if len(vals) >= CFG['min_vals']:
                med = statistics.median(vals)
                if abs(med - s['dep2dep']) <= 90:
                    s['dep2dep'] = float(med)
                    refined += 1
for ln, cc in palette(list(net['lines'])).items():
    net['lines'][ln]['color'] = cc
for line, msg in report:
    print('line', line.ljust(4), msg)
print('segments refined from live data:', refined)
out = 'backend/data/network_%s.json' % MODE
json.dump(net, open(out, 'w'), ensure_ascii=False, separators=(',', ':'))
shutil.copy(out, 'web/network_%s.json' % MODE)
print('rbls to poll:', len(rbls), '| file size:', os.path.getsize(out) // 1024, 'kB')
