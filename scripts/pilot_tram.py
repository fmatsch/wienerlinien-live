"""Pilot: tram line 1 - GTFS pattern, live API check (onStop, timeReal), chain consistency."""
import csv, json, sys, time, urllib.request, statistics
from collections import Counter, defaultdict
G = 'data/gtfs/'
LINE = sys.argv[1] if len(sys.argv) > 1 else '1'
rd = lambda f: csv.DictReader(open(G + f, encoding='utf-8-sig'))
routes = {r['route_id'] for r in rd('routes.txt') if r['route_id'].startswith('22-%s-' % LINE) and r['route_short_name'] == LINE}
trips = {t['trip_id']: t for t in rd('trips.txt') if t['route_id'] in routes}
st = defaultdict(list)
for r in rd('stop_times.txt'):
    if r['trip_id'] in trips:
        st[r['trip_id']].append((int(r['stop_sequence']), r['stop_id']))
stops = {r['stop_id']: r for r in rd('stops.txt') if r['stop_id'].startswith('at:49:')}
pat = defaultdict(Counter)
for tid, t in trips.items():
    seq = tuple(s for _, s in sorted(st[tid]))
    pat[t['direction_id']][seq] += 1
out = {}
for d, c in sorted(pat.items()):
    master = max(c, key=lambda s: (c[s], len(s)))
    print('dir', d, 'patterns', len(c), 'master', len(master), stops[master[0]]['stop_name'], '->', stops[master[-1]]['stop_name'], 'trips', sum(c.values()))
    out[d] = [{'diva': 60200000 + int(s.split(':')[2]), 'name': stops[s]['stop_name'], 'lat': float(stops[s]['stop_lat']), 'lon': float(stops[s]['stop_lon'])} for s in master]
json.dump(out, open('data/raw/pilot_tram_%s.json' % LINE, 'w'))
