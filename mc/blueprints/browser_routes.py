"""Browser pane endpoints — Part B of the browser-pairing feature.

A visible, interactive browser rendered inside a Clayrune tab. A headless
Chromium (the one already on disk from the Playwright MCP install) is launched
with remote debugging and driven over CDP (Chrome DevTools Protocol) via a
websocket:

  - a reader thread receives `Page.screencastFrame` events into the session's
    latest-frame buffer (base64 JPEG) and acks them;
  - `/api/browser/stream` re-streams those frames to the pane over SSE;
  - `/api/browser/input` queues mouse/key/scroll/navigate commands, which the
    same CDP thread dispatches (single sender → no cross-thread ws races).

Mirrors the terminal pop-out (mc/blueprints/terminal_routes.py): a late-wired
blueprint, a session registry in mc.state, and process-ledger registration.

OPTIONAL / LAZY BY DESIGN. `websocket-client` is imported lazily and Chromium is
located at call time; neither is a core Clayrune dependency, so a stranger's
install isn't bloated. If either is missing, the endpoints return 501 with a
one-line install hint instead of failing the app.

Security: same posture as /api/terminal/* — protected by the app-wide
local_auth_gate before_request (loopback + CF-tunnel exempt, LAN needs the
passcode). Chromium runs headless with a per-session throwaway user-data-dir,
unless a launch names a **profile** (see below).

## Profiles — ephemeral by default, persistent by name

A throwaway profile means every visit starts logged out. For one-off browsing
that is the right default; for an agent that posts to the same social account
every week it is the whole cost of the feature — a fresh login (and a 2FA
prompt, and an anti-bot challenge) on every single run.

So a launch may name a profile: `{"profile": "reddit"}` reuses
`~/.clayrune/browser_profiles_named/reddit`, which survives teardown and keeps
its cookies. Unnamed launches behave exactly as before.

Set `browser_default_profile` in config.json to make UNNAMED launches use a
named profile too, so the plain 🌐 Browser button keeps logins instead of
starting logged out every time. `{"ephemeral": true}` opts one launch out.

Three properties hold this together:

- **The profile is flushed by closing Chromium, not by killing it.** A hard
  kill loses every cookie the browser has not yet written — see
  `_graceful_close`, which is the difference between a saved login and an empty
  directory that merely looks like one.

- **The named root is a SIBLING of the throwaway root, not a subdirectory.**
  `sweep_orphan_profiles()` deletes everything under the throwaway root that no
  live session owns — a saved login nested inside it would be swept the moment
  its session ended. A filter would work until someone edited the filter; a
  separate directory cannot be reached by that walk at all.
- **One Chromium per profile dir.** Two processes sharing a user-data-dir
  corrupt it, so naming a profile that is already open returns the running
  session instead of launching a second browser.

A persistent profile holds live session cookies: it is a credential, and
deleting it is the sign-out. Chromium encrypts the cookie DB under the OS
keychain (DPAPI / Keychain / SecretService); on a headless Linux box with no
keyring it falls back to a hardcoded key, so there the file is effectively
plaintext to anything running as this user — the same honest caveat the secrets
vault makes about its file key backend.
"""

import glob
from urllib.parse import urlsplit as _urlsplit
import json
import os
import queue
import re
import shutil
import socket
import subprocess
import threading
import time as _time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from flask import Blueprint, Response, jsonify, request

from mc import state
from mc.state import browser_sessions, browser_lock

bp = Blueprint('browser_routes', __name__)

# Fixed render viewport — the pane scales displayed coords back to these.
VIEW_W, VIEW_H = 1280, 800

# ── wired by server.py ───────────────────────────────────────────────────────
_register_process: Callable[..., Any] = None  # type: ignore[assignment]
_unregister_process: Callable[..., Any] = None  # type: ignore[assignment]
_POPEN_FLAGS: int = 0
_STARTUPINFO: Any = None
# One-shot guard: the orphan-profile sweep runs on first real browser launch.
_swept_orphans: bool = False
# Second guard: the sweep decides what is an orphan by diffing the throwaway
# profile root against the IN-MEMORY browser_sessions of THIS process. Any other
# process that imports this module (a test, a debug script, a second MC) has an
# empty registry, so every live session's dir looks orphaned and gets deleted
# out from under a running Chromium. The lazy-from-_launch_browser placement
# narrowed that window but did not close it — driving _launch_browser from a
# verification script stripped a live session's profile on 2026-08-05. server.py
# sets this True at startup; nothing else may.
SWEEP_ENABLED: bool = False


