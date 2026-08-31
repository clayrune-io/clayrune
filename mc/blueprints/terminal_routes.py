"""Terminal session endpoints — blueprint 1.8 (MODERNIZATION_PLAN.md Phase 1).

Moved VERBATIM from server.py: 6 terminal-family routes (plan table said 5 —
the 5 /api/terminal/* [launch, stream, stdin, stop, delete] plus
/api/project/<id>/terminal/status, which is terminal-feature under the
project prefix; feature cohesion, same call as /api/presence in 1.2), the
stream-reader/kill session helpers, and the TTY-shim spawn machinery
(PYTHONPATH sitecustomize injection so child Python processes see
isatty()=True and Rich emits ANSI).

Security note (plan 1.8 acceptance): /api/terminal/launch has NO
route-private gate — its protection is the app-wide local_auth_gate
before_request (mc/blueprints/local_auth.py). Loopback is exempt (agents
curl localhost), CF-tunneled requests are exempt (already passed CF Access
OTP); every other origin must hold the LAN passcode cookie or gets
401 auth_required. tests/test_terminal_routes.py pins that contract.

The process-ledger fns (_register_process/_unregister_process) and
get_manager are dispatch-family (shared with agent/hivemind/housekeeping
spawns) — they stay in server.py and are wire()d in; they re-home at 1.12.

MC-928 — real PTY, opt-in via `"pty": true` on /api/terminal/launch: the
TTY-shim above only fixes Python children (Node CLIs like `gemini` need
actual terminal semantics to draw their Ink TUIs). mc/pty_backend.py gives
this file a second session shape — `session['pty']` (a pty_backend session)
instead of `session['proc']` (a subprocess.Popen) — read/write/kill all
branch on which key is set. Default (`pty` omitted/false) is byte-for-byte
the original pipe+shim path; nothing already relying on it changes.
launch_pty_session() is the in-process counterpart (no HTTP, no loopback
gate) for callers already running inside this server, e.g. agent_routes'
remote-login flow.
"""

import json
import os
import subprocess
import threading
import uuid
import time as _time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from flask import Blueprint, Response, jsonify, request

from mc import pty_backend
from mc.core import _is_loopback_request
from mc.state import agent_sessions, terminal_lock, terminal_sessions

bp = Blueprint('terminal_routes', __name__)

# ── wired by server.py (see wire()) ──────────────────────────────────────────
load_project: Callable[[str], Any] = None  # type: ignore[assignment]
get_manager: Callable[[str], Any] = None  # type: ignore[assignment]
_register_process: Callable[..., Any] = None  # type: ignore[assignment]
_unregister_process: Callable[..., Any] = None  # type: ignore[assignment]
_POPEN_FLAGS: int = 0
_STARTUPINFO: Any = None
# Resolve path to mc_tty_shim directory (contains sitecustomize.py) —
# derives from server.py's _APP_DIR, so it arrives via wire() (the 1.7
# SESSION_LABELS_PATH wired-placeholder pattern).
_TTY_SHIM_DIR: str = None  # type: ignore[assignment]
# Remote-family (CF JWT machinery, lives in remote_routes). Injected by
# server.py rather than imported, to avoid a blueprint-to-blueprint import —
# same pattern local_auth.py uses.
_is_cf_tunneled_request: "Callable[[], bool]" = None  # type: ignore[assignment]


def wire(*, load_project_fn, get_manager_fn, register_process_fn,
         unregister_process_fn, popen_flags, startupinfo, tty_shim_dir,
         is_cf_tunneled_request=None):
    """Late-bind cross-family deps: load_project (projects family, 1.11),
    get_manager + the process-ledger fns (dispatch family, 1.12), the Popen
    platform consts, and the _APP_DIR-derived TTY-shim dir. Called once from
    server.py at import, BEFORE app.register_blueprint(bp)."""
    global load_project, get_manager, _register_process, _unregister_process
    global _POPEN_FLAGS, _STARTUPINFO, _TTY_SHIM_DIR, _is_cf_tunneled_request
    load_project = load_project_fn
    get_manager = get_manager_fn
    _register_process = register_process_fn
    _unregister_process = unregister_process_fn
    _POPEN_FLAGS = popen_flags
    _STARTUPINFO = startupinfo
    _TTY_SHIM_DIR = tty_shim_dir
    _is_cf_tunneled_request = is_cf_tunneled_request


