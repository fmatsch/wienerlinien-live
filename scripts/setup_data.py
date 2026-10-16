"""Rebuilds backend/data/network.json from scratch (e.g. after a GTFS update).

1. downloads the GTFS feed + static CSVs (CC BY 4.0, Stadt Wien / Wiener Linien)
2. extracts the U-Bahn subset, scans the live API for RBLs, builds the network.
Run from the project root:  make setup
"""
import os, subprocess, sys, urllib.request, zipfile

GTFS = 'http://www.wienerlinien.at/ogd_realtime/doku/ogd/gtfs/gtfs.zip'
CSV = 'https://www.wienerlinien.at/ogd_realtime/doku/ogd/wienerlinien-ogd-haltepunkte.csv'
HERE = os.path.dirname(os.path.abspath(__file__))

def fetch(url, dest):
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    print('download', url)
    req = urllib.request.Request(url, headers={'User-Agent': 'fmatsch-ubahn-livemap/1.0'})
    with urllib.request.urlopen(req, timeout=300) as r, open(dest, 'wb') as f:
        while chunk := r.read(1 << 20):
            f.write(chunk)

fetch(CSV, 'data/raw/wienerlinien-ogd-haltepunkte.csv')
fetch(CSV.replace('haltepunkte', 'linien'), 'data/raw/wienerlinien-ogd-linien.csv')
fetch(GTFS, 'data/gtfs/gtfs.zip')
with zipfile.ZipFile('data/gtfs/gtfs.zip') as z:
    z.extractall('data/gtfs')
for script in ('scan_ubahn.py', 'extract_gtfs.py', 'build_network.py', 'build_tram_all.py'):
    print('run', script)
    subprocess.run([sys.executable, '-I', os.path.join(HERE, script)] + (['100'] if script == 'scan_ubahn.py' else []), check=True)
print('done -> backend/data/network*.json, web/network*.json')
