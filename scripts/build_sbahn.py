"""Builds the S-Bahn network and timetable from the ÖBB GTFS timetable (data.oebb.at, CC BY 4.0).

There is no open real-time feed for the S-Bahn, so the S-Bahn mode shows positions from the *timetable only*.

Output
    backend/data/network_sbahn.json     lines, stations, geometry (same format as the other networks, "schedule": true)
    backend/data/timetable_sbahn.json   trips (stop positions along the line + times) and service calendars
    web/network_sbahn.json              copy of the network for the apps / static page

Usage (the GTFS directory is the unzipped ÖBB feed; run with -I, it is untrusted data):
    python3 -I scripts/build_sbahn.py data/oebb/gtfs/GTFS_Fahrplan_2026
"""
import colorsys, csv, json, math, os, shutil, sys, time
from collections import Counter, defaultdict
sys.path.insert(0, 'scripts')
from build_network_lib import m, simplify

G = sys.argv[1].rstrip('/') + '/'
VIENNA_LINES = {'S1', 'S2', 'S3', 'S4', 'S7', 'S40', 'S45', 'S50', 'S60', 'S80'}
BBOX = (47.60, 48.95, 15.55, 17.20)         # lat min/max, lon min/max: the Vienna region
MAX_OFF_PATH = 2000.0                        # m: stops further from the line's path are ignored for that trip
rd = lambda f: csv.DictReader(open(G + f, encoding='utf-8-sig'))
inside = lambda la, lo: BBOX[0] <= la <= BBOX[1] and BBOX[2] <= lo <= BBOX[3]
sec = lambda s: int(s[:2]) * 3600 + int(s[3:5]) * 60 + int(s[6:8])


def palette(names):
    out = {}
    for i, n in enumerate(names):
        h = (0.52 + 0.075 * i) % 1.0          # blues to greens to purples: reads as "S-Bahn", lines stay distinguishable
        l = (0.36, 0.44, 0.30)[i % 3]
        r, g, b = colorsys.hls_to_rgb(h, l, 0.70)
        out[n] = '#%02X%02X%02X' % (round(r * 255), round(g * 255), round(b * 255))
    return out


routes = {r['route_id']: r['route_short_name'] for r in rd('routes.txt')
          if r['route_short_name'] in VIENNA_LINES and r['agency_id'] == '01'}
trips = {t['trip_id']: t for t in rd('trips.txt') if t['route_id'] in routes}
stops = {r['stop_id']: (r['stop_name'], float(r['stop_lat']), float(r['stop_lon'])) for r in rd('stops.txt')}
st = defaultdict(list)
for r in rd('stop_times.txt'):
    if r['trip_id'] in trips:
        st[r['trip_id']].append((int(r['stop_sequence']), r['stop_id'], sec(r['arrival_time']), sec(r['departure_time'])))
print(len(trips), 'candidate trips on', len(set(routes.values())), 'lines', file=sys.stderr)

# keep trips that mostly run in the Vienna region
kept = {}
for tid, v in st.items():
    v.sort()
    ins = [x for x in v if inside(stops[x[1]][1], stops[x[1]][2])]
    if len(ins) >= 3 and len(ins) >= 0.6 * len(v):
        kept[tid] = ins
print(len(kept), 'trips in the Vienna region', file=sys.stderr)
by_line = defaultdict(list)
for tid in kept:
    by_line[routes[trips[tid]['route_id']]].append(tid)

# geometry: the most common shape per line, clipped to the region
need = {trips[t]['shape_id'] for ids in by_line.values() for t in ids}
sh = defaultdict(list)
for r in rd('shapes.txt'):
    if r['shape_id'] in need:
        sh[r['shape_id']].append((int(r['shape_pt_sequence']), float(r['shape_pt_lat']), float(r['shape_pt_lon'])))


def clip(points):
    """Longest contiguous run of points inside the region."""
    best, cur = [], []
    for p in points:
        if inside(*p):
            cur.append(p)
        else:
            best = max(best, cur, key=len)
            cur = []
    return max(best, cur, key=len)


def project_free(path, cum, lat, lon):
    """Arc length of the point on the polyline nearest to (lat, lon) and its distance in metres."""
    best = (1e18, 0.0)
    for i in range(len(path) - 1):
        (a, b), (c, d) = path[i], path[i + 1]
        kx = 111320.0 * math.cos(math.radians(a))
        bx, by = (d - b) * kx, (c - a) * 111320.0
        px, py = (lon - b) * kx, (lat - a) * 111320.0
        L2 = bx * bx + by * by
        t = 0 if L2 == 0 else max(0, min(1, (px * bx + py * by) / L2))
        dist = math.hypot(px - t * bx, py - t * by)
        if dist < best[0]:
            best = (dist, cum[i] + t * (cum[i + 1] - cum[i]))
    return best[1], best[0]


