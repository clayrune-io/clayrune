"""Every new Desk account starts with Read and Post off (MC-1187 / DCW-C2).

`permission_policy` reads an account with no policy as `legacy`: "no consent
needed". Only two of the five paths that create an account initialized one, so an
X developer-app account, a purpose-bindings account and a pane-wizard account
(Instagram, YouTube, TikTok) started with nothing to deny them. The call now sits
inside `desk_accounts.create_account`, the one place all but the saved-site attach
go through, so a sixth path cannot miss it.

New accounts only. An existing account with no policy keeps reading as legacy: this
is called on a record `create_account` has just built and refuses to run when a
policy is already there, and nothing here walks stored accounts.
"""
from __future__ import annotations

from mc.desk_connect import permission_policy, registry


def initialize(rec: dict, account_kind: str | None = None) -> bool:
    """Write the default-deny policy on a newly built account record.

    Returns False and writes nothing when the platform has no permission profile
    (a blog), or when the platform has more than one account kind and the caller
    did not say which (LinkedIn: member or organization). The kind is part of the
    policy and cannot be changed later, so it is never guessed here.
    """
    profile = registry.profile(rec.get('platform'))
    if not profile:
        return False
    kinds = [k['id'] for k in profile['account_kinds']]
    kind = account_kind or (kinds[0] if len(kinds) == 1 else None)
    if kind is None:
        return False
    permission_policy.initialize_new_account(rec, kind)
    return True
