"""Builds dist/wienerlinien-live-hackberry.tar.gz (launcher + web + installer) and prints its sha256.

    python3 scripts/package_hackberry.py [outdir]
"""
import hashlib, json, os, shutil, subprocess, sys, tarfile, tempfile

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
out = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(root, 'dist'))
os.makedirs(out, exist_ok=True)
version = json.load(open(os.path.join(root, 'app', 'package.json')))['version']
tmp = tempfile.mkdtemp()
pkg = os.path.join(tmp, 'wienerlinien-live')
os.makedirs(pkg)
subprocess.run([sys.executable, os.path.join(root, 'scripts', 'prepare_web.py'), os.path.join(pkg, 'web')], check=True, stdout=subprocess.DEVNULL)
for f in ('wl_live.py', 'install.sh'):
    shutil.copy(os.path.join(root, 'hackberry', f), pkg)
shutil.copy(os.path.join(root, 'assets', 'icon-256.png'), os.path.join(pkg, 'icon.png'))
open(os.path.join(pkg, 'VERSION'), 'w').write(version + '\n')
target = os.path.join(out, 'wienerlinien-live-hackberry.tar.gz')
with tarfile.open(target, 'w:gz') as tf:
    tf.add(pkg, arcname='wienerlinien-live')
print(target, version, hashlib.sha256(open(target, 'rb').read()).hexdigest())
