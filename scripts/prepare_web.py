"""Copies web/ to a target folder for the desktop wrappers: remote API in config.js, no dev leftovers.

    python3 scripts/prepare_web.py <target>        e.g. app/web, dist/hackberry/web
"""
import os, shutil, sys

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
target = os.path.abspath(sys.argv[1])
if os.path.isdir(target):
    shutil.rmtree(target)
shutil.copytree(os.path.join(root, 'web'), target)
with open(os.path.join(target, 'config.js'), 'w') as f:
    f.write("// Overridden by the wrapper's local server; this value is only the fallback.\nwindow.WL_API = 'https://www.depes.ch/wl-api';\n")
print('web ->', target)
