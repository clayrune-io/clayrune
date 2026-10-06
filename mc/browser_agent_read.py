"""Per-profile "agents may read" policy for the browser pane (backlog 0be19837, MC-1056).

An unattended or dispatched agent may ask for ONE thing from a saved browser profile: a
laundered answer about a page (`POST /api/browser/read-digest`,
mc/blueprints/browser_agent_read_routes.py). Whether it may ask, and of which sites, is a
human decision recorded here, per profile:

    {"profiles": {"<name>": {"enabled": true, "domains": ["linkedin.com", ...]}}}

Rules this file pins:

  * DEFAULT OFF. A profile with no record, a record with `enabled` not exactly True, or an
    empty domain list reads nothing.
  * Stored in `~/.clayrune/browser_agent_read.json` (`clayrune_home()`), never under
    DATA_DIR or the repo: it is operator policy, and nothing in DATA_DIR may be a stray
    sidecar (CLAUDE.md "DATA_DIR pollution").
  * The write path is `set_policy`, called only from a route that has refused an
    unattended caller and checked the dashboard passcode. There is no agent-facing writer.
  * A malformed file reads as "everything off": this module fails closed.

`url_allowed` is the one definition of "is this address on the list", used for the
requested URL and again for wherever the page actually ended up.
"""
from __future__ import annotations

import ipaddress
import json
import re
import threading
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from mc.atomic_json import write_json_atomic
from mc.secrets_store import clayrune_home

MAX_DOMAINS = 20
_LABEL_RE = re.compile(r'^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$')
_PROFILE_RE = re.compile(r'^[a-z0-9][a-z0-9._-]{0,63}$')   # browser_routes._PROFILE_NAME_RE

_lock = threading.Lock()


def policy_path() -> Path:
    return clayrune_home() / 'browser_agent_read.json'


def _load() -> dict[str, dict[str, Any]]:
    try:
        raw = json.loads(policy_path().read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {}
    except Exception as e:
        from mc.core import _log
        _log(f"[browser-agent-read] policy file unreadable, treating every profile as off: {e}",
             flush=True)
        return {}
    profiles = raw.get('profiles') if isinstance(raw, dict) else None
    return profiles if isinstance(profiles, dict) else {}


def normalize_domain(value: Any) -> str | None:
    """A bare hostname (`linkedin.com`), lowercased, or None when it is not one.

    A leading `*.` / `.` is accepted and dropped (a listed domain already covers its
    subdomains). Anything with a scheme, path, port, userinfo or whitespace, an IP literal,
    a single label (`localhost`, `com`) or a non-ASCII name is refused rather than
    "fixed", so what is stored is exactly what `url_allowed` compares against.
    """
    if not isinstance(value, str):
        return None
    d = value.strip().lower()
    if d.startswith('*.'):
        d = d[2:]
    d = d.lstrip('.').rstrip('.')
    if not d or len(d) > 253 or not d.isascii():
        return None
    labels = d.split('.')
    if len(labels) < 2 or not all(_LABEL_RE.match(x) for x in labels):
        return None
    try:
        ipaddress.ip_address(d)
        return None
    except ValueError:
        pass
    if labels[-1].isdigit():          # 1.2.3.4-shaped, or a numeric TLD that is not a name
        return None
    return d


def get_policy(profile: str) -> dict[str, Any]:
    """`{enabled, domains}` for a profile; the default-off record when none is stored."""
    rec = _load().get((profile or '').strip().lower())
    if not isinstance(rec, dict):
        return {'enabled': False, 'domains': []}
    domains = [d for d in (normalize_domain(x) for x in (rec.get('domains') or [])
                           if isinstance(x, str)) if d]
    return {'enabled': rec.get('enabled') is True, 'domains': domains}


def all_policies() -> dict[str, dict[str, Any]]:
    return {name: get_policy(name) for name in _load() if isinstance(name, str)}


def validate_policy(profile: Any, enabled: Any, domains: Any) -> tuple[str, bool, list[str]]:
    """`(name, enabled, domains)` normalised, or ValueError. Split from `set_policy` so a
    route can reject a bad shape before it spends a passcode guess."""
    name = (profile or '').strip().lower() if isinstance(profile, str) else ''
    if not _PROFILE_RE.match(name):
        raise ValueError(f"invalid profile name '{profile}'")
    if not isinstance(enabled, bool):
        raise ValueError('enabled must be true or false')
    if not isinstance(domains, list) or len(domains) > MAX_DOMAINS:
        raise ValueError(f'domains must be a list of at most {MAX_DOMAINS} hostnames')
    clean: list[str] = []
    for raw in domains:
        d = normalize_domain(raw)
        if d is None:
            raise ValueError(f'not a hostname: {raw!r} (give a bare domain such as linkedin.com)')
        if d not in clean:
            clean.append(d)
    if enabled and not clean:
        raise ValueError('turn on agent reads only with at least one allowed domain')
    return name, enabled, clean


def set_policy(profile: Any, enabled: Any, domains: Any) -> dict[str, Any]:
    """Store a profile's policy. The caller (a passcode-gated human route) has already
    checked who is asking; this validates the shape and raises ValueError on a bad one."""
    name, enabled, clean = validate_policy(profile, enabled, domains)
    with _lock:
        profiles = _load()
        profiles[name] = {'enabled': enabled, 'domains': clean}
        _save(profiles)
    return {'enabled': enabled, 'domains': clean}


def clear_policy(profile: str) -> None:
    """Forget a profile's policy. Called when the profile itself is forgotten, so a new
    login saved under the same name does not inherit the old one's permission."""
    name = (profile or '').strip().lower()
    with _lock:
        profiles = _load()
        if profiles.pop(name, None) is not None:
            _save(profiles)


def _save(profiles: dict[str, dict[str, Any]]) -> None:
    path = policy_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(path, {'version': 1, 'profiles': profiles}, indent=1)


def url_host(url: Any) -> str | None:
    """The host Chromium would connect to for `url`, or None when `url` is anything
    other than a plain https address.

    Refused outright rather than parsed around: userinfo (`https://linkedin.com@evil.com`),
    backslashes (`https://evil.com\\@linkedin.com`, where Python and Chromium disagree
    about the host), whitespace and control characters, non-ASCII hosts, IP literals.
    """
    if not isinstance(url, str) or not url or len(url) > 2048:
        return None
    if any(c.isspace() or ord(c) < 0x20 or ord(c) == 0x7f or c == '\\' for c in url):
        return None
    try:
        parts = urlsplit(url)
        host = parts.hostname
        parts.port          # a malformed port raises here
    except ValueError:
        return None
    if parts.scheme != 'https' or parts.username is not None or parts.password is not None:
        return None
    if '@' in parts.netloc or not host:
        return None
    host = host.lower().rstrip('.')
    if not host.isascii() or normalize_domain(host) is None:
        return None
    return host


def url_allowed(url: Any, domains: list[str]) -> bool:
    """True when `url`'s host is exactly a listed domain or a subdomain of one."""
    host = url_host(url)
    if host is None:
        return False
    return any(host == d or host.endswith('.' + d) for d in domains)