def wire(*, register_process_fn, unregister_process_fn, popen_flags, startupinfo):
    global _register_process, _unregister_process, _POPEN_FLAGS, _STARTUPINFO
    _register_process = register_process_fn
    _unregister_process = unregister_process_fn
    _POPEN_FLAGS = popen_flags
    _STARTUPINFO = startupinfo


def _profiles_root():
    """Throwaway per-session profiles. Everything here is deletable."""
    return os.path.join(os.path.expanduser('~'), '.clayrune', 'browser_profiles')


def _named_profiles_root():
    """Saved, signed-in profiles. A SIBLING of the throwaway root by design —
    see the module docstring: the orphan sweep walks the throwaway root, and
    nothing that walk can reach may hold a login."""
    return os.path.join(os.path.expanduser('~'), '.clayrune',
                        'browser_profiles_named')


# Same shape as a secret name (mc/secrets_store.py): lowercase, dot-namespaced.
# Rejects separators and `..`, so a profile name can never escape its root.
_PROFILE_NAME_RE = re.compile(r'^[a-z0-9][a-z0-9._-]{0,63}$')


def _profile_dir(name):
    """Absolute path for a named profile, or None if the name is unusable."""
    name = (name or '').strip().lower()
    if not _PROFILE_NAME_RE.match(name) or '..' in name:
        return None
    return os.path.join(_named_profiles_root(), name)


def _session_using_profile(name):
    """A live session already holding this profile, if any. Launching a second
    Chromium on one user-data-dir corrupts the profile, so this is a guard, not
    an optimisation."""
    with browser_lock:
        for s in browser_sessions.values():
            if s.get('profile') == name and s.get('status') == 'running':
                return s
    return None


def _dir_size(path):
    total = 0
    for dirpath, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(dirpath, f))
            except OSError:
                pass
    return total


def sweep_orphan_profiles():
    """Delete Chromium profile dirs left behind by sessions that are gone.

    Teardown removes a session's own profile, but a hard kill of MC (or a crash
    before _kill_browser_session ran) strands the dir forever — they had grown
    to 56 dirs / 922 MB on the dev box before this existed.

    Deliberately NOT called from wire(): this deletes real files under the
    user's home, and wire() also runs under import/test harnesses — doing it
    there made a plain `pytest` run destroy 4 real profile dirs (130 MB) and
    pollute stdout. It is now invoked lazily from _launch_browser(), so it only
    ever runs in a process that is genuinely using the browser pane. Live
    sessions' dirs are skipped regardless.
    """
    if not SWEEP_ENABLED:
        # Not the server process → our browser_sessions view is not the truth
        # about what is live, so we cannot tell an orphan from a running pane.
        return
    root = _profiles_root()
    if not os.path.isdir(root):
        return
    with browser_lock:
        live = {s.get('user_data_dir') for s in browser_sessions.values()}
    removed = freed = 0
    for name in os.listdir(root):
        path = os.path.join(root, name)
        if path in live or not os.path.isdir(path):
            continue
        try:
            freed += _dir_size(path)
            shutil.rmtree(path, ignore_errors=True)
            removed += 1
        except Exception as e:
            print(f'[browser] orphan profile sweep failed for {path}: {e}', flush=True)
    if removed:
        print(f'[browser] swept {removed} orphaned profile dir(s), '
              f'freed {freed // (1024 * 1024)} MB', flush=True)


