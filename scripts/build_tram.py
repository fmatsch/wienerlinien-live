"""Builds backend/data/network_tram.json for tram line 1 (pilot).

GTFS gives the stop order, geometry and a minute-granular timetable; stops are mapped to live RBLs by coordinates
(scripts/pilot_tram_map.py). Segment times are refined with a few live samples (forecasts are schedule-propagated,
so they are exact multiples of 30 s).
Run:  python3 -I scripts/pilot_tram.py 1 && python3 -I scripts/pilot_tram_map.py 1 && python3 -I scripts/build_tram.py 1
"""
import csv, json, math, statistics, sys, time, urllib.request
from collections import Counter, defaultdict
sys.path.insert(0, 'backend')
sys.path.insert(0, 'scripts')
from engine import align, parse_monitor
from build_network_lib import m, project, simplify

LINE = sys.argv[1] if len(sys.argv) > 1 else '1'
COLOR = '#C00808'
DWELL = 15
G = 'data/gtfs/'
rd = lambda f: csv.DictReader(open(G + f, encoding='utf-8-sig'))
mp = json.load(open('data/raw/pilot_tram_%s_map.json' % LINE))
routes = {r['route_id'] for r in rd('routes.txt') if r['route_id'].startswith('22-%s-' % LINE) and r['route_short_name'] == LINE}
trips = {t['trip_id']: t for t in rd('trips.txt') if t['route_id'] in routes}
sec = lambda s: int(s[:2]) * 3600 + int(s[3:5]) * 60 + int(s[6:8])
st = defaultdict(list)
for r in rd('stop_times.txt'):
    if r['trip_id'] in trips:
        st[r['trip_id']].append((int(r['stop_sequence']), r['stop_id'], sec(r['arrival_time']), sec(r['departure_time'])))
pat = defaultdict(Counter)
for tid, t in trips.items():
    pat[t['direction_id']][tuple(s[1] for s in sorted(st[tid]))] += 1
shapes_needed = {t['shape_id'] for t in trips.values()}
sh = defaultdict(list)
for r in rd('shapes.txt'):
    if r['shape_id'] in shapes_needed:
        sh[r['shape_id']].append((int(r['shape_pt_sequence']), float(r['shape_pt_lat']), float(r['shape_pt_lon'])))

net = {'generated': time.strftime('%Y-%m-%d'), 'lines': {LINE: {'color': COLOR, 'dirs': {}}}}
for gd, D in (('0', 'H'), ('1', 'R')):
    master = max(pat[gd], key=lambda s: (pat[gd][s], len(s)))
    mtrips = sorted([tid for tid, t in trips.items() if t['direction_id'] == gd and tuple(s[1] for s in sorted(st[tid])) == master])
    shape_id = Counter(trips[t]['shape_id'] for t in mtrips).most_common(1)[0][0]
    path = simplify([(a, b) for _, a, b in sorted(sh[shape_id])], 3.0)
    cum = [0.0]
    for i in range(1, len(path)):
        cum.append(cum[-1] + m(*path[i - 1], *path[i]))
    rows = mp[D]
    assert len(rows) == len(master), (len(rows), len(master))
    # drop consecutive duplicates (terminal loops: arrival and departure platform listed as two stops)
    keep = []
    for i, r in enumerate(rows):
        if keep and (r['name'] == rows[keep[-1]]['name'] or m(r['lat'], r['lon'], rows[keep[-1]]['lat'], rows[keep[-1]]['lon']) < 25):
            if r['rbl'] and not rows[keep[-1]]['rbl']:
                keep[-1] = i
            continue
        keep.append(i)
    stations, s_prev = [], 0.0
    for i in keep:
        r = rows[i]
        s = project(path, cum, r['lat'], r['lon'], s_prev)
        s_prev = s
        stations.append({'name': r['name'], 'rbl': int(r['rbl']) if r['rbl'] else None, 'lat': r['lat'], 'lon': r['lon'], 's': round(s, 1),
                         'diva': 0, 'alt_rbls': []})
    # GTFS guess of departure->departure per consecutive kept stops (minute granular)
    guess = []
    for a, b in zip(keep, keep[1:]):
        v = [st[t][b][3] - st[t][a][3] for t in sorted(mtrips) if 7 * 3600 <= st[t][a][3] <= 21 * 3600] or [60]
        guess.append(max(30.0, statistics.median(v)))
    for k, s in enumerate(stations):
        s['dep2dep'] = guess[k] if k < len(guess) else None
        s['diva'] = k
    net['lines'][LINE]['dirs'][D] = {'towards': trips[mtrips[0]]['trip_headsign'].replace('Wien ', ''), 'dwell': DWELL,
        'stations': stations, 'path': [[round(a, 6), round(b, 6)] for a, b in path], 'cum': [round(c, 1) for c in cum]}
    print(D, len(stations), 'stations', sum(1 for s in stations if s['rbl']), 'with rbl; length', round(cum[-1]), 'm')

# refine with live samples
H = {'Accept': 'application/json', 'User-Agent': 'fmatsch-ubahn-livemap/1.0 (calibration)'}
rbls = sorted({s['rbl'] for D in net['lines'][LINE]['dirs'].values() for s in D['stations'] if s['rbl']})
diffs = defaultdict(list)
for k in range(4):
    now = time.time()
    obs = {}
    for i in range(0, len(rbls), 100):
        url = 'https://www.wienerlinien.at/ogd_realtime/monitor?' + '&'.join('stopId=%d' % r for r in rbls[i:i + 100])
        for key, v in parse_monitor(json.load(urllib.request.urlopen(urllib.request.Request(url, headers=H), timeout=30)), lambda n: n == LINE).items():
            obs.setdefault(key, []).extend(v)
    for D, td in net['lines'][LINE]['dirs'].items():
        sts = td['stations']
        for a in range(len(sts) - 1):
            ra, rb = sts[a]['rbl'], sts[a + 1]['rbl']
            if not (ra and rb):
                continue
            A = sorted(x[0] for x in obs.get((ra, LINE, D), []) if x[0] > now)
            B = sorted(x[0] for x in obs.get((rb, LINE, D), []))
            for ai, bj in align(A, B, sts[a]['dep2dep']):
                diffs[(D, a)].append(B[bj] - A[ai])
    if k < 3:
        time.sleep(20)
used = 0
for D, td in net['lines'][LINE]['dirs'].items():
    for a, s in enumerate(td['stations'][:-1]):
        v = diffs.get((D, a), [])
        if len(v) >= 6:
            med = statistics.median(v)
            if abs(med - s['dep2dep']) <= 90:
                s['dep2dep'] = float(med); used += 1
print('segments refined from live data:', used)
def assign_ids(net, line):
    """Give stations of both directions a shared id (the schematic view places R trains by the H station order)."""
    d = net['lines'][line]['dirs']
    for k, s in enumerate(d['H']['stations']):
        s['diva'] = k
    for s in d['R']['stations']:
        best = min(d['H']['stations'], key=lambda h: m(s['lat'], s['lon'], h['lat'], h['lon']))
        s['diva'] = best['diva']

assign_ids(net, LINE)
json.dump(net, open('backend/data/network_tram.json', 'w'), ensure_ascii=False, separators=(',', ':'))
import shutil; shutil.copy('backend/data/network_tram.json', 'web/network_tram.json')
