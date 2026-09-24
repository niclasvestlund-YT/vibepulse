"""Prepare an unpacked Chrome extension and workspace-pinned local ingress.

This prepares files only. Chrome's Load unpacked action is a separate user step.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tokenserver'))
from state_files import state_dir, fsync_parent

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--workspace-name', required=True)
parser.add_argument('--port', type=int, default=8737)
args = parser.parse_args()
if not 1 <= args.port <= 65535 or not 1 <= len(args.workspace_name) <= 200:
    parser.error('invalid port or workspace name')
source = Path(__file__).resolve().parent
manifest = json.loads((source / 'manifest.json').read_text(encoding='utf-8'))
digest = hashlib.sha256(base64.b64decode(manifest['key'])).hexdigest()[:32]
extension_id = ''.join(chr(ord('a') + int(c, 16)) for c in digest)
destination = state_dir() / 'lovable-browser-extension'
destination.mkdir(parents=True, exist_ok=True)
for name in ('manifest.json', 'background.js', 'content.js', 'popup.html', 'popup.js'):
    shutil.copyfile(source / name, destination / name)
(destination / 'config.js').write_text(f'const VIBEPULSE_PORT = {args.port};\n', encoding='utf-8')
config = state_dir() / 'lovable-browser.json'
temp = config.with_suffix('.tmp')
fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, 'w', encoding='utf-8') as handle:
    json.dump({'extensionId': extension_id, 'workspace': args.workspace_name}, handle)
    handle.flush()
    os.fsync(handle.fileno())
os.replace(temp, config)
fsync_parent(config)
print('Prepared extension:', destination)
print('Extension ID:', extension_id)
print('Restart tokenserver, then load this directory in chrome://extensions.')
