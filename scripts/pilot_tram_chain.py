"""Samples tram line departures several times, chains neighbouring stops and reports how stable the
stop-to-stop times are (this decides whether position estimation is accurate enough)."""
import json, sys, time, statistics, urllib.request
sys.path.insert(0, 'backend')
from engine import align, parse_monitor
LINE = sys.argv[1] if len(sys.argv) > 1 else '1'
N = int(sys.argv[2]) if len(sys.argv) > 2 else 6
mp = json.load(open('data/raw/pilot_tram_%s_map.json' % LINE))
H = {'Accept': 'application/json', 'User-Agent': 'fmatsch-ubahn-livemap/1.0 (pilot)'}
order = {D: ([r['name'] for r in rows], [int(r['rbl']) if r['rbl'] else None for r in rows]) for D, rows in mp.items()}
allr = sorted({r for _, st in order.values() for r in st if r})
samples = {'H': [], 'R': []}
for k in range(N):
    obs = {}
    now = time.time()
    for i in range(0, len(allr), 100):
        url = 'https://www.wienerlinien.at/ogd_realtime/monitor?' + '&'.join('stopId=%d' % r for r in allr[i:i + 100])
        for key, v in parse_monitor(json.load(urllib.request.urlopen(urllib.request.Request(url, headers=H), timeout=30)), lambda n: n == LINE).items():
            obs.setdefault(key, []).extend(v)
    for D, (seq, st) in order.items():
        deps = {}
        for i, r in enumerate(st):
            if r and (r, LINE, D) in obs:
                deps[i] = [d[0] for d in obs[(r, LINE, D)] if d[0] > now - 30]
        samples[D].append((now, deps))
    if k < N - 1:
        time.sleep(20)
for D in 'HR':
    seg = {}
    for now, deps in samples[D]:
        idx = sorted(i for i in deps if deps[i])
        for a, b in zip(idx, idx[1:]):
            if b != a + 1:
                continue
            # expected delta from the median of first-vs-first (rough): use pairs by alignment with generous tolerance
            A, B = deps[a], deps[b]
            guess = statistics.median([y - x for x, y in zip(A[:3], B[:3])]) if A and B else 0
            for ai, bj in align(A, B, guess):   # tolerance of align() = 110 s
                seg.setdefault(a, []).append(B[bj] - A[ai])
    spreads = []
    for a, v in sorted(seg.items()):
        if len(v) >= 4:
            med = statistics.median(v)
            mad = statistics.median([abs(x - med) for x in v])
            spreads.append((a, round(med), round(mad), len(v)))
    print(D, 'segments measured', len(spreads), 'median dep->dep [s] / MAD [s] / n:')
    print('  ', spreads[:40])
    print('   overall median MAD:', statistics.median([s[2] for s in spreads]) if spreads else None)
