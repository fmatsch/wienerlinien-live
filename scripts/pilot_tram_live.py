import json, sys, time, urllib.request
from collections import Counter
LINE = sys.argv[1] if len(sys.argv) > 1 else '1'
pat = json.load(open('data/raw/pilot_tram_%s.json' % LINE))
divas = sorted({d for v in pat.values() for d in v})
H = {'Accept': 'application/json', 'User-Agent': 'fmatsch-ubahn-livemap/1.0 (pilot)'}
rbls = {}
fields = Counter(); onstop = Counter(); real = Counter(); n = 0
sample = None
for i in range(0, len(divas), 25):
    url = 'https://www.wienerlinien.at/ogd_realtime/monitor?' + '&'.join('diva=%d' % d for d in divas[i:i + 25])
    d = json.load(urllib.request.urlopen(urllib.request.Request(url, headers=H), timeout=30))
    for m in d['data']['monitors']:
        rbl = m['locationStop']['properties']['attributes']['rbl']
        for l in m['lines']:
            if l['name'] != LINE:
                continue
            rbls.setdefault(rbl, []).append((l['direction'], l['towards'], m['locationStop']['properties']['name'], m['locationStop']['properties']['title']))
            for dep in l['departures'].get('departure', []):
                n += 1
                v = dep.get('vehicle') or {}
                for k in v: fields[k] += 1
                onstop[repr(v.get('onStop', 'absent'))] += 1
                real['real' if dep['departureTime'].get('timeReal') else 'plan only'] += 1
                if sample is None and v: sample = dep
    time.sleep(0.5)
print(len(divas), 'divas ->', len(rbls), 'rbls with line', LINE, '| departures', n)
print('vehicle fields:', dict(fields)); print('onStop:', dict(onstop)); print('time:', dict(real))
print(json.dumps(sample, ensure_ascii=False)[:600])
json.dump({str(k): v for k, v in rbls.items()}, open('data/raw/pilot_tram_%s_rbls.json' % LINE, 'w'), ensure_ascii=False)
