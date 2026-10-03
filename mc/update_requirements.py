"""Keep the venv's packages in step with requirements.txt (backlog 40260b57).

`/api/system/update` is `git pull`. It never touched the venv, so a release
that added a dependency (psutil, for dispatch caller attribution) reached every
updated install as code that imports a package the install does not have.
`caller_attribution` degrades silently without psutil -- to trusting Origin --
which is exactly the fence escape it exists to close.

Two triggers share one background sync:

  * boot: the update that first delivers this code runs the OLD in-memory
    `system_update`, so no update hook can fix installs already behind. At
    boot the server compares a digest of requirements.txt against a stamp left
    by the last successful install; missing or different -> pip install.
  * update: after a successful pull the endpoint kicks the same sync, so a
    release that changes requirements installs them without waiting for a
    restart.

pip runs in a daemon thread -- never in the HTTP request, never blocking boot.
The stamp is written only after pip succeeds, so a failure is retried at the
next boot or update. A failure is reported (state, log), never raised, and
never undoes the pull. The stamp covers requirements.txt AND the interpreter,
because two installs sharing ~/.clayrune have different venvs.

Frozen (PyInstaller) builds bundle their dependencies and have no pip: skipped.
Never raises.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from mc.core import _atomic_write_text, _log
from mc.guardrail_hooks import clayrune_home

REQUIREMENTS_FILE = 'requirements.txt'
STAMP_FILE = 'requirements_installed.sha256'
PIP_TIMEOUT_S = 600
_DETAIL_CHARS = 1500

IDLE, RUNNING, OK, FAILED, SKIPPED = 'idle', 'running', 'ok', 'failed', 'skipped'

_lock = threading.Lock()
_thread: Optional[threading.Thread] = None
_state: Dict[str, Any] = {'status': IDLE, 'trigger': '', 'reason': '', 'rc': None,
                          'detail': '', 'started_at': None, 'finished_at': None}


def _tail(text: str) -> str:
    return (text or '').strip()[-_DETAIL_CHARS:]


def stamp_path() -> Path:
    return clayrune_home() / STAMP_FILE


def requirements_digest(repo_root: Path) -> Optional[str]:
    """sha256 over requirements.txt (line endings normalised, so a CRLF
    checkout and an LF one agree) and the running interpreter. None when the
    file is missing."""
    try:
        data = (Path(repo_root) / REQUIREMENTS_FILE).read_bytes()
    except OSError:
        return None
    h = hashlib.sha256(data.replace(b'\r\n', b'\n'))
    h.update(b'\0' + sys.executable.encode('utf-8', 'replace'))
    return h.hexdigest()


def get_state() -> Dict[str, Any]:
    """Snapshot of the last sync in this process, for status routes."""
    with _lock:
        return dict(_state)


def wait_for_sync(timeout: Optional[float] = None) -> None:
    """Join the running sync thread, if any (tests, and orderly shutdown)."""
    t = _thread
    if t is not None:
        t.join(timeout)


def _set(**kw) -> Dict[str, Any]:
    with _lock:
        _state.update(kw)
        return dict(_state)


def start_background_sync(repo_root: Path, *, trigger: str,
                          frozen: Optional[bool] = None,
                          runner: Optional[Callable[..., Any]] = None,
                          run_kwargs: Optional[Dict[str, Any]] = None,
                          timeout: int = PIP_TIMEOUT_S,
                          stamp: Optional[Path] = None) -> Dict[str, Any]:
    """Install requirements in a daemon thread if the stamp says the venv is
    behind. Returns the state snapshot immediately. Never raises."""
    global _thread
    try:
        stamp = Path(stamp) if stamp else stamp_path()
        is_frozen = getattr(sys, 'frozen', False) if frozen is None else frozen
        if is_frozen:
            return _set(status=SKIPPED, trigger=trigger, rc=None, detail='',
                        reason='frozen build bundles its dependencies')
        if runner is None and os.environ.get('PYTEST_CURRENT_TEST'):
            # A test that reaches the real pip would install into the dev venv.
            return _set(status=SKIPPED, trigger=trigger, rc=None, detail='',
                        reason='pip is not run under pytest')
        digest = requirements_digest(repo_root)
        if digest is None:
            return _set(status=SKIPPED, trigger=trigger, rc=None, detail='',
                        reason='no requirements.txt')
        with _lock:
            if _state['status'] == RUNNING:
                return dict(_state)
            try:
                current = stamp.read_text(encoding='utf-8').strip()
            except OSError:
                current = ''
            if current == digest:
                _state.update(status=OK, trigger=trigger, rc=None, detail='',
                              reason='requirements already installed')
                return dict(_state)
            why = 'no install stamp' if not current else 'requirements.txt changed'
            _state.update(status=RUNNING, trigger=trigger, reason=why, rc=None,
                          detail='', started_at=time.time(), finished_at=None)
            snap = dict(_state)
            _thread = threading.Thread(
                target=_worker, name='requirements-sync', daemon=True,
                args=(Path(repo_root), digest, stamp, runner or subprocess.run,
                      run_kwargs or {}, timeout, trigger, why))
            _thread.start()
        return snap
    except Exception as e:
        _log(f"[requirements] could not start sync: {e!r}", flush=True)
        return _set(status=FAILED, trigger=trigger, rc=-3, reason='error',
                    detail=f'{e!r}', finished_at=time.time())


def _worker(repo_root, digest, stamp, runner, run_kwargs, timeout, trigger, why):
    try:
        res = _pip_install(repo_root, runner, run_kwargs, timeout, trigger, why)
        if res['ok']:
            try:
                stamp.parent.mkdir(parents=True, exist_ok=True)
                _atomic_write_text(stamp, digest + '\n')
            except Exception as e:
                # pip worked; the next boot just repeats it. Say so.
                _log(f"[requirements] install ok but stamp write failed: {e!r}", flush=True)
        _set(status=OK if res['ok'] else FAILED, rc=res['rc'], detail=res['detail'],
             finished_at=time.time())
    except Exception as e:
        _log(f"[requirements] sync raised: {e!r}", flush=True)
        _set(status=FAILED, rc=-3, detail=f'{e!r}', finished_at=time.time())


def _pip_install(repo_root: Path, runner, run_kwargs, timeout, trigger, why) -> Dict[str, Any]:
    cmd = [sys.executable, '-m', 'pip', 'install', '-r', str(repo_root / REQUIREMENTS_FILE)]
    env = os.environ.copy()
    env['PIP_DISABLE_PIP_VERSION_CHECK'] = '1'
    _log(f"[requirements] ({trigger}) {why}; running: {' '.join(cmd)}", flush=True)
    try:
        r = runner(cmd, cwd=str(repo_root), capture_output=True, text=True,
                   stdin=subprocess.DEVNULL, encoding='utf-8', errors='replace',
                   timeout=timeout, env=env, **run_kwargs)
    except subprocess.TimeoutExpired:
        _log(f"[requirements] pip install timed out after {timeout}s", flush=True)
        return {'ok': False, 'rc': -2, 'detail': f'pip install timed out after {timeout}s'}
    except Exception as e:
        _log(f"[requirements] pip install could not start: {e!r}", flush=True)
        return {'ok': False, 'rc': -3, 'detail': f'{e!r}'}
    out = _tail((r.stdout or '') + (r.stderr or ''))
    if r.returncode != 0:
        _log(f"[requirements] pip install FAILED (rc={r.returncode}); dependencies are "
             f"behind the code: {out[-300:]}", flush=True)
        return {'ok': False, 'rc': r.returncode, 'detail': out}
    _log("[requirements] pip install ok", flush=True)
    return {'ok': True, 'rc': 0, 'detail': out}
