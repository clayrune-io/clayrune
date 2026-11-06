"""Daily update-check ping, so the number of real Clayrune installs can be counted.

WHAT IS SENT. Once per 24 hours per install, one HTTPS POST of exactly three
fields and nothing else::

    {"install_id": "<random uuid>", "version": "<build>", "os": "windows|darwin|linux"}

`install_id` here is NOT `mc.project_sync.get_install_id()`: that one embeds the
machine's hostname (``ron-laptop-a3f9c2d1``). This is a separate random UUID kept
in ``~/.clayrune/ping_id``, created the first time a ping is actually about to
be sent (a machine that never pings never gets the file). The response may carry
``latest_version``; the dashboard shows it as an "update available" notice.

WHEN NOTHING IS SENT (every one of these is checked before the id is read or a
socket is opened; `tests/test_update_ping.py` proves zero requests for each):
  * ``DO_NOT_TRACK`` or ``CLAYRUNE_NO_PING`` set to 1/true/yes in the environment
    -- a hard off that no setting overrides;
  * config ``update_ping_enabled`` is false (Settings -> System; ON by default);
  * the user has not yet been shown the disclosure (config
    ``update_ping_disclosed``, set only by ``POST /api/update-ping/seen`` or
    ``/choice`` after the first-run step or the one-time notice was displayed);
  * the last attempt was under 24 hours ago -- a FAILED attempt counts, so
    "at most once per 24h" holds even when the endpoint is down;
  * the endpoint URL is not https (loopback http is allowed for local testing).

The gates are checked again, under the config writers' lock, and the request
bytes are written inside that same critical section (`_gate_and_send`). So an
opt-out save (PUT /api/config, /api/update-ping/choice) either finishes first,
and no request byte is ever written, or waits for the write already under way:
once a save has returned success, no request bytes leave the machine afterwards.

The network is kept OUT of that lock. The request is done in three steps
(`_HttpPost`): resolve + TCP + TLS (``connect``) with no lock held; gate re-check
and the ~1KB request write (``send``) under the lock; then the response is read
with no lock held. A save therefore never waits on DNS, the handshake or the
server: the longest it can wait is one local socket write of ~1KB, hard-capped
by the socket timeout (TIMEOUT_S). If an opt-out lands after ``connect`` but
before ``send``, the open connection is closed unused: a TCP/TLS handshake to the
endpoint may have happened (the server can see an address and a hostname, never
an id, version or request), but no request bytes were written. A request already
written cannot be recalled. The slow work (version lookup, id file) also happens
before the lock is taken. If an opt-out lands during the id-file step, the random
id may already have been created (nothing is sent).

The endpoint is config ``update_ping_url`` (default below). It is deliberately
not on the Settings-editable list: an agent session must not be able to point
the ping somewhere else. Redirects are refused, the response is size-capped,
and ``latest_version`` is validated before it is stored.

State (last attempt, last success, latest version) lives in a sidecar OUTSIDE
data/projects/ (DATA_DIR pollution rule, CLAUDE.md): ``data/update_ping_state.json``,
wired by server.py. Every outward dependency (clock, HTTP, env, platform) is a
parameter so tests never touch the network.
"""
from __future__ import annotations

import http.client
import json
import os
import platform
import re
import threading
import time
import urllib.parse
import uuid
from pathlib import Path
from typing import Callable, Optional

from mc.atomic_json import write_json_atomic

DEFAULT_URL = 'https://clayrune.io/api/ping'
INTERVAL_S = 24 * 3600
TICK_S = 900                      # how often the daemon re-checks the gates
BOOT_DELAY_S = 120
TIMEOUT_S = 10
MAX_RESPONSE_BYTES = 4096
ENV_OFF_VARS = ('DO_NOT_TRACK', 'CLAYRUNE_NO_PING')
_VERSION_RE = re.compile(r'^[0-9A-Za-z][0-9A-Za-z._+\- ()]{0,63}$')
_SEMVER_RE = re.compile(r'v?(\d+(?:\.\d+)*)')

