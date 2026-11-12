"""Read-only, best-effort diagnostics for an untracked browser profile holder."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from mc.core import _log


def _is_clayrune_server(parent: Any) -> bool:
    argv = parent.cmdline()
    if not argv:
        return False
    executable = argv[0].replace('\\', '/').rsplit('/', 1)[-1].lower()
    if executable in {'clayrune', 'clayrune.exe'}:
        return True
    for arg in argv[1:]:
        script = Path(arg)
        if script.name.lower() not in {'server.py', 'app.py'}:
            continue
        if not script.is_absolute():
            script = Path(parent.cwd()) / script
        # A generic server.py/app.py belonging to another app is not enough.
        if (script.parent / 'mc' / 'blueprints' / 'browser_routes.py').is_file():
            return True
    return False


def profile_owner(pid: int) -> str:
    """Describe the holder and its parent without closing or signalling either.

    Process access can fail or race with exit at any step. Preserve whatever
    identity was already read; a diagnostic must never block the refusal.
    """
    details = [f'pid {pid}']
    try:
        import psutil

        parent_pid = psutil.Process(pid).ppid()
        details.append(f'parent pid {parent_pid}')
    except Exception as e:
        _log(f'[browser] profile owner parent lookup failed for pid {pid}: {e}', flush=True)
        details.append('parent unknown')
        return ', '.join(details)

    try:
        parent = psutil.Process(parent_pid)
        if _is_clayrune_server(parent):
            connections = getattr(parent, 'net_connections', None) or parent.connections
            ports = sorted({conn.laddr.port for conn in connections(kind='tcp')
                            if conn.status == psutil.CONN_LISTEN and conn.laddr})
            if ports:
                details.append('Clayrune server port ' + ', '.join(map(str, ports)))
    except Exception as e:
        _log(f'[browser] profile owner server lookup failed for parent pid {parent_pid}: {e}', flush=True)
    return ', '.join(details)
