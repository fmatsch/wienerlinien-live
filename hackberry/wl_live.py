#!/usr/bin/env python3
"""Wiener Linien Live for the Hackberry Pi (and any Linux desktop with Chromium).

Serves the bundled web page on 127.0.0.1, opens it in a Chromium app window and keeps itself up to date:
every 6 hours (first check shortly after start) the latest GitHub release is checked; an update is
downloaded, verified against SHA256SUMS.txt and installed on request (button in the page).

Standard library only.
    wienerlinien-live              start the app
    wienerlinien-live --check      print whether an update is available
    wienerlinien-live --serve      only run the local server (no browser window)
"""
import hashlib
import io
import json
import mimetypes
import os
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
WEB = HERE / 'web'
REPO = 'fmatsch/wienerlinien-live'
PORT = 47234
API = os.environ.get('WL_API', 'https://www.depes.ch/wl-api')
CHECK_EVERY = 6 * 3600
ASSET = 'wienerlinien-live-hackberry.tar.gz'


def version():
    try:
        return (HERE / 'VERSION').read_text().strip()
    except OSError:
        return '0.0.0'


def is_newer(latest, current):
    def parts(v):
        out = []
        for p in str(v).lstrip('v').replace('-', '.').split('.')[:3]:
            out.append(int(p) if p.isdigit() else 0)
        return out + [0] * (3 - len(out))
    return parts(latest) > parts(current)