def _find_chromium():
    """Locate the Chromium binary from the Playwright browser cache (or an
    explicit override). Returns None if not found."""
    override = os.environ.get('MC_BROWSER_CHROMIUM')
    if override and os.path.exists(override):
        return override
    root = os.path.join(os.path.expanduser('~'), 'AppData', 'Local', 'ms-playwright')
    if os.name != 'nt':
        root = os.path.join(os.path.expanduser('~'), '.cache', 'ms-playwright')
    pats = [
        os.path.join(root, 'chromium-*', 'chrome-win64', 'chrome.exe'),
        os.path.join(root, 'chromium-*', 'chrome-win', 'chrome.exe'),
        os.path.join(root, 'chromium-*', 'chrome-linux', 'chrome'),
        os.path.join(root, 'chromium-*', 'chrome-mac', 'Chromium.app', 'Contents', 'MacOS', 'Chromium'),
    ]
    found = []
    for p in pats:
        found.extend(glob.glob(p))
    if not found:
        return None
    # Highest chromium-<N> build wins.
    def _rev(path):
        try:
            seg = [s for s in path.split(os.sep) if s.startswith('chromium-')][0]
            return int(seg.split('-')[1])
        except Exception:
            return 0
    return sorted(found, key=_rev)[-1]


def _free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _import_ws():
    try:
        import websocket  # websocket-client
        return websocket
    except Exception:
        return None


def _pick_page_target(targets, want_url=''):
    """Choose which tab to attach to.

    Was `first page target, whatever it is`. A named profile restores its
    previous tabs and SSO flows open their own, so that routinely picked the
    wrong one -- and a tab that is not frontmost renders NOTHING in headless
    Chromium, so the pane went permanently black while the URL bar still
    showed the address we asked for. Measured on the cloudflare profile: the
    foreground tab emitted a frame immediately, the background sign-in tab
    emitted zero in 5s.
    """
    pages = [t for t in targets
             if t.get('type') == 'page' and t.get('webSocketDebuggerUrl')]
    if not pages:
        return None

    def _origin(u):
        try:
            sp = _urlsplit(u or '')
            return (sp.scheme, sp.netloc)
        except Exception:
            return ('', '')

    if want_url:
        for t in pages:                       # exact page we asked for
            if t.get('url') == want_url:
                return t
        wo = _origin(want_url)
        if wo != ('', ''):                    # same site, post-redirect
            for t in pages:
                if _origin(t.get('url')) == wo:
                    return t
    for t in pages:                           # any real page over a blank one
        u = t.get('url') or ''
        if u and not u.startswith(('about:', 'chrome://', 'devtools://')):
            return t
    return pages[0]