def _read_terminal_stream(proc, session):
    """Reader thread: captures stdout chunks into terminal session output_lines.

    Uses raw chunk reads (not line-by-line) to preserve ANSI escape sequences
    like cursor movement, screen clearing, and Rich Live display updates.
    """
    my_proc = proc
    fd = proc.stdout.fileno()
    try:
        while True:
            if session.get('proc') is not my_proc:
                break
            try:
                chunk = os.read(fd, 4096)
            except OSError:
                break
            if not chunk:
                break
            text = chunk.decode('utf-8', errors='replace')
            session['output_lines'].append(text)
            # Cap to prevent unbounded memory growth
            if len(session['output_lines']) > 5000:
                session['output_lines'] = session['output_lines'][-3000:]
    except Exception as e:
        if session.get('proc') is my_proc:
            session['output_lines'].append(f'[stream error: {e}]')
    finally:
        rc = proc.wait()
        _unregister_process(proc.pid)
        if session.get('proc') is my_proc:
            session['exit_code'] = rc
            if session['status'] == 'running':
                session['status'] = 'completed' if rc == 0 else 'error'
                session['output_lines'].append(f'\r\n[Process exited with code {rc}]')


def _read_pty_stream(pty_sess, session):
    """Reader thread for a real-PTY session (mirrors _read_terminal_stream's
    shape). An empty read() is unambiguous EOF on both backends — POSIX's
    blocking os.read() only returns b'' at EOF (pty_backend.py converts the
    Linux EIO-on-exit variant to '' too), and winpty's read() only returns ''
    after its internal reader thread observed the ConPTY close — so no
    isalive() polling/busy-loop is needed here, unlike a naive port of the
    pipe reader above."""
    my_pty = pty_sess
    try:
        while True:
            if session.get('pty') is not my_pty:
                return
            text = pty_sess.read(4096)
            if not text:
                break
            session['output_lines'].append(text)
            if len(session['output_lines']) > 5000:
                session['output_lines'] = session['output_lines'][-3000:]
    except Exception as e:
        if session.get('pty') is my_pty:
            session['output_lines'].append(f'[stream error: {e}]')
    finally:
        rc = pty_sess.wait()
        _unregister_process(pty_sess.pid)
        if session.get('pty') is my_pty:
            session['exit_code'] = rc
            if session['status'] == 'running':
                session['status'] = 'completed' if rc == 0 else 'error'
                session['output_lines'].append(f'\r\n[Process exited with code {rc}]')


def launch_pty_session(project_id, command, cwd=None):
    """In-process counterpart to terminal_launch()'s pty branch — no HTTP, no
    loopback gate, for callers already running inside this server (the
    remote-login flow in agent_routes.py). Returns (session_id, None) on
    success or (None, error_message) on failure — never raises, so a caller
    building a JSON error response doesn't need its own try/except.
    """
    if not pty_backend.pty_available():
        return None, ('Real-PTY terminal sessions need pywinpty on Windows '
                       "('pip install pywinpty') — not installed.")
    session_id = uuid.uuid4().hex[:12]
    env = {
        **os.environ,
        'PYTHONIOENCODING': 'utf-8',
        'PYTHONUNBUFFERED': '1',
        'TERM': 'xterm-256color',
        'COLUMNS': '120',
        'LINES': '30',
    }
    try:
        pty_sess = pty_backend.spawn(command, cwd=cwd, env=env, cols=120, rows=30)
    except pty_backend.PtyUnavailable as e:
        return None, str(e)
    except Exception as e:
        return None, f'Failed to launch: {e}'

    session = {
        'pty': pty_sess,
        'proc': None,
        'status': 'running',
        'command': command,
        'output_lines': [],
        'started_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'session_id': session_id,
        'project_id': project_id,
        'exit_code': None,
        'is_pty': True,
    }
    _register_process(pty_sess, 'Terminal', 'terminal',
                      session_id, project_id, command[:80])
    with terminal_lock:
        terminal_sessions[session_id] = session
    threading.Thread(target=_read_pty_stream, args=(pty_sess, session), daemon=True).start()
    return session_id, None


def _kill_terminal_session(session):
    """Kill a terminal session's subprocess (pipe backend) or PTY child."""
    pty_sess = session.get('pty')
    if pty_sess is not None:
        try:
            pty_sess.close(force=True)
        except Exception:
            pass
        _unregister_process(pty_sess.pid)
        return
    proc = session.get('proc')
    if not proc:
        return
    try:
        proc.stdin.close()
    except Exception:
        pass
    try:
        proc.kill()
    except Exception:
        pass
    _unregister_process(proc.pid)
    try:
        proc.wait(timeout=5)
    except Exception:
        pass


