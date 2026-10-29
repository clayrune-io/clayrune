"""Keep a LinkedIn Company Page read separate from the signed-in member's feed.

Legacy LinkedIn accounts predate member support and have no saved kind. They
also need an explicit admin activity address; a display name cannot identify a
company. No company address is guessed, and automatic discovery stays off until
the address is supplied. This is account scoping, not a platform text parser.
"""
from __future__ import annotations

import posixpath
import re
from urllib.parse import urlsplit

from mc.desk_connect.browser_setup_record import kind_of

COMPANY_PAGE_REQUIRED = ('LinkedIn Company Page admin activity address is required; '
                         'member notifications are not read for this account')


def needs_company_page(platform: str, account: dict) -> bool:
    return platform == 'linkedin' and (bool(account.get('organization_id'))
                                        or kind_of(account) != 'member')


def _company_segment(url: str) -> str | None:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    path = re.sub(r'%2e', '.', parsed.path, flags=re.IGNORECASE)
    if any(segment in ('.', '..') for segment in path.split('/')):
        return None
    match = re.match(r'^/company/([^/]+)/admin(?:/|$)', posixpath.normpath(path))
    if (parsed.scheme == 'https' and parsed.hostname in ('linkedin.com', 'www.linkedin.com')
            and match is not None):
        return match[1]
    return None


def is_company_admin(url: str, organization_id: str | None = None, *,
                     configured_url: str | None = None) -> bool:
    """A company admin URL bound to both the saved ID and requested destination."""
    company = _company_segment(url)
    return bool(company and (not organization_id or company == str(organization_id))
                and (configured_url is None or company == _company_segment(configured_url)))