def _run_cdp(session):
    """Single CDP thread: connect, drive screencast, dispatch queued commands.

    Owns the websocket end-to-end so there is exactly one sender — input/nav
    handlers enqueue onto session['cmd_queue'] and this loop sends them."""
    import urllib.request
    websocket = _import_ws()
    if websocket is None:
        session['status'] = 'error'
        session['error'] = 'websocket-client not installed'
        return
    port = session['port']
    _id = [100]

    def _next_id():
        _id[0] += 1
        return _id[0]

    try:
        # Wait for the devtools endpoint + a page target.
        page = None
        for _ in range(75):
            if session['status'] != 'running':
                return
            try:
                targets = json.load(urllib.request.urlopen(
                    f'http://127.0.0.1:{port}/json/list', timeout=1))
                page = _pick_page_target(targets, session.get('url') or '')
                if page and page.get('webSocketDebuggerUrl'):
                    break
            except Exception:
                pass
            _time.sleep(0.2)
        if not page:
            session['status'] = 'error'
            session['error'] = 'Chromium devtools endpoint did not come up'
            return

        # connect timeout is generous; the per-recv poll timeout is set below.
        ws = websocket.create_connection(page['webSocketDebuggerUrl'],
                                         max_size=None, timeout=5)
        # 0.1s recv poll: the loop drains queued input at the TOP of each
        # iteration, so this timeout bounds click/scroll/key dispatch latency —
        # 0.5s felt sluggish, 0.1s is snappy. The old black-pane risk (a short
        # timeout firing mid-frame, corrupting the ws and silently killing the
        # thread) is now covered by the consecutive-error tolerance below, and
        # measured safe: a 0.1s reader sustains 50fps with 0 recv errors.
        ws.settimeout(0.1)
        session['ws'] = ws

        def send(method, params=None):
            ws.send(json.dumps({'id': _next_id(), 'method': method, 'params': params or {}}))

        def start_screencast():
            send('Page.startScreencast',
                 {'format': 'jpeg', 'quality': 55, 'maxWidth': VIEW_W,
                  'maxHeight': VIEW_H, 'everyNthFrame': 1})

        send('Page.enable')
        # A background tab renders nothing in headless Chromium, so the tab we
        # attached to must be the frontmost one or every frame is a no-show.
        send('Page.bringToFront')
        send('Emulation.setDeviceMetricsOverride',
             {'width': VIEW_W, 'height': VIEW_H, 'deviceScaleFactor': 1, 'mobile': False})
        # Present a normal (non-headless) User-Agent. Chromium's --headless=new
        # advertises "HeadlessChrome/…", which sites like Hacker News block with
        # a "Sorry." page. Derive from the real browser UA (so the Chrome version
        # always matches) and just strip the Headless marker; also drop the
        # navigator.webdriver bot flag on every new document. Set BEFORE navigate
        # so the first request already carries the clean UA.
        try:
            ver = json.load(urllib.request.urlopen(
                f'http://127.0.0.1:{port}/json/version', timeout=2))
            ua = (ver.get('User-Agent') or '').replace('HeadlessChrome', 'Chrome')
            if ua:
                send('Network.setUserAgentOverride', {'userAgent': ua})
        except Exception as e:
            session['error'] = f'UA override failed: {e}'
        send('Page.addScriptToEvaluateOnNewDocument',
             {'source': 'Object.defineProperty(navigator, "webdriver", {get: () => undefined});'})
        send('Page.navigate', {'url': session['url']})
        start_screencast()

        q = session['cmd_queue']
        errors = 0  # consecutive non-timeout recv errors before we give up
        while session['status'] == 'running':
            # 1. drain queued outbound commands (input / navigate)
            try:
                while True:
                    method, params = q.get_nowait()
                    try:
                        send(method, params)
                    except Exception as e:
                        session['error'] = f'send failed: {e}'
            except queue.Empty:
                pass
            # 2. read one inbound message (poll timeout so we loop back to send)
            try:
                raw = ws.recv()
                errors = 0
            except websocket.WebSocketTimeoutException:
                continue
            except websocket.WebSocketConnectionClosedException:
                session['status'] = 'error'
                session['error'] = 'CDP websocket closed'
                break
            except Exception as e:
                # Transient recv error — do NOT die silently (that froze the
                # pane on a blank frame with status still 'running'). Tolerate a
                # few in a row, then surface an error so the SSE can end.
                errors += 1
                session['error'] = f'recv error: {e}'
                if errors >= 10:
                    session['status'] = 'error'
                    break
                continue
            if not raw:
                continue
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            method = msg.get('method')
            if method == 'Page.screencastFrame':
                p = msg['params']
                session['frame'] = p.get('data')
                # CDP reports the frame's TRUE viewport in CSS px. It is NOT
                # VIEW_W x VIEW_H: Emulation.setDeviceMetricsOverride does not
                # take effect here, so a 1280x800 window yields a 1264x649
                # content viewport once chrome and the scrollbar come out. The
                # pane used to map clicks into a hardcoded 1280x800 space, which
                # put a click at the bottom edge ~150px below where the user
                # aimed. Ship the real numbers so the client scales to them.
                md = p.get('metadata') or {}
                dw, dh = md.get('deviceWidth'), md.get('deviceHeight')
                if dw and dh:
                    session['frame_w'], session['frame_h'] = int(dw), int(dh)
                session['frame_seq'] = session.get('frame_seq', 0) + 1
                try:
                    ws.send(json.dumps({'id': _next_id(),
                                        'method': 'Page.screencastFrameAck',
                                        'params': {'sessionId': p['sessionId']}}))
                except Exception as e:
                    session['status'] = 'error'
                    session['error'] = f'ack failed: {e}'
                    break
            elif method == 'Page.frameNavigated':
                # The pane used to display the URL we REQUESTED, so a redirect to
                # an SSO page (or attaching to the wrong tab) left the bar showing
                # a confident, wrong address over a blank pane. Report the truth.
                fr = (msg.get('params') or {}).get('frame') or {}
                if not fr.get('parentId') and fr.get('url'):
                    session['live_url'] = fr['url']
            elif method == 'Page.frameStoppedLoading':
                # A cross-document navigation (typed URL, clicked link, or a
                # queued Page.navigate) STOPS the active screencast on this ws.
                # Re-arm it on every load, else the pane freezes/blacks after
                # the first navigation away from the launch page.
                try:
                    start_screencast()
                except Exception as e:
                    session['error'] = f'rearm failed: {e}'
    except Exception as e:
        session['status'] = 'error'
        session['error'] = str(e)
    finally:
        # If we fell out of the loop for any reason other than an explicit
        # /stop, make it observable — never leave status 'running' with a dead
        # reader (that is exactly the silent black-pane failure).
        if session.get('status') == 'running':
            session['status'] = 'error'
            session.setdefault('error', 'CDP reader exited unexpectedly')
        try:
            if session.get('ws'):
                session['ws'].close()
        except Exception:
            pass