@bp.route('/api/terminal/launch', methods=['POST'])
def terminal_launch():
    """Launch a command in a terminal session.  Called by agents via curl."""
    # HOST-ONLY, and "host" excludes the tunnel.
    #
    # cloudflared runs on this machine and forwards to the origin over
    # loopback, so a tunneled request presents remote_addr == 127.0.0.1 —
    # tunnel requests are a strict SUBSET of loopback requests. A loopback
    # check alone therefore admits anyone who cleared CF Access, turning
    # dashboard access into arbitrary command execution. The original comment
    # here claimed tunnel sessions "never get raw shell"; they did.
    #
    # The dashboard never calls this endpoint (it only uses stream/stdin/
    # stop/delete) — only agents do, by curl from real localhost. So excluding
    # the tunnel costs no UI behaviour: pop-outs still render and stay
    # interactive on a phone.
    if not _is_loopback_request():
        return jsonify({'error': 'loopback_only'}), 403
    if _is_cf_tunneled_request is not None and _is_cf_tunneled_request():
        return jsonify({'error': 'host_only'}), 403
    data = request.get_json() or {}
    project_id = data.get('project_id', '').strip()
    command = data.get('command', '').strip()
    if not project_id or not command:
        return jsonify({'error': 'project_id and command required'}), 400

    p = load_project(project_id)
    if not p:
        return jsonify({'error': 'project not found'}), 404

    pp = p.get('project_path', '')
    cwd = pp if pp and Path(pp).is_dir() else None

    # MC-928: real PTY, opt-in via {"pty": true}. See launch_pty_session()
    # (this file) and mc/pty_backend.py — a wholly separate spawn path so the
    # default (pty omitted/false) below is untouched.
    if data.get('pty'):
        session_id, err = launch_pty_session(project_id, command, cwd=cwd)
        if err:
            return jsonify({'error': err}), 400
        mgr = get_manager(project_id)
        with mgr.lock:
            for sid in list(mgr.session_ids):
                asess = agent_sessions.get(sid)
                if asess and asess['status'] in ('running', 'idle'):
                    cmd_label = command.replace('\n', ' ').replace('\r', '')[:60]
                    asess['log_lines'].append(f'[terminal:{session_id}:{cmd_label}]')
        return jsonify({'ok': True, 'session_id': session_id, 'is_pty': True})

    session_id = uuid.uuid4().hex[:12]
    # TTY shim: inject sitecustomize.py via PYTHONPATH so child Python
    # processes see isatty()=True and Rich emits ANSI color codes
    existing_pypath = os.environ.get('PYTHONPATH', '')
    shim_pypath = _TTY_SHIM_DIR + os.pathsep + existing_pypath if existing_pypath else _TTY_SHIM_DIR
    env = {
        **os.environ,
        'PYTHONIOENCODING': 'utf-8',
        'PYTHONUNBUFFERED': '1',
        'MC_FORCE_TTY': '1',
        'PYTHONPATH': shim_pypath,
        'TERM': 'xterm-256color',
        'COLUMNS': '120',
        'LINES': '30',
    }

    try:
        proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=cwd,
            shell=True,
            creationflags=_POPEN_FLAGS,
            startupinfo=_STARTUPINFO,
            env=env,
        )
    except Exception as e:
        return jsonify({'error': f'Failed to launch: {e}'}), 500

    session = {
        'proc': proc,
        'status': 'running',
        'command': command,
        'output_lines': [],
        'started_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'session_id': session_id,
        'project_id': project_id,
        'exit_code': None,
    }

    _register_process(proc, 'Terminal', 'terminal',
                      session_id, project_id, command[:80])

    with terminal_lock:
        terminal_sessions[session_id] = session

    threading.Thread(target=_read_terminal_stream, args=(proc, session), daemon=True).start()

    # Notify any active agent SSE streams for this project (only this project's sessions)
    mgr = get_manager(project_id)
    with mgr.lock:
        for sid in list(mgr.session_ids):
            asess = agent_sessions.get(sid)
            if asess and asess['status'] in ('running', 'idle'):
                cmd_label = command.replace('\n', ' ').replace('\r', '')[:60]
                asess['log_lines'].append(f'[terminal:{session_id}:{cmd_label}]')

    return jsonify({'ok': True, 'session_id': session_id})


