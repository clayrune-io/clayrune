"""Warn when an argv unit's bare `python` resolves to an interpreter without the deps.

On Windows a venv's python.exe is a redirector: the process that actually runs
is the base interpreter, and CreateProcess searches the calling process's image
directory (the base one) BEFORE PATH when argv[0] is a bare name. So a launcher
started as the project's venv python spawns a bare `python` child that is the
BASE python, while a shell's own PATH lookup of the same word finds the venv.
The launcher never swaps interpreters (exact argv is the contract); this module
only reports, so a red run caused by the environment says so.

Standalone stdlib only, like the rest of tools/test_run_*.py. Never raises.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Sequence

# The Clayrune test suites need these; a child lacking either goes red for
# environment reasons (2026-11-17: psutil, in tests/test_agent_shim_adopt_blockers.py).
REQUIRED_MODULES = ('psutil', 'pytest')
PROBE_TIMEOUT_SECONDS = 10.0
_PYTHON_NAME = re.compile(r'python(3(\.\d+)?)?w?|python\d+(\.\d+)?', re.IGNORECASE)
_PROBE = ('import sys, json, importlib.util as u;'
          'print(json.dumps({"exe": sys.executable, "missing": '
          '[m for m in json.loads(sys.argv[1]) if u.find_spec(m) is None]}))')


def is_python_argv(argv: Any) -> bool:
    """True for a direct `python`-named executable; `py`, scripts and shells are not probed."""
    if isinstance(argv, str) or not argv:
        return False
    name = Path(argv[0]).name
    if name.lower().endswith('.exe'):
        name = name[:-4]
    return _PYTHON_NAME.fullmatch(name) is not None


def check(argv: Any, cwd: str, env: dict[str, str],
          modules: Sequence[str] = REQUIRED_MODULES) -> list[tuple[str, str]]:
    """Return (diagnostic_code, stderr_message) pairs; empty when nothing to say.

    The probe is `argv[0]` itself with the unit's cwd and env, so it resolves
    exactly as the real child will. It uses find_spec, so no module code runs.
    """
    try:
        if not is_python_argv(argv):
            return []
        done = subprocess.run([argv[0], '-c', _PROBE, json.dumps(list(modules))], cwd=cwd, env=env,
                              stdin=subprocess.DEVNULL, capture_output=True,
                              timeout=PROBE_TIMEOUT_SECONDS)
        lines = done.stdout.decode('utf-8', 'replace').strip().splitlines()
        info = json.loads(lines[-1]) if done.returncode == 0 and lines else None
        if not isinstance(info, dict) or not isinstance(info.get('missing'), list):
            return [('interpreter_probe_failed',
                     'interpreter_probe_failed: could not inspect `' + argv[0] + '` (exit '
                     + str(done.returncode) + '); dependency check skipped')]
        exe = str(info.get('exe'))
        found = []
        for module in info['missing']:
            # Codes are [a-z0-9_] only (mc/test_run_store.py), so module names are
            # reduced to that alphabet; REQUIRED_MODULES already conforms.
            code = 'interpreter_missing_' + re.sub(r'[^a-z0-9_]', '_', str(module).lower())
            message = (code + ': the child `' + argv[0] + '` runs ' + exe + ', which has no `'
                       + str(module) + '`')
            if os.path.normcase(exe) != os.path.normcase(sys.executable):
                message += ' (this launcher runs ' + sys.executable + ')'
            message += '. The launcher runs the argv exactly as given; use the project interpreter by absolute path.'
            found.append((code, message))
        return found
    except FileNotFoundError:
        return []  # The real start reports child_start_failed; do not double up.
    except subprocess.TimeoutExpired:
        return [('interpreter_probe_failed', 'interpreter_probe_failed: `' + str(argv[0])
                 + '` did not answer within ' + str(int(PROBE_TIMEOUT_SECONDS)) + 's; dependency check skipped')]
    except Exception as exc:  # Diagnostics must never change a test run's result.
        return [('interpreter_probe_failed', 'interpreter_probe_failed: ' + type(exc).__name__)]
