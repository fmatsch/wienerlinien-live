"""Builds the night-bus lines on a machine that is up at night (e.g. a server, scheduled with `at 01:50`).

Downloads the static data into fresh directories, runs build_surface.py for the night lines only and merges them into
backend/data/network_bus.json.  Run from the repository root:   python3 scripts/run_night_build.py
"""
import os, subprocess, sys, time, zipfile

BASE = 'https://www.wienerlinien.at/ogd_realtime/doku/ogd/'
UA = {'User-Agent': 'wienerlinien-live/1.0 (+https://fmatsch.github.io/wienerlinien-live/; network build)'}
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(root)


def fetch(url, dest):
    """curl instead of urllib: the Wiener Linien firewall answers 429 to Python's urllib for the big GTFS file."""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    for attempt in range(6):
        print('download', url, '(attempt %d)' % (attempt + 1), flush=True)
        if subprocess.run(['curl', '-fsSL', '-A', UA['User-Agent'], '-o', dest, url]).returncode == 0:
            return
        time.sleep(60 * (attempt + 1))
    raise SystemExit('download failed: ' + url)


fetch(BASE + 'wienerlinien-ogd-haltepunkte.csv', 'data/raw/wienerlinien-ogd-haltepunkte.csv')
fetch(BASE + 'wienerlinien-ogd-linien.csv', 'data/raw/wienerlinien-ogd-linien.csv')
fetch('http://www.wienerlinien.at/ogd_realtime/doku/ogd/gtfs/gtfs.zip', 'data/gtfs/gtfs.zip')
with zipfile.ZipFile('data/gtfs/gtfs.zip') as z:
    z.extractall('data/gtfs')
env = dict(os.environ, BUILD_LINES='night', BUILD_MERGE='1', BUILD_POOL_CACHE='0')
sys.exit(subprocess.run([sys.executable, '-I', 'scripts/build_surface.py', 'bus'], env=env).returncode)
