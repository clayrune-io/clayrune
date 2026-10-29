"""Keep a LinkedIn Company Page read separate from the signed-in member's feed.

A Company Page needs an explicit admin activity address; a display name cannot
identify a company. No company address is guessed, and automatic discovery stays
off until the address is supplied. A legacy LinkedIn account with no saved kind is
read as a member only when the user gave it their member notifications address and
nothing says Company Page (`effective_kind`). This is account scoping, not a platform
text parser.
"""
from __future__ import annotations

import posixpath
import re
from urllib.parse import urlsplit

from mc.desk_connect.browser_setup_record import kind_of

COMPANY_PAGE_REQUIRED = ('LinkedIn Company Page admin activity address is required; '
                         'member notifications are not read for this account')


def effective_kind(account: dict) -> str | None:
    """The kind this account is READ as, or None when it cannot be told. A recorded kind
    wins. A legacy account with none is a Company Page if it carries an organization id or
    a company admin address, and a member only if the user pointed it at their own member
    notifications and nothing says Company Page. A bare kindless account stays unknown
    (None, read as a Company Page that needs its admin address): legacy accounts predate
    member support, and reading a Company Page's coverage from the member's feed is the
    harm this module exists to stop. Nothing is written to the account: this is a
    read-time default, so a recorded kind always overrides it."""
    recorded = kind_of(account)
    if recorded:
        return recorded
    if account.get('organization_id'):
        return 'organization'
    pages = account.get('read_pages')
    urls = [p['url'] for p in (pages if isinstance(pages, list) else [])
            if isinstance(p, dict) and isinstance(p.get('url'), str)]
    if any(_company_segment(u) for u in urls):
        return 'organization'
    if any(_is_member_notifications(u) for u in urls):
        return 'member'
    return None


def needs_company_page(platform: str, account: dict) -> bool:
    return platform == 'linkedin' and (bool(account.get('organization_id'))
                                        or effective_kind(account) != 'member')


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


def _is_member_notifications(url: str) -> bool:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    path = posixpath.normpath(re.sub(r'%2e', '.', parsed.path, flags=re.IGNORECASE))
    return (parsed.scheme == 'https' and parsed.hostname in ('linkedin.com', 'www.linkedin.com')
            and (path == '/notifications' or path.startswith('/notifications/')))


def is_company_admin(url: str, organization_id: str | None = None, *,
                     configured_url: str | None = None) -> bool:
    """A company admin URL bound to both the saved ID and requested destination."""
    company = _company_segment(url)
    return bool(company and (not organization_id or company == str(organization_id))
                and (configured_url is None or company == _company_segment(configured_url)))


def normalize_activity_url(url: str) -> str:
    """Expand only a bare LinkedIn company admin path to engagement notifications.

    Callers retain their URL validation, company binding and profile-policy gates.
    Specific admin destinations, other sites and non-admin paths stay untouched.
    """
    if not is_company_admin(url):
        return url
    parsed = urlsplit(url)
    if not re.fullmatch(r'/company/[^/]+/admin/?', parsed.path):
        return url
    return parsed._replace(path=parsed.path.rstrip('/') + '/notifications/all/').geturl()