def _default_profile():
    """Config-set profile used when a launch names none. Empty (the shipped
    default) keeps the original throwaway behaviour."""
    return (state.CONFIG.get('browser_default_profile') or '').strip().lower() or None


def _launch_browser(project_id, url, profile=None, ephemeral=False):
    """Start a headless Chromium and its CDP reader.

    ``profile`` names a persistent user-data-dir that survives teardown; None
    (the default) gets a throwaway one — unless ``browser_default_profile`` is
    configured, which makes unnamed launches persistent so the 🌐 Browser button
    stops signing the user out of everything on every click. ``ephemeral=True``
    opts a single launch back out of that default. Returns ``(session, err)``;
    the session carries ``reused=True`` when an existing one already held that
    profile.
    """
    if profile is None and not ephemeral:
        profile = _default_profile()
    chromium = _find_chromium()
    if not chromium:
        return None, 'Chromium not found (install: pip install playwright && playwright install chromium)'
    if not _import_ws():
        return None, 'websocket-client not installed (pip install websocket-client)'
    if profile is not None:
        profile = (profile or '').strip().lower()
        udd = _profile_dir(profile)
        if not udd:
            return None, (f"invalid profile name '{profile}' — use lowercase "
                          f"letters, digits, '.', '-', '_' (e.g. reddit)")
        # One Chromium per profile dir: a second process on the same dir
        # corrupts it, and the caller almost certainly wants the tab that is
        # already signed in anyway.
        live = _session_using_profile(profile)
        if live:
            live['reused'] = True
            return live, None
    global _swept_orphans
    if not _swept_orphans:
        # Lazy, once per process, and only when the pane is actually used —
        # never from wire()/import (see sweep_orphan_profiles' docstring).
        _swept_orphans = True
        sweep_orphan_profiles()
    sid = uuid.uuid4().hex[:12]
    port = _free_port()
    if profile is None:
        udd = os.path.join(_profiles_root(), sid)
    os.makedirs(udd, exist_ok=True)
    args = [
        chromium, '--headless=new', f'--remote-debugging-port={port}',
        '--remote-allow-origins=*', f'--user-data-dir={udd}',
        '--no-first-run', '--no-default-browser-check', '--disable-gpu',
        f'--window-size={VIEW_W},{VIEW_H}', 'about:blank',
    ]
    try:
        proc = subprocess.Popen(args, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                                creationflags=_POPEN_FLAGS, startupinfo=_STARTUPINFO)
    except Exception as e:
        return None, f'failed to launch Chromium: {e}'
    session = {
        'session_id': sid, 'project_id': project_id, 'proc': proc, 'port': port,
        'url': url or 'about:blank', 'status': 'running', 'frame': None,
        'frame_seq': 0, 'cmd_queue': queue.Queue(), 'ws': None, 'error': None,
        'started_at': datetime.now(timezone.utc).isoformat(),
        # Remembered so teardown can delete this throwaway profile. Without it
        # the dirs accumulated forever (56 dirs / 922 MB observed 2026-07-31).
        'user_data_dir': udd,
        # A named profile is kept; teardown deletes only throwaway dirs.
        'profile': profile,
    }
    with browser_lock:
        browser_sessions[sid] = session
    if _register_process:
        try:
            _register_process(proc.pid, name=f'browser pane ({url or "about:blank"})',
                              type='browser', session_id=sid, project_id=project_id,
                              command_preview=f'chromium --headless (browser pane) :{port}',
                              proc=proc)
        except Exception:
            pass
    t = threading.Thread(target=_run_cdp, args=(session,), daemon=True)
    session['thread'] = t
    t.start()
    return session, None


