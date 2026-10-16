"""Live map of the Vienna U-Bahn: polling proxy with cache + HTTP API.

Python standard library only (no pip install on the server).

    python3 backend/server.py                # http://127.0.0.1:4731, also serves ../web
    WL_INTERVAL=30 WL_RECORD_DIR=rec python3 backend/server.py

Endpoints
    /api/trains?mode=u|tram   current trains (default u)
    /api/meeting   U4 x U6 at Längenfeldgasse
    /api/network   line geometry and stations
    /api/debug     raw departures per station and derived trains
    /api/health    poller status
"""
import gzip
import json
import mimetypes
import os
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from engine import Engine, Network, meetings, parse_monitor

ROOT = Path(__file__).resolve().parent
WEB = ROOT.parent / 'web'
API = 'https://www.wienerlinien.at/ogd_realtime/monitor'
# WL_CONTACT: mail address or URL the Wiener Linien can reach you at if your polling ever causes trouble
UA = 'wienerlinien-live/1.0 (+https://fmatsch.github.io/wienerlinien-live/' + (('; ' + os.environ['WL_CONTACT']) if os.environ.get('WL_CONTACT') else '') + ')'

HOST = os.environ.get('WL_HOST', '127.0.0.1')
PORT = int(os.environ.get('WL_PORT', '4731'))
INTERVAL = max(15.0, float(os.environ.get('WL_INTERVAL', '30')))
IDLE_AFTER = float(os.environ.get('WL_IDLE_AFTER', '300'))   # stop polling when nobody asks for this long
BATCH = int(os.environ.get('WL_BATCH', '100'))               # stopIds per request (tested: 100 works)
RECORD_DIR = os.environ.get('WL_RECORD_DIR')
ALLOWED_ORIGINS = [o for o in os.environ.get(
    'WL_ORIGINS', 'https://fmatsch.ist,https://www.fmatsch.ist,http://localhost,http://127.0.0.1').split(',') if o]


def allowed_origin(origin):
    if not origin:
        return None
    for a in ALLOWED_ORIGINS:
        if origin == a or origin.startswith(a + ':'):
            return origin
    return None


# one network + poller per mode; the tram poller only runs while somebody looks at the tram view
MODES = {
    'u': {'file': 'network.json', 'interval': INTERVAL, 'pause': 0.4},
    'tram': {'file': 'network_tram.json', 'interval': max(INTERVAL, 60.0), 'pause': 0},
}


_api_lock = threading.Lock()
MIN_GAP = float(os.environ.get('WL_MIN_GAP', '1.0'))      # seconds between any two requests to the Wiener Linien API
# The API answers "Abfragelimit erreicht" (HTTP 403, code 316) when too many stopIds are asked in a short time. Measured:
# ~420 ids/min sustained is fine, ~500 ids within 12 s is not. One shared token bucket for all pollers.
RATE = float(os.environ.get('WL_IDS_PER_SEC', '6.5'))
BURST = float(os.environ.get('WL_BURST', '150'))
_bucket = {'tokens': BURST, 'ts': time.time(), 'next': 0.0}


def api_get(url, n_ids):
    """All pollers share one lane: requests are serialised and limited to RATE stopIds per second."""
    with _api_lock:
        while True:
            now = time.time()
            _bucket['tokens'] = min(BURST, _bucket['tokens'] + (now - _bucket['ts']) * RATE)
            _bucket['ts'] = now
            wait = max(_bucket['next'] - now, 0 if _bucket['tokens'] >= n_ids else (n_ids - _bucket['tokens']) / RATE)
            if wait <= 0:
                break
            time.sleep(wait)
        _bucket['tokens'] -= n_ids
        try:
            req = urllib.request.Request(url, headers={'Accept': 'application/json', 'User-Agent': UA})
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.load(r)
        finally:
            _bucket['next'] = time.time() + MIN_GAP


