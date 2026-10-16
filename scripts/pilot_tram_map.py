"""Maps GTFS tram stops to live RBLs by coordinates (GTFS ids are not reliably DIVA numbers)."""
import csv, json, math, sys, time, urllib.request
LINE = sys.argv[1] if len(sys.argv) > 1 else '1'
pat = json.load(open('data/raw/pilot_tram_%s.json' % LINE))
hp = [(int(r['StopID']), r['StopText'], float(r['Latitude']), float(r['Longitude'])) for r in
      csv.DictReader(open('data/raw/wienerlinien-ogd-haltepunkte.csv'), delimiter=';') if r['Latitude']]
m = lambda a, b, c, d: math.hypot((c - a) * 111320, (d - b) * 111320 * math.cos(math.radians(a)))
cand = {}
for d, seq in pat.items():
    for s in seq:
        for sid, nm, la, lo in hp:
            if m(s['lat'], s['lon'], la, lo) < 130:
                cand[sid] = (nm, la, lo)
print(len(cand), 'candidate haltepunkte')
H = {'Accept': 'application/json', 'User-Agent': 'fmatsch-ubahn-livemap/1.0 (pilot)'}
ids = sorted(cand); rbls = {}
for i in range(0, len(ids), 100):
    url = 'https://www.wienerlinien.at/ogd_realtime/monitor?' + '&'.join('stopId=%d' % x for x in ids[i:i + 100])
    for mon in json.load(urllib.request.urlopen(urllib.request.Request(url, headers=H), timeout=30))['data']['monitors']:
        rbl = mon['locationStop']['properties']['attributes']['rbl']
        c = mon['locationStop']['geometry']['coordinates']
        for l in mon['lines']:
            if l['name'] == LINE:
                rbls.setdefault(rbl, []).append({'dir': l['direction'], 'towards': l['towards'], 'title': mon['locationStop']['properties']['title'], 'lat': c[1], 'lon': c[0], 'n': len(l['departures'].get('departure', []))})
    time.sleep(0.5)
res = {}
for gd, D in (('0', 'H'), ('1', 'R')):
    row = []
    for s in pat[gd]:
        best = None
        for rbl, es in rbls.items():
            for e in es:
                if e['dir'] == D:
                    dist = m(s['lat'], s['lon'], e['lat'], e['lon'])
                    if dist < 130 and (best is None or dist < best[0]):
                        best = (dist, rbl, e['title'])
        row.append({'name': s['name'], 'lat': s['lat'], 'lon': s['lon'], 'rbl': best[1] if best else None})
    res[D] = row
    print(D, len(row), 'stops,', sum(1 for r in row if r['rbl']), 'mapped; unmapped:', [r['name'] for r in row if not r['rbl']])
json.dump(res, open('data/raw/pilot_tram_%s_map.json' % LINE, 'w'), ensure_ascii=False)
