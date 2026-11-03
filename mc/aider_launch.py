"""Server-side opt-in and preflight for the restricted Aider child."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

FLAG = 'aider_restricted_adapter_enabled'


def enabled() -> bool:
    from mc import state
    return state.CONFIG.get(FLAG, False) is True


def prepare(cmd: list[str], cwd: str, env: dict[str, str]) -> list[str]:
    """Resolve only the selected installation; unknown layouts refuse."""
    from mc.cli_install import sibling_python
    if not enabled():
        raise RuntimeError('restricted Aider adapter is disabled')
    if getattr(sys, 'frozen', False):
        raise RuntimeError('restricted Aider source runner is unavailable in frozen builds')
    interpreter = sibling_python(cmd[0])
    if not interpreter:
        raise RuntimeError('cannot resolve the selected Aider installation interpreter')
    runner = Path(__file__).with_name('aider_runner.py').resolve()
    prefix = [interpreter, '-I', '-B', str(runner)]
    # Both preflight and actual child independently verify source and install
    # the guard. No cached proof survives a version/source change.
    result = subprocess.run(prefix + ['--self-test'], cwd=cwd, env=env,
                            capture_output=True, text=True, encoding='utf-8',
                            errors='replace', timeout=30,
                            creationflags=0x08000000 if os.name == 'nt' else 0)
    if result.returncode:
        raise RuntimeError('restricted Aider preflight refused: ' +
                           (result.stderr or result.stdout)[-3000:].strip())
    try:
        evidence: Any = json.loads(result.stdout.splitlines()[-1])
    except (ValueError, IndexError) as exc:
        raise RuntimeError('restricted Aider preflight returned no guard proof') from exc
    if evidence != {'restricted_aider': '0.86.2', 'guard': 'ready'}:
        raise RuntimeError('restricted Aider preflight returned unexpected guard proof')
    return prefix + cmd[1:]
