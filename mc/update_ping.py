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

The gates are checked again, under the config writers' lock, immediately before
the request (after the slow version lookup), so an opt-out saved while the
sender was working is honoured and no new request starts after it returns.
A request already on the wire cannot be recalled.

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

import json
import os
import platform
import re
import threading
import time
import urllib.parse
import urllib.request
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

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _http_post(url: str, payload: dict) -> dict:
    """POST JSON, no redirects, size-capped. Returns the parsed JSON object ({} if none)."""
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode('utf-8'), method='POST',
        headers={'Content-Type': 'application/json', 'User-Agent': 'Clayrune'})
    opener = urllib.request.build_opener(_NoRedirect)
    with opener.open(req, timeout=TIMEOUT_S) as resp:
        body = resp.read(MAX_RESPONSE_BYTES + 1)
    if len(body) > MAX_RESPONSE_BYTES:
        raise ValueError('response too large')
    data = json.loads(body.decode('utf-8')) if body.strip() else {}
    return data if isinstance(data, dict) else {}


def _recheck(environ=None) -> Optional[str]:
    """Final eligibility check, taken under the config writers' lock.

    The same lock covers every config write (PUT /api/config and the
    /api/update-ping/* routes, which update the live dict inside it), so an
    opt-out that has returned HTTP 200 is always visible here, and one still
    in progress is waited for. Without this, the gates in `maybe_ping` ran once
    before the slow version lookup and an OFF saved during it still got a POST.
    Returns the blocking reason, or None if the ping may go.
    """
    from mc import provider_preferences
    return provider_preferences.serialized_write(
        lambda: blocked_reason(_config_fn() or {}, environ))()


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
            reason = _recheck(environ)                    # an opt-out may have landed meanwhile
            if reason:
                # Nothing was sent, so this must not eat the day.
                if prior_attempt is None:
                    _STATE.pop('last_attempt_ts', None)
                else:
                    _STATE['last_attempt_ts'] = prior_attempt
                _save()
                return {'sent': False, 'reason': reason}
            payload = build_payload(version)
            reply = (post_fn or _http_post)(url, payload)
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