@bp.route('/api/terminal/stream')
def terminal_stream():
    """SSE endpoint streaming terminal output for a specific session."""
    session_id = request.args.get('session', '')
    since = request.args.get('since', '0')

    def generate():
        session = terminal_sessions.get(session_id)
        if not session:
            yield f"data: {json.dumps({'type': 'error', 'msg': 'no active session'})}\n\n"
            return

        sent = int(since) if since.isdigit() else 0
        tick = 0
        while True:
            lines = session['output_lines']
            if sent < len(lines):
                for line in lines[sent:]:
                    yield f"data: {json.dumps({'type': 'output', 'text': line})}\n\n"
                sent = len(lines)

            status = session['status']
            if status != 'running':
                yield f"data: {json.dumps({'type': 'status', 'status': status, 'exit_code': session.get('exit_code')})}\n\n"
                break

            tick += 1
            if tick % 50 == 0:
                yield ": heartbeat\n\n"

            _time.sleep(0.3)

    return Response(generate(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@bp.route('/api/terminal/stdin', methods=['POST'])
def terminal_stdin():
    """Write text to a terminal session's stdin."""
    data = request.get_json() or {}
    session_id = data.get('session_id', '').strip()
    text = data.get('text', '')
    if not session_id:
        return jsonify({'error': 'session_id required'}), 400

    session = terminal_sessions.get(session_id)
    if not session or session['status'] != 'running':
        return jsonify({'error': 'session not running'}), 400

    pty_sess = session.get('pty')
    if pty_sess is not None:
        try:
            pty_sess.write(text)
        except Exception:
            pass
        return jsonify({'ok': True})

    try:
        session['proc'].stdin.write(text.encode('utf-8'))
        session['proc'].stdin.flush()
    except (BrokenPipeError, OSError):
        pass

    return jsonify({'ok': True})


@bp.route('/api/terminal/resize', methods=['POST'])
def terminal_resize():
    """Resize a real-PTY session's window (MC-928). No-op target for pipe
    sessions — they have no PTY geometry to resize, so 400 rather than
    silently doing nothing, same as calling stdin on a dead session."""
    data = request.get_json() or {}
    session_id = data.get('session_id', '').strip()
    cols, rows = data.get('cols'), data.get('rows')
    if not session_id or not cols or not rows:
        return jsonify({'error': 'session_id, cols, rows required'}), 400

    session = terminal_sessions.get(session_id)
    if not session or session['status'] != 'running':
        return jsonify({'error': 'session not running'}), 400
    pty_sess = session.get('pty')
    if pty_sess is None:
        return jsonify({'error': 'not a pty session'}), 400
    try:
        pty_sess.resize(int(cols), int(rows))
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    return jsonify({'ok': True})


@bp.route('/api/terminal/stop', methods=['POST'])
def terminal_stop():
    """Stop (kill) a running terminal session."""
    data = request.get_json() or {}
    session_id = data.get('session_id', '').strip()
    if not session_id:
        return jsonify({'error': 'session_id required'}), 400

    with terminal_lock:
        session = terminal_sessions.get(session_id)
        if not session:
            return jsonify({'error': 'session not found'}), 404
        if session['status'] != 'running':
            return jsonify({'error': 'not running'}), 400
        _kill_terminal_session(session)
        session['status'] = 'stopped'
        session['output_lines'].append('\r\n[Process stopped by user]')

    return jsonify({'ok': True})


@bp.route('/api/project/<project_id>/terminal/status')
def terminal_status(project_id):
    """Return running terminal sessions for a project (for reconnection after refresh)."""
    sessions = []
    for sid, s in list(terminal_sessions.items()):
        if s['project_id'] != project_id:
            continue
        # Only return running sessions — completed/stopped are disposable
        if s['status'] == 'running':
            sessions.append({
                'session_id': s['session_id'],
                'status': s['status'],
                'command': s['command'],
                'output_lines': s['output_lines'],
                'started_at': s['started_at'],
                'exit_code': s.get('exit_code'),
                'is_pty': bool(s.get('is_pty')),
            })
        else:
            # Purge non-running sessions from memory
            terminal_sessions.pop(sid, None)
    return jsonify({'sessions': sessions})


@bp.route('/api/terminal/delete', methods=['POST'])
def terminal_delete():
    """Kill process (if running) and remove session from memory entirely."""
    data = request.get_json() or {}
    session_id = data.get('session_id', '').strip()
    if not session_id:
        return jsonify({'error': 'session_id required'}), 400

    with terminal_lock:
        session = terminal_sessions.pop(session_id, None)
        if not session:
            return jsonify({'ok': True})  # already gone
        if session['status'] == 'running':
            _kill_terminal_session(session)

    return jsonify({'ok': True})
