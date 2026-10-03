"""Claude subscription usage windows (5h / 7d / per-model %) — one source seam.

Carved out of ``mc/blueprints/system_routes.py`` (backlog 1d940d0f) so the
*source* of the numbers is a config choice, not hard-wired to this machine's
``~/.claude/.credentials.json``:

  ``claude_usage_source`` = ``local`` (default, today's behaviour)
      Read the CLI's own OAuth token from ``~/.claude/.credentials.json`` and
      call Anthropic's undocumented usage endpoint — the same call the CLI
      ``/usage`` command makes. Unchanged from before the carve-out.

  ``claude_usage_source`` = ``runner``
      NEVER open ``.credentials.json``. Read a numbers-only usage document
      that a runner *inside the customer's pod* publishes
      (``tools/pod-usage-runner/``). Location is ``claude_usage_runner_doc``:
      a filesystem path or an ``http(s)://`` URL. Why: in the hosted split the
      Clayrune service sits OUTSIDE the pod, and a service that reads the
      pod's credential file is "collecting" Claude credentials
      (clayrune-cloud/docs/research/SIGNIN_CHANNEL_REVIEW.md).

Contract shared by both sources: return the parsed windows dict
(``{five_hour, seven_day, seven_day_opus, seven_day_sonnet, extra_usage,
limits}``; each window ``{utilization: 0-100, resets_at: ISO8601}``) or None
on ANY failure — the UI then falls back to the header-derived window. Failure
is logged, never raised.

``_oauth_usage_cache`` is the same dict object ``system_routes`` re-exports;
several callers read ``['ts']`` as the observation epoch and the refresh route
zeroes it, so it stays a module-level dict.
"""

import json
import time as _time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from mc import state
from mc.core import _log

# The User-Agent MUST start with `claude-code/` or Anthropic routes the
# request to an aggressively throttled bucket (persistent 429s).
_OAUTH_USAGE_TTL = 60.0  # seconds
_oauth_usage_cache: dict = {'ts': 0.0, 'data': None}

USAGE_SOURCES = ('local', 'runner')

# A runner document older than this is a dead runner, not a live reading.
# Showing its % as "now" would break the real-numbers-only rule, so it reads
# as unavailable (None), like an expired local token.
_RUNNER_MAX_AGE_SECS = 15 * 60
_RUNNER_MAX_BYTES = 64 * 1024
_RUNNER_FETCH_TIMEOUT = 6

# What the usage UI and the sampler actually read. Anything else in a runner
# document is dropped.
_RUNNER_KEYS = ('five_hour', 'seven_day', 'seven_day_opus', 'seven_day_sonnet',
                'extra_usage', 'limits')
_SANITIZE_MAX_DEPTH = 5
_SANITIZE_MAX_STR = 80


def usage_source() -> str:
    """Configured source, normalised. Unset/blank -> 'local' (the default).
    A NON-blank unknown value -> 'runner': this key exists to keep the
    service off the credential file, so a typo ('runer') must land on the side
    that never opens it — worst case the usage bars read unavailable."""
    v = str(state.CONFIG.get('claude_usage_source') or 'local').strip().lower()
    return v if v in USAGE_SOURCES else 'runner'


def fetch_oauth_usage_limits():
    """Return the parsed usage windows dict, or None on any failure.

    Cached for ``_OAUTH_USAGE_TTL`` seconds whichever source is selected.
    """
    now = _time.time()
    cached = _oauth_usage_cache.get('data')
    if cached is not None and (now - _oauth_usage_cache.get('ts', 0.0)) < _OAUTH_USAGE_TTL:
        return cached
    if usage_source() == 'runner':
        data = _read_runner_document()
    else:
        data = _fetch_local()
    if data is not None:
        _oauth_usage_cache['ts'] = now
        _oauth_usage_cache['data'] = data
    return data


