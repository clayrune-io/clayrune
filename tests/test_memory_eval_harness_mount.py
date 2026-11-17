"""MC-1179 TL29: _harness.wire mounts a corpus snapshot under the project path it was given.

Run in a subprocess: wire() re-initialises mc.memory's module state, which must
not leak into the rest of the test session.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

_SCRIPT = r'''
import sys
sys.path.insert(0, sys.argv[1])
import _harness
m, project = _harness.wire(corpus_snapshot=sys.argv[2], project_path=sys.argv[3])
mp = m._get_memory_path(project)
print('EXISTS' if mp.exists() else 'MISSING')
_harness.cleanup()
'''


def test_snapshot_and_project_path_together_find_the_snapshot(tmp_path):
    snap = tmp_path / 'snap' / 'memory'
    snap.mkdir(parents=True)
    (snap / 'MEMORY.md').write_text('# idx\n', encoding='utf-8')
    other = tmp_path / 'some-other-checkout'
    other.mkdir()
    # A data root whose one project record points at that other checkout.
    pdir = tmp_path / 'droot' / 'data' / 'projects'
    pdir.mkdir(parents=True)
    (pdir / 'zz.json').write_text(json.dumps({'id': 'zz', 'project_path': str(other)}), encoding='utf-8')
    r = subprocess.run([sys.executable, '-c', _SCRIPT, str(ROOT / 'tools' / 'memory-eval'),
                        str(tmp_path / 'snap'), str(other)],
                       capture_output=True, text=True, timeout=120, cwd=str(ROOT),
                       env={**os.environ, 'MC_DATA_DIR': str(tmp_path / 'droot')})
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip().splitlines()[-1] == 'EXISTS', r.stdout + r.stderr