STATE_PATH: Optional[Path] = None
PING_ID_PATH: Optional[Path] = None
_config_fn: Callable[[], dict] = lambda: {}
_version_fn: Callable[[], str] = lambda: ''
_lock = threading.Lock()
_STATE: dict = {}


def wire(state_path, config_fn: Callable[[], dict], version_fn: Callable[[], str],
         ping_id_path=None) -> None:
    """Called once by server.py. `config_fn` returns the live CONFIG dict."""
    global STATE_PATH, PING_ID_PATH, _config_fn, _version_fn
    STATE_PATH = Path(state_path)
    PING_ID_PATH = Path(ping_id_path) if ping_id_path else Path.home() / '.clayrune' / 'ping_id'
    _config_fn = config_fn
    _version_fn = version_fn
    _load()


def _load() -> None:
    global _STATE
    _STATE = {}
    if STATE_PATH is None or not STATE_PATH.exists():
        return
    try:
        data = json.loads(STATE_PATH.read_text(encoding='utf-8'))
        if isinstance(data, dict):
            _STATE = data
    except Exception as e:
        from mc.core import _log
        _log(f"[update-ping] state unreadable, starting empty: {e}", flush=True)


def _save() -> None:
    if STATE_PATH is None:
        return
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(STATE_PATH, _STATE)
    except Exception as e:
        from mc.core import _log
        _log(f"[update-ping] could not save state: {e}", flush=True)


# ── gates ────────────────────────────────────────────────────────────────────

def env_disabled(environ=None) -> bool:
    env = os.environ if environ is None else environ
    return any(str(env.get(k, '')).strip().lower() in ('1', 'true', 'yes') for k in ENV_OFF_VARS)


def user_enabled(cfg: dict) -> bool:
    """The Settings toggle. ON unless explicitly false (default ON, Ron 2026-11-05)."""
    return cfg.get('update_ping_enabled', True) is True


def disclosed(cfg: dict) -> bool:
    return cfg.get('update_ping_disclosed') is True


def _url(cfg: dict) -> Optional[str]:
    raw = str(cfg.get('update_ping_url') or DEFAULT_URL).strip()
    p = urllib.parse.urlparse(raw)
    if p.scheme == 'https' and p.netloc:
        return raw
    if p.scheme == 'http' and p.hostname in ('localhost', '127.0.0.1', '::1'):
        return raw
    return None


def blocked_reason(cfg: dict, environ=None) -> Optional[str]:
    """Why a ping must not be sent right now, ignoring the 24h clock; None = allowed."""
    if env_disabled(environ):
        return 'env_off'
    if not user_enabled(cfg):
        return 'disabled'
    if not disclosed(cfg):
        return 'not_disclosed'
    if _url(cfg) is None:
        return 'bad_url'
    return None


# ── payload ──────────────────────────────────────────────────────────────────

def get_ping_id() -> str:
    """Random per-install UUID, created on first use. Never derived from the host."""
    p = PING_ID_PATH or (Path.home() / '.clayrune' / 'ping_id')
    try:
        saved = p.read_text(encoding='utf-8').strip()
        if saved and re.fullmatch(r'[0-9a-f]{32}', saved):
            return saved
    except Exception:
        pass
    new = uuid.uuid4().hex
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(new, encoding='utf-8')
    return new


def os_name() -> str:
    return {'Windows': 'windows', 'Darwin': 'darwin', 'Linux': 'linux'}.get(platform.system(), 'other')


def build_payload(version: Optional[str] = None) -> dict:
    """Exactly three keys. Anything added here must also change the disclosure text.

    `version` lets the sender read the (slow, `git describe`) version BEFORE its
    final eligibility recheck and build the rest after it; omitted, it is read here.
    """
    if version is None:
        version = str(_version_fn() or '')[:64]
    return {'install_id': get_ping_id(), 'version': version, 'os': os_name()}


# ── version compare ──────────────────────────────────────────────────────────

def _semver(text: str):
    m = _SEMVER_RE.match((text or '').strip())
    return tuple(int(x) for x in m.group(1).split('.')) if m else None


def is_newer(latest: str, current: str) -> bool:
    a, b = _semver(latest), _semver(current)
    return a is not None and b is not None and a > b


