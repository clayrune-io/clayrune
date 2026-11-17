"""Test helper: turn freshly created accounts into LEGACY ones (no consent policy).

`desk_accounts.create_account` now starts every new account with Read and Post off
(MC-1187 DCW-C2). Tests whose subject is something else (the tick, a publisher, a
binding) used to get a legacy account from it by accident. They call this to say so
out loud: the account has no policy, which `permission_policy.read_policy` reads as
`state: legacy`, exactly what stored accounts from before the change look like.
"""
from __future__ import annotations

from mc import desk


def make_legacy(*account_ids: str) -> None:
    with desk._store_lock:
        store = desk._read_store()
        for aid in account_ids:
            store['accounts'][aid].pop('permission_policy', None)
            store['accounts'][aid].pop('permission_policy_version', None)
        desk._write_store(store)