def http_get(url, timeout=30):
    req = urllib.request.Request(url, headers={'User-Agent': 'WienerLinienLive-hackberry/' + version(),
                                               'Accept': 'application/vnd.github+json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


class Updater:
    def __init__(self):
        self.status = {'state': 'idle'}
        self.lock = threading.Lock()
        self.release = None

    def set(self, **kw):
        with self.lock:
            self.status = kw

    def check(self, manual=False):
        if self.status.get('state') in ('downloading', 'ready'):
            return
        try:
            rel = json.loads(http_get('https://api.github.com/repos/%s/releases/latest' % REPO))
            latest = str(rel.get('tag_name', '')).lstrip('v')
            if latest and is_newer(latest, version()) and any(a['name'] == ASSET for a in rel.get('assets', [])):
                self.release = rel
                self.set(state='available', version=latest, page=rel.get('html_url'))
            else:
                self.set(state='current')
        except Exception as e:  # noqa: BLE001 - offline etc.: quiet in the background, visible when asked
            self.set(state='error', message=str(e)[:120]) if manual else self.set(state='idle')

    def install(self):
        """Downloads the release archive, verifies it and swaps it in. The page then offers a restart."""
        rel = self.release
        if not rel or self.status.get('state') != 'available':
            return
        latest = self.status['version']
        self.set(state='downloading', version=latest, percent=0)
        try:
            assets = {a['name']: a['browser_download_url'] for a in rel['assets']}
            blob = http_get(assets[ASSET], timeout=120)
            self.set(state='downloading', version=latest, percent=80)
            sums = http_get(assets['SHA256SUMS.txt']).decode()
            want = next(line.split()[0] for line in sums.splitlines() if line.strip().endswith(ASSET))
            if hashlib.sha256(blob).hexdigest() != want:
                raise RuntimeError('checksum mismatch')
            tmp = Path(tempfile.mkdtemp(prefix='wl-update-'))
            with tarfile.open(fileobj=io.BytesIO(blob)) as tf:
                for m in tf.getmembers():          # never trust paths from an archive
                    if m.name.startswith('/') or '..' in Path(m.name).parts:
                        raise RuntimeError('unsafe path in archive')
                tf.extractall(tmp)
            new = tmp / 'wienerlinien-live'
            if not (new / 'wl_live.py').exists() or not (new / 'web' / 'index.html').exists():
                raise RuntimeError('archive incomplete')
            backup = HERE.with_name(HERE.name + '.old')
            if backup.exists():
                shutil.rmtree(backup)
            os.replace(HERE, backup)
            try:
                shutil.copytree(new, HERE)
            except Exception:
                os.replace(backup, HERE)           # roll back
                raise
            shutil.rmtree(tmp, ignore_errors=True)
            self.set(state='ready', version=latest)
        except Exception as e:  # noqa: BLE001
            self.set(state='error', message=str(e)[:120])

    def loop(self):
        time.sleep(15)
        while True:
            self.check()
            time.sleep(CHECK_EVERY)


updater = Updater()
restart_requested = threading.Event()


class Handler(BaseHTTPRequestHandler):
    server_version = 'wl-live'

    def log_message(self, *a):
        pass

    def reply(self, code, body, ctype='application/json; charset=utf-8'):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        self.wfile.write(body)

    def app_json(self):
        return json.dumps({'app': 'hackberry', 'platform': 'hackberry', 'version': version(), 'update': updater.status}).encode()

    def do_POST(self):
        if self.path == '/_app/check':
            threading.Thread(target=updater.check, args=(True,), daemon=True).start()
        elif self.path == '/_app/install':
            if updater.status.get('state') == 'ready':
                restart_requested.set()
            else:
                threading.Thread(target=updater.install, daemon=True).start()
        else:
            return self.reply(404, b'{}')
        self.reply(200, self.app_json())

    def do_GET(self):
        path = self.path.split('?')[0]
        if path == '/_app.json':
            return self.reply(200, self.app_json())
        if path == '/config.js':
            return self.reply(200, ('window.WL_API = %s;\n' % json.dumps(API)).encode(), 'text/javascript; charset=utf-8')
        f = (WEB / ('index.html' if path in ('/', '') else path.lstrip('/'))).resolve()
        if str(f).startswith(str(WEB.resolve()) + os.sep) and f.is_file():
            ctype = mimetypes.guess_type(f.name)[0] or 'application/octet-stream'
            return self.reply(200, f.read_bytes(), ctype + ('; charset=utf-8' if ctype.startswith('text') or 'javascript' in ctype else ''))
        self.reply(404, b'{"error":"not found"}')


def port_in_use(port):
    with socket.socket() as s:
        return s.connect_ex(('127.0.0.1', port)) == 0


def find_browser(url):
    profile = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'wienerlinien-live' / 'chromium'
    for name in ('chromium', 'chromium-browser', 'google-chrome'):
        exe = shutil.which(name)
        if exe:
            # Wayland desktop (labwc on the Hackberry): Chromium falls back to X11 and dies unless told otherwise
            ozone = ['--ozone-platform=wayland'] if os.environ.get('WAYLAND_DISPLAY') and not os.environ.get('DISPLAY') else []
            return [exe, *ozone, '--app=' + url, '--user-data-dir=' + str(profile), '--start-maximized', '--no-first-run',
                    '--no-default-browser-check', '--password-store=basic', '--disable-features=Translate,TranslateUI,TranslateSubFrames', '--lang=de',
                    '--class=wienerlinien-live']
    exe = shutil.which('firefox') or shutil.which('firefox-esr')
    if exe:
        return [exe, '--new-window', url]
    return None


def main(argv):
    if '--version' in argv:
        print(version())
        return 0
    if '--check' in argv:
        updater.check(True)
        print(version(), '->', updater.status)
        return 0
    url = 'http://127.0.0.1:%d/' % PORT
    if port_in_use(PORT):                        # already running: just open another window
        cmd = find_browser(url)
        if cmd:
            subprocess.Popen(cmd)
        return 0
    srv = ThreadingHTTPServer(('127.0.0.1', PORT), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    threading.Thread(target=updater.loop, daemon=True).start()
    print('Wiener Linien Live %s on %s' % (version(), url), flush=True)
    if '--serve' in argv:
        while not restart_requested.is_set():
            time.sleep(1)
        os.execv(sys.executable, [sys.executable, str(HERE / 'wl_live.py')] + argv)
    cmd = find_browser(url)
    if not cmd:
        print('Kein Browser gefunden (chromium/firefox). Adresse manuell öffnen:', url)
        while not restart_requested.is_set():
            time.sleep(1)
    else:
        proc = subprocess.Popen(cmd)
        while proc.poll() is None and not restart_requested.is_set():
            time.sleep(1)
        if restart_requested.is_set():
            proc.terminate()
            proc.wait(timeout=10)
    if restart_requested.is_set():
        srv.shutdown()
        os.execv(sys.executable, [sys.executable, str(HERE / 'wl_live.py')] + argv)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