def _fetch_local():
    """Today's behaviour, byte for byte: credentials file -> usage endpoint."""
    try:
        cred_path = Path.home() / '.claude' / '.credentials.json'
        creds = json.loads(cred_path.read_text(encoding='utf-8'))
        oauth = creds.get('claudeAiOauth') or {}
        token = oauth.get('accessToken')
        if not token:
            return None
        ver = state._LAST_SYSTEM_STATUS.get('claude_code_version') or '2.0.0'
        req = urllib.request.Request(
            'https://api.anthropic.com/api/oauth/usage',
            headers={
                'Authorization': f'Bearer {token}',
                'anthropic-beta': 'oauth-2025-04-20',
                'User-Agent': f'claude-code/{ver}',
                'Content-Type': 'application/json',
            },
            method='GET',
        )
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        if isinstance(data, dict):
            return data
        return None
    except Exception as e:
        _log(f"[system_usage] oauth usage fetch failed: {e}", flush=True)
        return None


def _sanitize(node, depth: int = 0):
    """Keep numbers, bools, None and SHORT strings; recurse dicts/lists to a
    bounded depth. A runner document is data from outside this process — it
    must stay a numbers document, not a channel for arbitrary text into the
    dashboard. The only strings that legitimately survive are timestamps,
    window kinds and model display names."""
    if depth > _SANITIZE_MAX_DEPTH:
        return None
    if node is None or isinstance(node, (bool, int, float)):
        return node
    if isinstance(node, str):
        return node if len(node) <= _SANITIZE_MAX_STR else None
    if isinstance(node, dict):
        return {str(k)[:_SANITIZE_MAX_STR]: _sanitize(v, depth + 1)
                for k, v in list(node.items())[:64]}
    if isinstance(node, list):
        return [_sanitize(v, depth + 1) for v in node[:64]]
    return None


def _parse_sampled_at(value) -> Optional[float]:
    try:
        dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _read_runner_raw(location: str):
    """Return (raw_bytes, mtime_epoch_or_None). Raises on any failure."""
    if location.lower().startswith(('http://', 'https://')):
        with urllib.request.urlopen(location, timeout=_RUNNER_FETCH_TIMEOUT) as resp:
            return resp.read(_RUNNER_MAX_BYTES + 1), None
    p = Path(location)
    with open(p, 'rb') as fh:
        raw = fh.read(_RUNNER_MAX_BYTES + 1)
    return raw, p.stat().st_mtime


def _read_runner_document():
    """Numbers-only usage document -> windows dict, or None. Never touches
    ``.credentials.json``: the only file this opens is the configured document
    path."""
    location = str(state.CONFIG.get('claude_usage_runner_doc') or '').strip()
    if not location:
        _log("[system_usage] claude_usage_source=runner but "
             "claude_usage_runner_doc is not set", flush=True)
        return None
    try:
        raw, mtime = _read_runner_raw(location)
        if len(raw) > _RUNNER_MAX_BYTES:
            raise ValueError(f'document larger than {_RUNNER_MAX_BYTES} bytes')
        doc = json.loads(raw.decode('utf-8'))
        if not isinstance(doc, dict):
            raise ValueError('document is not a JSON object')
        # Freshness: the runner's own stamp when it wrote one, else the
        # file's mtime (path sources only). No usable stamp = unknowable age
        # = not shown as live.
        sampled = _parse_sampled_at(doc.get('sampled_at')) if doc.get('sampled_at') else mtime
        if sampled is None:
            raise ValueError('document has no usable sampled_at')
        age = _time.time() - sampled
        if age > _RUNNER_MAX_AGE_SECS:
            raise ValueError(f'document is {int(age)}s old (max {_RUNNER_MAX_AGE_SECS}s)')
        out = {k: _sanitize(doc[k]) for k in _RUNNER_KEYS if k in doc}
        return out or None
    except Exception as e:
        _log(f"[system_usage] runner usage document unavailable: {e}", flush=True)
        return None