# ── transport ────────────────────────────────────────────────────────────────

class _HttpPost:
    """One JSON POST in three steps, so the config lock covers only the middle one.

    connect(): DNS + TCP + TLS, NO lock held (nothing bounds the resolver but the
    OS). send(): write the ~1KB request; called by `_gate_and_send` under the
    config lock. read(): the reply, again with no lock. Redirects are never
    followed (http.client does not follow them; any non-2xx status, 3xx included,
    raises). The reply is size-capped.
    """

    def __init__(self, url: str, payload: dict):
        p = urllib.parse.urlsplit(url)
        self._https = p.scheme == 'https'
        self._host = p.hostname or ''
        self._port = p.port
        self._target = (p.path or '/') + ('?' + p.query if p.query else '')
        self._body = json.dumps(payload).encode('utf-8')
        self._conn: Optional[http.client.HTTPConnection] = None

    def connect(self) -> None:
        cls = http.client.HTTPSConnection if self._https else http.client.HTTPConnection
        self._conn = cls(self._host, self._port, timeout=TIMEOUT_S)
        self._conn.connect()

    def send(self) -> None:
        assert self._conn is not None
        self._conn.request('POST', self._target, body=self._body, headers={
            'Content-Type': 'application/json', 'User-Agent': 'Clayrune',
            'Connection': 'close'})

    def read(self) -> dict:
        """Parsed JSON object of the reply ({} if none). Runs outside the lock."""
        assert self._conn is not None
        resp = self._conn.getresponse()
        if not 200 <= resp.status < 300:
            raise ValueError(f'HTTP {resp.status}')
        # The lock is not held here; this deadline only stops a server that drips
        # its body from parking the ping thread (and its recorded attempt) forever.
        deadline = time.monotonic() + TIMEOUT_S
        body = b''
        while len(body) <= MAX_RESPONSE_BYTES:
            if time.monotonic() > deadline:
                raise TimeoutError('response too slow')
            chunk = resp.read1(1024)    # one socket read, so the deadline is checked between bytes
            if not chunk:
                break
            body += chunk
        if len(body) > MAX_RESPONSE_BYTES:
            raise ValueError('response too large')
        data = json.loads(body.decode('utf-8')) if body.strip() else {}
        return data if isinstance(data, dict) else {}

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass


class _CallablePost:
    """Test seam: an injected `post_fn(url, payload)` stands in for the whole request.

    It runs inside the send step, i.e. under the config lock, which is what the
    tests that pin the opt-out ordering rely on. connect/read are no-ops.
    """

    def __init__(self, fn: Callable[[str, dict], dict], url: str, payload: dict):
        self._fn, self._url, self._payload = fn, url, payload
        self._reply: dict = {}

    def connect(self) -> None:
        pass

    def send(self) -> None:
        self._reply = self._fn(self._url, self._payload) or {}

    def read(self) -> dict:
        return self._reply

    def close(self) -> None:
        pass


def _recheck(environ=None) -> Optional[str]:
    """Eligibility check under the config writers' lock. Returns the reason, or None.

    Used as an early-out after the version lookup. It is NOT the guarantee:
    the lock is released on return, so an opt-out can still land before the
    request. `_gate_and_send` is the check that holds the lock through the send.
    """
    from mc import provider_preferences
    return provider_preferences.serialized_write(
        lambda: blocked_reason(_config_fn() or {}, environ))()


def _gate_and_send(transport, environ=None) -> Optional[str]:
    """Final gate AND the request write in ONE critical section of the config lock.

    `transport` is already connected (that happened with no lock held). The lock is
    the one PUT /api/config and the /api/update-ping/* routes take for the whole
    read/merge/write + live-dict update. Holding it from the gate through
    `transport.send()` means an opt-out save either completed before the gate (we
    see it, return its reason, write nothing) or blocks until the ~1KB write returns
    (the request was already past the point of no return). The hold is one local
    socket write, bounded by the socket timeout; it never spans DNS, the handshake
    or the response. Returns the blocking reason, or None once the request has been
    written. Send errors propagate to the caller.

    Lock order is always module `_lock` -> config write lock. Nothing that holds
    the config write lock takes `_lock` (the routes call only `status()` and
    `blocked_reason`, both lock-free), so there is no cycle.
    """
    from mc import provider_preferences

    def _run():
        reason = blocked_reason(_config_fn() or {}, environ)
        if reason:
            return reason
        transport.send()
        return None
    return provider_preferences.serialized_write(_run)()


