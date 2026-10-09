"""Record installed source-built engine identity without mutating its code."""
import hashlib
import importlib.metadata
import json
from pathlib import Path
import subprocess
import sys
import jasmin.managers.listeners as listeners
import jasmin.protocols.smpp.protocol as protocol
import txamqp

for directory in ('/etc/jasmin/store', '/etc/jasmin/resource', '/var/log/jasmin'):
    Path(directory).mkdir(parents=True, exist_ok=True)
spec = Path(txamqp.__file__).parent / 'specs/standard/amqp0-9-1.stripped.xml'
Path('/etc/jasmin/resource/amqp0-9-1.xml').write_bytes(spec.read_bytes())
Path('/suite/build.json').write_text(json.dumps({
    'jasmin': importlib.metadata.version('jasmin'),
    'source_repository': 'convergia/jasmin',
    'source_commit': Path('/build/source-commit').read_text().strip(),
    'patches': ['No installed-package mutation; engine built from recorded checkout'],
    'listeners_sha256': hashlib.sha256(Path(listeners.__file__).read_bytes()).hexdigest(),
    'protocol_sha256': hashlib.sha256(Path(protocol.__file__).read_bytes()).hexdigest(),
    'python': sys.version.split()[0],
    'dependencies': subprocess.check_output([sys.executable, '-m', 'pip', 'freeze'], text=True).splitlines()
}, indent=2))
