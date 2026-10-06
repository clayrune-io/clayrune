"""What the generic pane reader has learned about where an account's pages are
(`mc/desk_engagement_pane_digest.py`), kept in the Desk store beside the read coverage:

    store['engagement']['pages'][project_id][platform] = {
        'pages': [{'role', 'url', 'source': 'discovered', 'found_at', 'verified'}],
        'status': 'ok' | 'pages_needed' | None,
        'reason': str | None,                # why the pages are needed
        'last_discovery_at': iso | None,     # every attempt; the once-a-day limit reads it
    }

A separate record, not a field on the presence account: the Presence editor replaces
`accounts` wholesale and would drop it. Pages a human typed live on the account itself
(`read_pages`) and are never written here. Never raises on a missing record.
"""
from __future__ import annotations

from typing import Any

from mc import desk as _desk


def get(project_id: str, platform: str) -> dict[str, Any]:
    with _desk._store_lock:
        pages = (_desk._read_store().get('engagement') or {}).get('pages') or {}
    rec = (pages.get(project_id) or {}).get(platform)
    return dict(rec) if isinstance(rec, dict) else {}


def update(project_id: str, platform: str, **fields: Any) -> dict[str, Any]:
    """Merge `fields` onto the record (creating it) and return the stored record."""
    with _desk._store_lock:
        store = _desk._read_store()
        eng = store.setdefault('engagement', {})
        rec = eng.setdefault('pages', {}).setdefault(project_id, {}).setdefault(platform, {})
        rec.update(fields)
        _desk._write_store(store)
        return dict(rec)