def _browser_ws_url(port, timeout=2):
    """The BROWSER-level CDP endpoint (not a page target) — the one that accepts
    Browser.close."""
    import urllib.request
    try:
        v = json.load(urllib.request.urlopen(
            f'http://127.0.0.1:{port}/json/version', timeout=timeout))
        return v.get('webSocketDebuggerUrl')
    except Exception:
        return None


def _graceful_close(session, timeout=10):
    """Ask Chromium to shut itself down so it FLUSHES the profile to disk.

    proc.kill() is TerminateProcess/SIGKILL. Chromium holds cookies and
    localStorage in memory and writes them on clean shutdown (or on a lazy
    ~30s timer), so hard-killing right after a login silently discarded that
    login. The saved-profile feature therefore *looked* like it worked — the
    directory persisted, and `/api/browser/profiles` happily listed it — while
    keeping none of the credential it exists to keep.

    Measured on this box 2026-08-05, same profile dir, same launch args:

      kill immediately  -> cookie LOST,     localStorage LOST
      kill after 45s    -> cookie survived, localStorage survived  (lazy flush)
      Browser.close     -> both survived, process exits in ~0.1s

    The middle row is why this read as flaky rather than broken: leave a pane
    open a while and the login sticks; close it right after signing in — the
    normal thing to do — and it is gone.

    Returns True if the process exited on its own (no hard kill needed).
    """
    websocket = _import_ws()
    proc = session.get('proc')
    if websocket is None or proc is None:
        return False
    url = _browser_ws_url(session.get('port'))
    if not url:
        return False
    try:
        ws = websocket.create_connection(url, max_size=None, timeout=3)
        try:
            ws.send(json.dumps({'id': 1, 'method': 'Browser.close', 'params': {}}))
        finally:
            try:
                ws.close()
            except Exception:
                pass
    except Exception as e:
        print(f'[browser] graceful close failed for '
              f'{session.get("session_id")}: {e}', flush=True)
        return False
    try:
        proc.wait(timeout=timeout)
        return True
    except Exception:
        print(f'[browser] Chromium did not exit within {timeout}s after '
              f'Browser.close; falling back to kill', flush=True)
        return False


def _kill_browser_session(session):
    session['status'] = 'stopped'
    proc = session.get('proc')
    # A named profile IS the saved login, so it must reach disk before the
    # process dies — ask Chromium to close itself and only fall back to the
    # hard kill. Throwaway profiles are deleted below, so there is nothing to
    # preserve and no reason to spend time waiting on them.
    closed = False
    if proc and session.get('profile'):
        closed = _graceful_close(session)
    if proc:
        if not closed:
            try:
                proc.kill()
            except Exception:
                pass
        if _unregister_process:
            try:
                _unregister_process(proc.pid)
            except Exception:
                pass
        try:
            proc.wait(timeout=5)
        except Exception:
            pass
    # Drop the throwaway Chromium profile. Must happen AFTER the process is
    # dead, or Chromium rewrites the dir as we delete it (and Windows holds
    # locks on the open files).
    #
    # A NAMED profile is the point of the feature — deleting it here would
    # sign the user out on every close, which is the behaviour this replaced.
    # It is removed only by DELETE /api/browser/profiles/<name>.
    if session.get('profile'):
        return
    udd = session.get('user_data_dir')
    if udd:
        try:
            shutil.rmtree(udd, ignore_errors=True)
        except Exception as e:
            print(f'[browser] profile cleanup failed for {udd}: {e}', flush=True)


@bp.route('/api/browser/launch', methods=['POST'])
def browser_launch():
    data = request.get_json(silent=True) or {}
    project_id = data.get('project_id')
    url = (data.get('url') or 'about:blank').strip()
    if url and not url.startswith(('http://', 'https://', 'about:')):
        url = 'https://' + url
    profile = data.get('profile')
    session, err = _launch_browser(project_id, url, profile=profile or None,
                                   ephemeral=bool(data.get('ephemeral')))
    if err or session is None:
        err = err or 'browser failed to start'
        # A bad profile name is the caller's mistake, not a missing dependency —
        # 501 would send them installing Chromium over a typo.
        return jsonify({'error': err}), (400 if 'profile name' in err else 501)
    reused = bool(session.pop('reused', False))
    if reused and url and url != 'about:blank' and session.get('url') != url:
        # Adopting a signed-in session should still honour where the caller
        # asked to go, or `profile` would silently ignore the URL.
        session['url'] = url
        session['cmd_queue'].put(('Page.navigate', {'url': url}))
    return jsonify({'session_id': session['session_id'], 'url': session['url'],
                    'profile': session.get('profile'), 'reused': reused,
                    'view': {'w': VIEW_W, 'h': VIEW_H}}), (200 if reused else 201)