class Poller(threading.Thread):
    daemon = True

    def __init__(self, net, interval, wanted, mode='u', pause=0.4):
        super().__init__(name='poller-' + mode)
        self.mode = mode
        self.wanted = wanted
        self.pause = pause
        self.net = net
        self.engine = Engine(net)
        self.base_interval = interval
        self.interval = interval
        self.rbls = net.rbls()
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.last_request = 0.0
        self.line_demand = {}       # line -> last time a client asked for it (trams: poll only what is looked at)
        self.force = False
        self.cycle = 0.0            # duration of the last poll cycle (rate limit makes big networks slow)
        self.snapshot = None        # {'ts', 'trains', 'raw'}
        self.status = {'ok': None, 'message': 'starting', 'failures': 0, 'last_ok': None, 'polls': 0}

    # called by HTTP handlers
    def touch(self, lines=None):
        now = time.time()
        self.last_request = now
        if lines is not None:
            fresh = [l for l in lines if now - self.line_demand.get(l, 0) > IDLE_AFTER]
            for l in lines:
                self.line_demand[l] = now
            if fresh:
                self.force = True      # a line nobody was polling is wanted now
        self.wake.set()

    def demanded(self):
        now = time.time()
        if self.mode == 'u':
            return None
        names = self.net.tracks_lines()
        d = {l for l, t in self.line_demand.items() if now - t < IDLE_AFTER and l in names}
        return d or None if self.line_demand else None

    def get(self, wait=20.0, lines=None):
        self.touch(lines)
        end = time.time() + wait
        while self.snapshot is None and time.time() < end:
            time.sleep(0.1)
        with self.lock:
            return self.snapshot, dict(self.status)

    def fetch(self, lines=None):
        merged, raws = {}, []
        rbls = self.net.rbls(lines) if lines else self.rbls
        for i in range(0, len(rbls), BATCH):
            chunk = rbls[i:i + BATCH]
            url = API + '?' + '&'.join('stopId=%d' % x for x in chunk)
            try:
                payload = api_get(url, len(chunk))
            except urllib.error.URLError:     # the API sometimes answers 403/5xx for a single request: one retry
                time.sleep(5)
                payload = api_get(url, len(chunk))
            code = (payload.get('message') or {}).get('messageCode')
            if code != 1:
                raise RuntimeError('API messageCode %s (%s)' % (code, (payload.get('message') or {}).get('value')))
            raws.append(payload)
            for k, v in parse_monitor(payload, self.wanted).items():
                merged.setdefault(k, []).extend(v)
        for v in merged.values():
            v.sort()
        return merged, raws

    def poll_once(self):
        now = time.time()
        t_start = now
        lines = self.demanded()
        self.force = False
        merged, raws = self.fetch(lines)
        trains = self.engine.update(merged, now, lines)
        stations = {}
        for (rbl, line, d), deps in merged.items():
            key = self.net.rbl_index.get((rbl, line, d))
            if key:
                tk, i = key
                stations['%s/%s/%d' % (tk[0], tk[1], i)] = [
                    {'t': round(t, 1), 'in': round(t - now), 'real': real, 'towards': tw, 'onStop': on}
                    for t, tw, on, real in deps]
        if RECORD_DIR:
            os.makedirs(RECORD_DIR, exist_ok=True)
            with gzip.open(os.path.join(RECORD_DIR, '%s%d.json.gz' % ('' if self.mode == 'u' else self.mode + '-', now)), 'wt') as f:
                json.dump({'ts': now, 'responses': raws}, f)
        with self.lock:
            self.snapshot = {'ts': now, 'lines': sorted(lines) if lines else None, 'trains': trains, 'stations': stations,
                             'on_stop_seen': any(on for deps in merged.values() for _, _, on, _ in deps)}
            self.status.update(ok=True, message='ok', failures=0, last_ok=now, polls=self.status['polls'] + 1)
        self.cycle = time.time() - t_start
        self.interval = self.base_interval

    def run(self):
        while True:
            if time.time() - self.last_request > IDLE_AFTER:
                self.wake.clear()
                self.wake.wait()          # sleep until somebody asks
            started = time.time()
            try:
                self.poll_once()
            except Exception as e:  # noqa: BLE001 - keep the poller alive, back off
                with self.lock:
                    self.status['failures'] += 1
                    self.status.update(ok=False, message=str(e)[:200])
                    if self.snapshot and time.time() - self.snapshot['ts'] > 120:
                        self.snapshot = dict(self.snapshot, trains=[], stale=True)
                self.interval = min(max(self.interval * 2, 60.0), 600.0)
                print(time.strftime('%H:%M:%S'), self.mode, 'poll failed:', e, '-> next in', self.interval, 's', flush=True)
            end = time.time() + max(1.0, self.interval - (time.time() - started))
            while time.time() < end and not (self.force and time.time() - started > 10):
                time.sleep(0.5)


