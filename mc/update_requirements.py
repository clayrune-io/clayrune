"""Install changed Python requirements after an in-app update (backlog 40260b57).

`/api/system/update` is `git pull`. It never touched the venv, so a release
that added a dependency (psutil, for dispatch caller attribution) reached every
updated install as code that imports a package the install does not have.
`caller_attribution` degrades silently without psutil -- to trusting Origin --
which is exactly the fence escape it exists to close.

`sync_requirements` runs after a successful pull: if `requirements.txt` differs
between the pre-pull HEAD and the new one, it runs
`<this python> -m pip install -r requirements.txt` and reports the outcome. A
pip failure is REPORTED (return value + log), never raised and never undone:
the pull already landed and code is ahead of the venv either way; the user
needs to be told, not rolled back.

Frozen (PyInstaller) builds bundle their dependencies and have no pip: skipped.
Never raises.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from mc.core import _log

REQUIREMENTS_FILE = 'requirements.txt'
PIP_TIMEOUT_S = 600
_DETAIL_CHARS = 1500


def _tail(text: str) -> str:
    text = (text or '').strip()
    return text[-_DETAIL_CHARS:]


def requirements_changed(git: Callable[..., Tuple[int, str]], repo_root: Path,
                         old_ref: str) -> Optional[bool]:
    """True/False when requirements.txt differs between `old_ref` and HEAD;
    None when that cannot be told (no old ref, git error)."""
    if not old_ref:
        return None
    rc, out = git(['diff', '--quiet', old_ref, 'HEAD', '--', REQUIREMENTS_FILE], repo_root)
    if rc == 0:
        return False
    if rc == 1:               # `git diff --quiet` exits 1 when there are differences
        return True
    _log(f"[update] requirements diff failed (rc={rc}): {out[:200]}", flush=True)
    return None


def sync_requirements(git: Callable[..., Tuple[int, str]], repo_root: Path,
                      old_ref: str, *, frozen: Optional[bool] = None,
                      runner: Callable[..., Any] = subprocess.run,
                      run_kwargs: Optional[Dict[str, Any]] = None,
                      timeout: int = PIP_TIMEOUT_S) -> Dict[str, Any]:
    """{'ran', 'ok', 'reason', 'rc', 'detail'} for the response body.

    `ok` is False only when pip was needed and did not succeed."""
    try:
        return _sync(git, Path(repo_root), old_ref,
                     getattr(sys, 'frozen', False) if frozen is None else frozen,
                     runner, run_kwargs or {}, timeout)
    except Exception as e:
        _log(f"[update] requirements sync raised: {e!r}", flush=True)
        return {'ran': False, 'ok': False, 'reason': 'error', 'rc': None,
                'detail': f'{e!r}'}


def _sync(git, repo_root, old_ref, frozen, runner, run_kwargs, timeout):
    if frozen:
        return {'ran': False, 'ok': True, 'reason': 'frozen build bundles its dependencies',
                'rc': None, 'detail': ''}
    req = repo_root / REQUIREMENTS_FILE
    if not req.is_file():
        return {'ran': False, 'ok': True, 'reason': 'no requirements.txt', 'rc': None, 'detail': ''}
    changed = requirements_changed(git, repo_root, old_ref)
    if changed is False:
        return {'ran': False, 'ok': True, 'reason': 'requirements.txt unchanged',
                'rc': None, 'detail': ''}
    # changed is None (cannot tell): pip is idempotent, so installing is the
    # safe answer to "might the venv be behind the code".
    why = 'requirements.txt changed' if changed else 'could not compare requirements.txt'

    cmd = [sys.executable, '-m', 'pip', 'install', '-r', str(req)]
    env = os.environ.copy()
    env['PIP_DISABLE_PIP_VERSION_CHECK'] = '1'
    _log(f"[update] {why}; running: {' '.join(cmd)}", flush=True)
    try:
        r = runner(cmd, cwd=str(repo_root), capture_output=True, text=True,
                   stdin=subprocess.DEVNULL, encoding='utf-8', errors='replace',
                   timeout=timeout, env=env, **run_kwargs)
    except subprocess.TimeoutExpired:
        _log(f"[update] pip install timed out after {timeout}s", flush=True)
        return {'ran': True, 'ok': False, 'reason': why, 'rc': -2,
                'detail': f'pip install timed out after {timeout}s'}
    except Exception as e:
        _log(f"[update] pip install could not start: {e!r}", flush=True)
        return {'ran': True, 'ok': False, 'reason': why, 'rc': -3, 'detail': f'{e!r}'}

    out = _tail((r.stdout or '') + (r.stderr or ''))
    if r.returncode != 0:
        _log(f"[update] pip install FAILED (rc={r.returncode}); the pull is kept, "
             f"dependencies are behind the code: {out[-300:]}", flush=True)
        return {'ran': True, 'ok': False, 'reason': why, 'rc': r.returncode, 'detail': out}
    _log("[update] pip install ok", flush=True)
    return {'ran': True, 'ok': True, 'reason': why, 'rc': 0, 'detail': out}
