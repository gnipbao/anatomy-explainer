#!/usr/bin/env python3
"""Offline integrity and behavioral checks; no cloud synthesis or browser launch."""
from pathlib import Path
import hashlib
import json
import subprocess
import sys
ROOT=Path(__file__).resolve().parents[1]
lock=json.loads((ROOT/'upstream.lock.json').read_text())
for relative,digest in lock['files'].items():
    path=ROOT/'vendor/anatomy'/relative
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=digest:
        raise SystemExit(f'Upstream integrity failure: {relative}')
print(f'Upstream integrity passed ({len(lock["files"])} files).',flush=True)
commands=[
    [sys.executable,'-m','unittest','discover','-s',str(ROOT/'tests'),'-p','test_*.py'],
    ['node','--test',*[str(p) for p in sorted((ROOT/'tests').glob('*.test.mjs'))]],
]
for command in commands:
    result=subprocess.run(command,cwd=ROOT)
    if result.returncode: raise SystemExit(result.returncode)
print('Offline checks passed. Cloud TTS, source truth and full visual review need separate evidence.')
