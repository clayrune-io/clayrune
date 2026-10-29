"""What the Desk reads: one READ TARGET per presence account (backlog 0ddcd44b).

`desk_engagement` used to key readers, coverage and poll results by PLATFORM and take the
project's first account on it, so a project holding both Ron's personal LinkedIn and the
Clayrune Company Page read only one of them. A target is one account; each gets its own
reader and its own coverage row.

The target KEY is what every dict is keyed by (`readers_for_project`, the poll report, the
stored coverage record):
  - the FIRST account on a platform keeps the bare platform name (`linkedin`), so every
    existing stored coverage record, test and UI lookup is unchanged;
  - each further account on that platform is `<platform>:<channel_id>`;
  - a platform the project only published to (no account record) is one target, key =
    platform, account None, read the default way.

Keys can move when an account is removed (the next one becomes first), so a stored coverage
record carries the `account_id` it describes and `record_matches` refuses one that belongs to
another account: it reads as "not read yet", never as someone else's success.
"""
from __future__ import annotations

from typing import Any

from mc import desk as _desk


def _present_accounts(project_id: str) -> list[tuple[str, dict]]:
    """`(platform, resolved account)` for each presence account that names a platform, in
    presence order, one per channel id."""
    from mc import desk_engagement as _eng
    from mc.desk_engagement_account import resolve
    out: list[tuple[str, dict]] = []
    seen: set[str] = set()
    for acc in ((_desk.get_presence(project_id) or {}).get('accounts') or []):
        platform = _eng._account_platform(acc)
        if not platform:
            continue
        cid = acc.get('channel_id')
        if cid:
            if cid in seen:
                continue
            seen.add(cid)
        out.append((platform, resolve(acc)))
    return out


def read_targets(project_id: str) -> list[dict[str, Any]]:
    """`[{key, platform, account, account_id}]`, platforms sorted, accounts in presence order."""
    from mc import desk_engagement as _eng
    by_platform: dict[str, list[dict]] = {}
    for platform, account in _present_accounts(project_id):
        by_platform.setdefault(platform, []).append(account)
    for platform in _eng.project_platforms(project_id):     # published-to, no account record
        by_platform.setdefault(platform, [])
    targets: list[dict[str, Any]] = []
    for platform in sorted(by_platform):
        accounts = by_platform[platform] or [None]
        for i, account in enumerate(accounts):
            cid = account.get('channel_id') if isinstance(account, dict) else None
            key = platform if i == 0 else f'{platform}:{cid or i}'
            targets.append({'key': key, 'platform': platform, 'account': account,
                            'account_id': cid})
    return targets


def target_for(project_id: str, platform: str, key: str | None = None) -> dict | None:
    """The target a caller means: the one with `key`, else the platform's first. None when
    the project is not present on the platform."""
    found = None
    for t in read_targets(project_id):
        if key is not None and t['key'] == key:
            return t
        if found is None and t['platform'] == platform:
            found = t
    return found if key is None else None


def record_matches(rec: dict | None, account_id: str | None) -> bool:
    """A stored coverage record describes this account: it carries no account stamp (written
    before targets, or by a caller with no account) or the same one."""
    stamp = (rec or {}).get('account_id')
    return not stamp or not account_id or stamp == account_id
