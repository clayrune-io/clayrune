"""MC-1179 TL30: tools/position-review.py must never import `server`.

`import server` starts the tunnel autostart thread, whose orphan reaper kills the
running server's connector. Run in a subprocess against a temp data root so the
test neither imports server itself nor touches the live records.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

def _run(tmp_path, *args):
    data = tmp_path / 'droot' / 'data' / 'projects'
    data.mkdir(parents=True, exist_ok=True)
    (data / 'zz.json').write_text(json.dumps({'id': 'zz', 'project_path': ''}), encoding='utf-8')
    (data / 'zz_agent_log.json').write_text('[]', encoding='utf-8')   # sidecar: not a project
    code = (
        "import runpy, sys\n"
        f"sys.argv = ['position-review.py'] + {list(args)!r}\n"
        "try:\n"
        f"    runpy.run_path({str(ROOT / 'tools' / 'position-review.py')!r}, run_name='__main__')\n"
        "except SystemExit as e:\n"
        "    code = e.code\n"
        "else:\n"
        "    code = 0\n"
        "print('SERVER_IMPORTED=%s' % ('server' in sys.modules))\n"
        "print('EXIT=%s' % code)\n")
    r = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=120,
                       cwd=str(ROOT), env={**os.environ, 'MC_DATA_DIR': str(tmp_path / 'droot')})
    lines = r.stdout.strip().splitlines()
    return r, lines


def test_flags_runs_without_importing_the_server(tmp_path):
    r, lines = _run(tmp_path, 'flags', '--json')
    assert 'SERVER_IMPORTED=False' in lines, r.stdout + r.stderr
    assert 'EXIT=0' in lines, r.stdout + r.stderr


def test_unknown_project_is_exit_2(tmp_path):
    r, lines = _run(tmp_path, 'brief', '--project', 'nosuch')
    assert 'SERVER_IMPORTED=False' in lines, r.stdout + r.stderr
    assert 'EXIT=2' in lines, r.stdout + r.stderr
