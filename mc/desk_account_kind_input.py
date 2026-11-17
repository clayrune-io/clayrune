"""The account kind on the generic create route (MC-1187 follow-up F1).

`POST /api/desk/accounts` used to ignore any kind, so a LinkedIn account made there
got no consent policy and read as a legacy account ("no consent needed").
`desk_accounts.create_account` already initializes the policy when it is given a
kind; this checks what the route was given. The kind is part of the policy and
cannot be changed later, so it is never defaulted here: a platform with more than
one kind (LinkedIn: member or organization) refuses a request that names none.
"""
from __future__ import annotations

from mc.desk_connect import registry


def checked_kind(platform, kind):
    """The kind to hand to `create_account`, or an `AccountError` (400) naming the options.

    A platform with one kind (X, Instagram, YouTube, TikTok) needs none: `None` goes
    through and `create_account` uses the only kind. A platform with no permission
    profile (a blog) has no kinds and refuses one.
    """
    from mc.desk_accounts import AccountError
    profile = registry.profile(platform)
    kinds = [k['id'] for k in profile['account_kinds']] if profile else []
    if kind is None:
        if len(kinds) > 1:
            raise AccountError(f'say which kind of {platform} account this is: '
                               f'account_kind must be one of {", ".join(kinds)}')
        return None
    if not kinds:
        raise AccountError(f'a {platform} account has no account kind')
    if not isinstance(kind, str) or kind not in kinds:
        raise AccountError(f'account_kind must be one of {", ".join(kinds)}')
    return kind