class Handler(BaseHTTPRequestHandler):
    server_version = 'wl-ubahn'
    pollers = {}
    network_json = {}

    def log_message(self, fmt, *args):
        if os.environ.get('WL_ACCESS_LOG'):
            super().log_message(fmt, *args)

    def send(self, code, body, ctype='application/json; charset=utf-8', cache='no-store'):
        origin = allowed_origin(self.headers.get('Origin'))
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', cache)
        if origin:
            self.send_header('Access-Control-Allow-Origin', origin)
            self.send_header('Vary', 'Origin')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def do_OPTIONS(self):
        self.send(204, b'')

    def do_GET(self):
        url = urlparse(self.path)
        path = url.path
        mode = (parse_qs(url.query).get('mode') or ['u'])[0]
        if mode not in self.pollers:
            return self.send(400, b'{"error":"unknown mode"}')
        p = self.pollers[mode]
        qlines = (parse_qs(url.query).get('lines') or [''])[0]
        qlines = [l for l in qlines.split(',') if l and len(l) <= 3][:40] or None
        if path == '/api/trains':
            snap, st = p.get(40.0 if mode == 'tram' else 20.0, qlines if mode == 'tram' else None)
            if snap is None:
                return self.send(503, json.dumps({'error': st['message'], 'status': st}).encode())
            age = time.time() - snap['ts']
            body = {'ts': snap['ts'], 'now': time.time(), 'age': round(age, 1), 'poll_interval': round(max(p.base_interval, p.cycle), 1),
                    'ok': st['ok'] and age < max(120, 3 * p.cycle), 'message': st['message'],
                    'loading': bool(qlines and snap.get('lines') is not None and not set(qlines) <= set(snap['lines'])),
                    'trains': [t for t in snap['trains'] if not qlines or t['line'] in qlines]}
            return self.send(200, json.dumps(body, ensure_ascii=False, separators=(',', ':')).encode())
        if path == '/api/meeting':
            p = self.pollers['u']
            snap, st = p.get()
            if snap is None:
                return self.send(503, json.dumps({'error': st['message']}).encode())
            age = time.time() - snap['ts']
            body = {'now': time.time(), 'age': round(age, 1), 'ok': bool(st['ok'] and age < 120),
                    'station': 'Längenfeldgasse',
                    'pairs': meetings(p.net, snap['trains'], 'U4', 'U6', 'Längenfeldgasse')}
            return self.send(200, json.dumps(body, ensure_ascii=False, separators=(',', ':')).encode())
        if path == '/api/network':
            return self.send(200, self.network_json[mode], cache='public, max-age=300')
        if path == '/api/debug':
            snap, st = p.get()
            body = {'status': st, 'snapshot': snap}
            return self.send(200, json.dumps(body, ensure_ascii=False, separators=(',', ':')).encode())
        if path == '/api/health':
            return self.send(200, json.dumps({m: {'status': q.status, 'idle': time.time() - q.last_request > IDLE_AFTER}
                                              for m, q in self.pollers.items()}).encode())
        # static files for local use
        rel = 'index.html' if path in ('/', '') else path.lstrip('/')
        f = (WEB / rel).resolve()
        if WEB.exists() and str(f).startswith(str(WEB.resolve())) and f.is_file():
            ctype = mimetypes.guess_type(f.name)[0] or 'application/octet-stream'
            return self.send(200, f.read_bytes(), ctype + ('; charset=utf-8' if ctype.startswith('text') or 'javascript' in ctype else ''),
                             cache='no-cache')
        self.send(404, b'{"error":"not found"}')


def main():
    nets = 0
    for mode, cfg in MODES.items():
        f = ROOT / 'data' / cfg['file']
        if not f.exists():
            continue
        raw = f.read_bytes()
        net = Network(json.loads(raw))
        Handler.network_json[mode] = raw
        names = set(net.tracks_lines())
        Handler.pollers[mode] = Poller(net, cfg['interval'], lambda n, names=names: n in names, mode, cfg['pause'])
        Handler.pollers[mode].start()
        nets += len(net.rbls())
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    print('U-Bahn/Tram live map on http://%s:%d  (poll every %ss, %d stops, modes: %s)' % (HOST, PORT, INTERVAL, nets, ', '.join(Handler.pollers)), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
