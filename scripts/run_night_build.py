"""Builds the night-bus lines on a machine that is up at night (e.g. a server, scheduled with `at 01:50`).

Downloads the static data into fresh directories, runs build_surface.py for the night lines only and merges them into
backend/data/network_bus.json.  Run from the repository root:   python3 scripts/run_night_build.py
"""
import os, subprocess, sys, urllib.request, zipfile

BASE = 'https://www.wienerlinien.at/ogd_realtime/doku/ogd/'
UA = {'User-Agent': 'wienerlinien-live/1.0 (+https://fmatsch.github.io/wienerlinien-live/; network build)'}
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(root)


def fetch(url, dest):
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    print('download', url, flush=True)
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=300) as r, open(dest, 'wb') as f:
        while chunk := r.read(1 << 20):
            f.write(chunk)


fetch(BASE + 'wienerlinien-ogd-haltepunkte.csv', 'data/raw/wienerlinien-ogd-haltepunkte.csv')
fetch(BASE + 'wienerlinien-ogd-linien.csv', 'data/raw/wienerlinien-ogd-linien.csv')
fetch('http://www.wienerlinien.at/ogd_realtime/doku/ogd/gtfs/gtfs.zip', 'data/gtfs/gtfs.zip')
with zipfile.ZipFile('data/gtfs/gtfs.zip') as z:
    z.extractall('data/gtfs')
env = dict(os.environ, BUILD_LINES='night', BUILD_MERGE='1', BUILD_POOL_CACHE='0')
sys.exit(subprocess.run([sys.executable, '-I', 'scripts/build_surface.py', 'bus'], env=env).returncode)
