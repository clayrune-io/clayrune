"""Resolve a presence binding to its account metadata without consulting the vault."""
from __future__ import annotations

from mc import desk


def resolve(binding: dict) -> dict:
    """Presence owns the read settings; the workspace account owns identity/kind."""
    account_id = binding.get('channel_id')
    with desk._store_lock:
        account = (desk._read_store().get('accounts') or {}).get(account_id)
    if not isinstance(account, dict) or account.get('platform') != binding.get('platform'):
        return dict(binding)
    return {**account, **binding}