@bp.route('/api/browser/stream')
def browser_stream():
    sid = request.args.get('session_id')
    session = browser_sessions.get(sid)
    if not session:
        return jsonify({'error': 'unknown session'}), 404

    def gen():
        last = -1
        idle = 0
        while session['status'] == 'running':
            seq = session.get('frame_seq', 0)
            if seq != last and session.get('frame'):
                last = seq
                idle = 0
                payload = json.dumps({'seq': seq, 'img': session['frame'],
                                      'url': session.get('live_url') or session.get('url'),
                                      'w': session.get('frame_w'),
                                      'h': session.get('frame_h')})
                yield f'data: {payload}\n\n'
            else:
                idle += 1
                if idle % 60 == 0:  # ~2s heartbeat keeps the SSE open
                    yield ': ping\n\n'
                _time.sleep(0.033)  # ~30fps delivery cap (was 20fps at 0.05s)
        # final status frame
        yield f'data: {json.dumps({"status": session["status"], "error": session.get("error")})}\n\n'

    return Response(gen(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@bp.route('/api/browser/input', methods=['POST'])
def browser_input():
    data = request.get_json(silent=True) or {}
    sid = data.get('session_id')
    session = browser_sessions.get(sid)
    if not session or session['status'] != 'running':
        return jsonify({'error': 'unknown or stopped session'}), 404
    q = session['cmd_queue']
    kind = data.get('type')
    try:
        if kind == 'mouse':
            # x,y are already in VIEW_W×VIEW_H page coords (pane scales them)
            q.put(('Input.dispatchMouseEvent', {
                'type': data['action'],  # mousePressed | mouseReleased | mouseMoved
                'x': float(data['x']), 'y': float(data['y']),
                'button': data.get('button', 'left'),
                'clickCount': int(data.get('clickCount', 1)),
                'buttons': int(data.get('buttons', 0)),
            }))
        elif kind == 'wheel':
            q.put(('Input.dispatchMouseEvent', {
                'type': 'mouseWheel', 'x': float(data['x']), 'y': float(data['y']),
                'deltaX': float(data.get('deltaX', 0)), 'deltaY': float(data.get('deltaY', 0)),
            }))
        elif kind == 'text':
            q.put(('Input.insertText', {'text': data.get('text', '')}))
        elif kind == 'key':
            base = {'key': data.get('key', ''), 'code': data.get('code', ''),
                    'windowsVirtualKeyCode': int(data.get('keyCode', 0))}
            q.put(('Input.dispatchKeyEvent', {'type': 'keyDown', **base}))
            q.put(('Input.dispatchKeyEvent', {'type': 'keyUp', **base}))
        elif kind == 'navigate':
            url = (data.get('url') or '').strip()
            if url and not url.startswith(('http://', 'https://', 'about:')):
                url = 'https://' + url
            session['url'] = url
            q.put(('Page.navigate', {'url': url}))
        elif kind == 'back':
            # navigate history: use Page.goBack via CDP (needs the entry id) —
            # simplest is JS history.back through Runtime.evaluate.
            q.put(('Runtime.evaluate', {'expression': 'history.back()'}))
        elif kind == 'forward':
            q.put(('Runtime.evaluate', {'expression': 'history.forward()'}))
        elif kind == 'reload':
            q.put(('Page.reload', {}))
        else:
            return jsonify({'error': f'unknown input type: {kind}'}), 400
    except (KeyError, ValueError, TypeError) as e:
        return jsonify({'error': f'bad input payload: {e}'}), 400
    return jsonify({'ok': True})


def _read_page_selection(session):
    """Read the page's current text selection via a short-lived CDP connection,
    kept OFF the reader thread's single-sender websocket to avoid races."""
    import urllib.request
    websocket = _import_ws()
    if websocket is None:
        return ''
    port = session.get('port')
    try:
        targets = json.load(urllib.request.urlopen(
            f'http://127.0.0.1:{port}/json/list', timeout=2))
        # Same picker as the reader thread: attaching to an arbitrary tab is why
        # this used to return '' on any site that opened a second tab.
        page = _pick_page_target(targets, session.get('live_url') or session.get('url') or '')
        if not page or not page.get('webSocketDebuggerUrl'):
            return ''
        ws = websocket.create_connection(page['webSocketDebuggerUrl'],
                                         max_size=None, timeout=3)
        try:
            ws.send(json.dumps({'id': 1, 'method': 'Runtime.evaluate', 'params': {
                'expression': 'window.getSelection().toString()', 'returnByValue': True}}))
            for _ in range(20):
                r = json.loads(ws.recv())
                if r.get('id') == 1:
                    return (r.get('result', {}).get('result', {}) or {}).get('value') or ''
        finally:
            ws.close()
    except Exception:
        return ''
    return ''


@bp.route('/api/browser/selection', methods=['POST'])
def browser_selection():
    """Return the page's current text selection (for copy-out to the host
    clipboard). Paste-in needs no endpoint — the pane inserts host-clipboard
    text via the existing 'text' input type."""
    data = request.get_json(silent=True) or {}
    sid = data.get('session_id')
    session = browser_sessions.get(sid)
    if not session or session['status'] != 'running':
        return jsonify({'error': 'unknown or stopped session'}), 404
    return jsonify({'text': _read_page_selection(session)})


@bp.route('/api/browser/stop', methods=['POST'])
def browser_stop():
    data = request.get_json(silent=True) or {}
    sid = data.get('session_id')
    session = browser_sessions.get(sid)
    if not session:
        return jsonify({'error': 'unknown session'}), 404
    _kill_browser_session(session)
    with browser_lock:
        browser_sessions.pop(sid, None)
    return jsonify({'ok': True})


@bp.route('/api/project/<project_id>/browser/status')
def browser_status(project_id):
    out = []
    for sid, s in list(browser_sessions.items()):
        if s.get('project_id') == project_id:
            out.append({'session_id': sid, 'url': s.get('url'),
                        'status': s.get('status'), 'profile': s.get('profile'),
                        'started_at': s.get('started_at')})
    return jsonify({'sessions': out})


@bp.route('/api/browser/profiles')
def browser_profiles():
    """Saved signed-in profiles. Names and sizes only — a cookie jar is never
    served over HTTP, same rule the secrets vault holds for values."""
    root = _named_profiles_root()
    out = []
    if os.path.isdir(root):
        for name in sorted(os.listdir(root)):
            path = os.path.join(root, name)
            if not os.path.isdir(path):
                continue
            live = _session_using_profile(name)
            try:
                last_used = datetime.fromtimestamp(
                    os.path.getmtime(path), timezone.utc).isoformat()
            except OSError:
                last_used = None
            out.append({'name': name, 'size_mb': round(_dir_size(path) / 1048576, 1),
                        'last_used': last_used,
                        'in_use_by': live.get('session_id') if live else None})
    return jsonify({'profiles': out})


@bp.route('/api/browser/profiles/<name>', methods=['DELETE'])
def browser_profile_delete(name):
    """Forget a saved profile — this is the sign-out."""
    path = _profile_dir(name)
    if not path:
        return jsonify({'error': f"invalid profile name '{name}'"}), 400
    if not os.path.isdir(path):
        return jsonify({'error': f"no saved profile '{name}'"}), 404
    live = _session_using_profile((name or '').strip().lower())
    if live:
        # Deleting the dir under a running Chromium leaves a half-lived browser
        # writing into nothing; make the caller close it deliberately.
        return jsonify({'error': f"profile '{name}' is open in session "
                                 f"{live.get('session_id')} — close it first",
                        'session_id': live.get('session_id')}), 409
    freed = _dir_size(path)
    shutil.rmtree(path, ignore_errors=True)
    print(f"[browser] forgot profile '{name}' "
          f"({freed // (1024 * 1024)} MB, signed out)", flush=True)
    return jsonify({'ok': True, 'forgotten': name})
