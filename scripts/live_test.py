import sys, json, time, urllib.request
sys.path.insert(0, 'backend')
from engine import Network, Engine, parse_monitor
net = Network(json.load(open('backend/data/network.json')))
H = {'Accept': 'application/json', 'User-Agent': 'fmatsch-ubahn-livemap/0.1'}
rbls = net.rbls()
obs = {}
raw = []
for i in range(0, len(rbls), 100):
    url = 'https://www.wienerlinien.at/ogd_realtime/monitor?' + '&'.join(f'stopId={x}' for x in rbls[i:i + 100])
    d = json.load(urllib.request.urlopen(urllib.request.Request(url, headers=H), timeout=30))
    raw.append(d)
    obs.update(parse_monitor(d))
now = time.time()
eng = Engine(net)
tr = eng.update(obs, now)
print(len(rbls), 'rbls', len(tr), 'trains')
from collections import Counter
print(Counter((t['line'], t['dir']) for t in tr))
for t in tr:
    if t['line'] == 'U4':
        print(t['id'], t['line'], t['dir'], t['towards'], t['prev'], '->', t['next'], t['s'], 'ghost' if t['ghost'] else '', (t['next_departure'] or {}).get('station'))