def maybe_ping(now: Optional[float] = None, post_fn: Optional[Callable[[str, dict], dict]] = None,
               environ=None) -> dict:
    """One decision pass. Sends at most one request. Never raises."""
    cfg = _config_fn() or {}
    reason = blocked_reason(cfg, environ)
    url = _url(cfg)
    if reason or url is None:
        return {'sent': False, 'reason': reason or 'bad_url'}
    t = time.time() if now is None else now
    with _lock:
        last = _STATE.get('last_attempt_ts') or 0
        if last and t - last < INTERVAL_S:
            return {'sent': False, 'reason': 'not_due'}
        # Record the attempt BEFORE the request: a crash or hang mid-request
        # must not turn into a retry storm.
        prior_attempt = _STATE.get('last_attempt_ts')
        _STATE['last_attempt_ts'] = t
        _save()
        try:
            version = str(_version_fn() or '')[:64]      # slow: may run `git describe`
            reason = _recheck(environ)                    # early-out: skip the id file if already opted out
            payload: dict = {}
            reply: dict = {}
            if not reason:
                payload = build_payload(version)          # id file I/O, deliberately BEFORE the gate
                transport = (_CallablePost(post_fn, url, payload) if post_fn
                             else _HttpPost(url, payload))
                try:
                    transport.connect()                   # DNS/TCP/TLS: NO lock held
                    reason = _gate_and_send(transport, environ)   # lock: gate + request write
                    if not reason:
                        reply = transport.read()          # response: NO lock held
                finally:
                    transport.close()                     # unused if the gate said no: no bytes written
            if reason:
                # Nothing was sent, so this must not eat the day.
                if prior_attempt is None:
                    _STATE.pop('last_attempt_ts', None)
                else:
                    _STATE['last_attempt_ts'] = prior_attempt
                _save()
                return {'sent': False, 'reason': reason}
        except Exception as e:
            _STATE['last_error'] = str(e)[:200]
            _save()
            return {'sent': True, 'ok': False, 'error': _STATE['last_error']}
        _STATE['last_success_ts'] = t
        _STATE.pop('last_error', None)
        latest = reply.get('latest_version')
        if isinstance(latest, str) and _VERSION_RE.match(latest.strip()):
            _STATE['latest_version'] = latest.strip()
        _save()
        return {'sent': True, 'ok': True, 'payload': payload}


def status() -> dict:
    """What the dashboard needs. `latest_version` is withheld while the ping is off."""
    cfg = _config_fn() or {}
    env_off = env_disabled()
    on = user_enabled(cfg) and not env_off
    current = ''
    try:
        current = str(_version_fn() or '')
    except Exception:
        pass
    latest = _STATE.get('latest_version', '') if on else ''
    return {
        'enabled': user_enabled(cfg),
        'env_disabled': env_off,
        'disclosed': disclosed(cfg),
        'current_version': current,
        'latest_version': latest,
        'update_available': bool(latest) and is_newer(latest, current),
        'last_success_ts': _STATE.get('last_success_ts'),
    }


# ── daemon ───────────────────────────────────────────────────────────────────

def _loop() -> None:
    time.sleep(BOOT_DELAY_S)
    while True:
        try:
            maybe_ping()
        except Exception as e:  # maybe_ping never raises; belt and braces for the thread
            from mc.core import _log
            _log(f"[update-ping] loop error: {e}", flush=True)
        time.sleep(TICK_S)


def start() -> None:
    """Server-process only (server.py boot). Importing this module starts nothing."""
    if STATE_PATH is None:
        return
    threading.Thread(target=_loop, daemon=True, name='update-ping').start()