net = {'generated': time.strftime('%Y-%m-%d'), 'schedule': True, 'lines': {}}
trip_out, report = [], []
services_needed = set()
for line in sorted(by_line, key=lambda x: int(x[1:])):
    ids = by_line[line]
    # base geometry: the longest (clipped) shape that several trips use - short-turn variants would hide most of the line
    usage = Counter(trips[t]['shape_id'] for t in ids)
    def extent(sid):
        pts = clip([(a, b) for _, a, b in sorted(sh[sid])])
        return sum(m(*pts[i - 1], *pts[i]) for i in range(1, len(pts)))
    cands = [sid for sid, n in usage.items() if n >= 3] or list(usage)
    shape_id = max(cands, key=extent)
    path = simplify(clip([(a, b) for _, a, b in sorted(sh[shape_id])]), 4.0)
    if len(path) < 2:
        report.append((line, 'dropped: no geometry in the region'))
        continue
    cum = [0.0]
    for i in range(1, len(path)):
        cum.append(cum[-1] + m(*path[i - 1], *path[i]))
    total = cum[-1]
    proj = {}
    def at(stop_id):
        if stop_id not in proj:
            proj[stop_id] = project_free(path, cum, stops[stop_id][1], stops[stop_id][2])
        return proj[stop_id]
    # per trip: stop positions along the path, direction by the sign of progress
    recs = []
    for tid in ids:
        pts = [(at(sid)[0], a, d, sid) for _, sid, a, d in kept[tid] if at(sid)[1] <= MAX_OFF_PATH]
        if len(pts) < 2:
            continue
        direction = 'H' if pts[-1][0] >= pts[0][0] else 'R'
        if direction == 'R':
            pts = [(total - s, a, d, sid) for s, a, d, sid in pts]
        mono, last = [], -1.0
        for p in pts:                              # keep progress monotone (loops or detours would otherwise zig-zag)
            if p[0] >= last - 1.0:
                mono.append(p)
                last = p[0]
        if len(mono) < 2:
            continue
        recs.append((tid, direction, mono))
    dirs = {}
    for D in ('H', 'R'):
        mine = [r for r in recs if r[1] == D]
        if not mine:
            continue
        # stations: stops of the most common stop pattern of this direction, ordered along the track
        pos = {}
        for r in mine:
            for p in r[2]:
                pos.setdefault(stops[p[3]][0], []).append((p[0], p[3]))      # by station name: platforms of one station merge
        sts = []
        for nm, lst in pos.items():
            s_med = sorted(x[0] for x in lst)[len(lst) // 2]
            sid = min(lst, key=lambda x: abs(x[0] - s_med))[1]
            sts.append({'name': nm, 'rbl': None, 'lat': stops[sid][1], 'lon': stops[sid][2], 's': round(s_med, 1), 'diva': 0, 'alt_rbls': [], 'dep2dep': None})
        sts.sort(key=lambda x: x['s'])
        towards = Counter(trips[r[0]]['trip_headsign'] for r in mine).most_common(1)[0][0]
        pp = path if D == 'H' else list(reversed(path))
        cc = cum if D == 'H' else [round(total - c, 1) for c in reversed(cum)]
        dirs[D] = {'towards': towards, 'dwell': 0, 'stations': sts, 'path': [[round(a, 6), round(b, 6)] for a, b in pp],
                   'cum': [round(c, 1) for c in cc], 'n_trips': len(mine)}
        for tid, direction, mono in mine:
            trips_t = trips[tid]
            services_needed.add(trips_t['service_id'])
            trip_out.append({'l': line, 'd': D, 'svc': trips_t['service_id'], 'h': trips_t['trip_headsign'],
                             'n': trips_t['trip_short_name'], 'p': [[round(p[0], 1), p[1], p[2]] for p in mono]})
    if set(dirs) != {'H', 'R'}:
        report.append((line, 'dropped: only one direction found (%s)' % ','.join(dirs)))
        continue
    for k, s in enumerate(dirs['H']['stations']):
        s['diva'] = k
    for s in dirs['R']['stations']:
        same = [h for h in dirs['H']['stations'] if h['name'] == s['name']]
        s['diva'] = same[0]['diva'] if same else min(dirs['H']['stations'], key=lambda h: m(s['lat'], s['lon'], h['lat'], h['lon']))['diva']
    net['lines'][line] = {'color': '#0076BD', 'dirs': dirs}
    full = sum(1 for r in recs if len(r[2]) >= 0.8 * len(kept[r[0]]))      # trips that stay on the base path almost completely
    report.append((line, 'ok: %d/%d trips fully on the path (%d H / %d R), %d+%d stations, %.0f km, %s' % (
        full, len(ids), dirs['H']['n_trips'], dirs['R']['n_trips'], len(dirs['H']['stations']), len(dirs['R']['stations']), total / 1000,
        dirs['H']['stations'][0]['name'] + ' - ' + dirs['H']['stations'][-1]['name'])))
for ln, cc in palette(list(net['lines'])).items():
    net['lines'][ln]['color'] = cc

# service calendars (weekday flags, validity, exceptions) of the services in use
services = {}
for r in rd('calendar.txt'):
    if r['service_id'] in services_needed:
        services[r['service_id']] = {'d': ''.join(r[k] for k in ('monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday')),
                                     'a': int(r['start_date']), 'b': int(r['end_date']), 'add': [], 'rem': []}
for r in rd('calendar_dates.txt'):
    if r['service_id'] in services:
        services[r['service_id']]['add' if r['exception_type'] == '1' else 'rem'].append(int(r['date']))
valid = (min(s['a'] for s in services.values()), max(s['b'] for s in services.values()))

for line, msg in report:
    print('line', line.ljust(4), msg)
timetable = {'generated': net['generated'], 'valid_from': valid[0], 'valid_to': valid[1], 'services': services, 'trips': trip_out}
os.makedirs('backend/data', exist_ok=True)
json.dump(net, open('backend/data/network_sbahn.json', 'w'), ensure_ascii=False, separators=(',', ':'))
json.dump(timetable, open('backend/data/timetable_sbahn.json', 'w'), ensure_ascii=False, separators=(',', ':'))
if os.path.isdir('web'):
    shutil.copy('backend/data/network_sbahn.json', 'web/network_sbahn.json')
print('trips:', len(trip_out), '| network %d kB, timetable %d kB | valid %d-%d' % (
    os.path.getsize('backend/data/network_sbahn.json') // 1024, os.path.getsize('backend/data/timetable_sbahn.json') // 1024, *valid))
