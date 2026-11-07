#!/usr/bin/env python3
"""Verify every frozen payload file against the package SHA-256 manifest."""
import hashlib
from pathlib import Path

root = Path(__file__).resolve().parent
count = 0
for line in (root / 'SHA256SUMS').read_text().splitlines():
    expected, relative = line.split('  ', 1)
    path = root / relative
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    if h.hexdigest() != expected:
        raise SystemExit(f'FAILED: {relative}')
    count += 1
print(f'OK: {count} files match the frozen SHA-256 manifest.')
