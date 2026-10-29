"""Keep a LinkedIn Company Page read separate from the signed-in member's feed.

Legacy LinkedIn accounts predate member support and have no saved kind. They
also need an explicit admin activity address; a display name cannot identify a
company. No company address is guessed, and automatic discovery stays off until
the address is supplied. This is account scoping, not a platform text parser.
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit

from mc.desk_connect.browser_setup_record import kind_of

COMPANY_PAGE_REQUIRED = ('LinkedIn Company Page admin activity address is required; '
                         'member notifications are not read for this account')


def needs_company_page(platform: str, account: dict) -> bool:
    return platform == 'linkedin' and (bool(account.get('organization_id'))
                                        or kind_of(account) != 'member')


def is_company_admin(url: str, organization_id: str | None = None) -> bool:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    match = re.match(r'^/company/([^/]+)/admin(?:/|$)', parsed.path)
    return (parsed.scheme == 'https' and parsed.hostname in ('linkedin.com', 'www.linkedin.com')
            and match is not None and (not organization_id or match[1] == str(organization_id)))
