"""mc_tty_shim must not turn a non-numeric COLUMNS into a crash in shutil.get_terminal_size."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

SHIM = Path(__file__).resolve().parent.parent / 'mc_tty_shim'


def _size(columns: str, *call_args: str) -> str:
    env = dict(os.environ, MC_FORCE_TTY='1', PYTHONPATH=str(SHIM), COLUMNS=columns, LINES='30')
    code = f'import shutil; s = shutil.get_terminal_size({", ".join(call_args)}); print(s.columns, s.lines)'
    done = subprocess.run([sys.executable, '-c', code], env=env, capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


def test_numeric_columns_still_win():
    assert _size('97') == '97 30'


def test_non_numeric_columns_fall_back_instead_of_raising():
    # Stdlib semantics: the unusable COLUMNS is ignored, the caller's fallback is used.
    assert _size('wide', '(66, 11)') == '66 30'
