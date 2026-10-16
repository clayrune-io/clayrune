"""A concurrent pytest startup must not collect the current run's state."""
import os
from pathlib import Path
import subprocess
import sys

import pytest


def test_startup_preserves_live_data_root_and_collects_dead_owner(tmp_path):
    psutil = pytest.importorskip('psutil')
    root = Path(__file__).resolve().parents[1]
    shared = tmp_path / 'shared-temp'
    live = shared / f'data-root-{os.getpid()}'
    live.mkdir(parents=True)
    marker = live / 'in-use.txt'
    marker.write_text('the parent still needs this state', encoding='utf-8')
    dead_pid = 2**31 - 1
    assert not psutil.pid_exists(dead_pid)
    dead = shared / f'data-root-{dead_pid}'
    dead.mkdir()
    (dead / 'abandoned.txt').write_text('left by an exited run', encoding='utf-8')
    env = dict(os.environ, PYTEST_DEBUG_TEMPROOT=str(shared))
    for key in ('MC_DATA_DIR', 'CLAYRUNE_HOME'):
        env.pop(key, None)
    # Execute the real startup with its real __file__, in another process.
    # No test modules or model CLIs are launched by this child.
    code = (
        "from pathlib import Path; import sys; p=sys.argv[1]; "
        "exec(compile(Path(p).read_text(encoding='utf-8'), p, 'exec'), "
        "{'__file__':p,'__name__':'probe_conftest'})"
    )
    result = subprocess.run(
        [sys.executable, '-c', code, str(root / 'tests/conftest.py')],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding='utf-8',
        env=env, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert marker.read_text(encoding='utf-8') == 'the parent still needs this state'
    assert not dead.exists()
