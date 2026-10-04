"""What a pasted service address may be (spec "Detection and trust").

Pure and offline: nothing here resolves a name or opens a connection. A later
slice that fetches a page must repeat the network restrictions at connect time
(redirects, DNS rebinding); this file only refuses an address that could never
be a public service.

Refused, each with a sentence that says how to paste a clean one: not https, over
300 characters, userinfo (`user:pw@`), a query or fragment, an explicit port other
than 443, localhost and the reserved internal names, a private / loopback /
link-local / reserved IP, a name whose last label is not letters (`127.1`,
`0x7f.1`), and Clayrune's own origins.
"""
from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlsplit

from mc.desk_connect import registry as _registry

MAX_URL = 300

# Last labels that name a private or reserved network, never a public service.
_RESERVED_TLDS = frozenset({
    'localhost', 'local', 'internal', 'lan', 'home', 'corp', 'localdomain', 'intranet',
    'invalid', 'test', 'arpa', 'onion',
})
_LABEL = re.compile(r'^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$')
_TLD = re.compile(r'^([a-z]{2,63}|xn--[a-z0-9-]{1,59})$')
_CTRL = re.compile(r'[\s\x00-\x1f\x7f]')


class UrlError(ValueError):
    """A refused address. `str(e)` says what is wrong; `hint` says what to paste."""

    def __init__(self, message: str, hint: str = ''):
        super().__init__(message)
        self.hint = hint


def _host_of(parts) -> str:
    try:
        host = parts.hostname
    except ValueError as e:                       # a malformed bracketed IPv6
        raise UrlError('That address is not valid.', 'Paste it again from your browser\'s address bar.') from e
    if not host:
        raise UrlError('There is no host name in that address.', 'Paste it like https://example.com/account.')
    return host.lower().rstrip('.')


def check_url(raw, *, own_hosts=()) -> dict:
    """Validate and normalise. Returns `{url, host, path}` or raises UrlError.
    `own_hosts`: host names of the Clayrune server answering this request."""
    if not isinstance(raw, str):
        raise UrlError('The address must be text.')
    text = raw.strip()
    if not text:
        raise UrlError('Paste the service address.', 'Paste it like https://example.com/account.')
    if len(text) > MAX_URL:
        raise UrlError(f'That address is longer than {MAX_URL} characters.', 'Paste the shorter page address of the service itself.')
    if _CTRL.search(text):
        raise UrlError('That address contains spaces or control characters.', 'Paste just the address, with nothing around it.')
    parts = urlsplit(text)
    if parts.scheme.lower() != 'https':
        raise UrlError('Only https:// addresses are accepted.', 'Paste the address that starts with https://.')
    if '@' in parts.netloc:
        raise UrlError('That address has a user name or password in it.', 'Remove everything up to and including the @; credentials go in the next steps.')
    if '?' in text or '#' in text:
        raise UrlError('That address has a ? or # part.', 'Remove everything from the ? or # onward and paste the plain page address.')
    host = _host_of(parts)
    try:
        port = parts.port
    except ValueError as e:
        raise UrlError('That address has an invalid port.', 'Remove the :port part.') from e
    if port not in (None, 443):
        raise UrlError('Only the standard https port is accepted.', 'Remove the :port part.')

    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None:
        if not ip.is_global:
            raise UrlError('That is a private or local network address.', 'Paste the public address of the service, not a local or internal one.')
    else:
        try:
            host = host.encode('idna').decode('ascii')
        except UnicodeError as e:
            raise UrlError('That host name is not valid.', 'Paste the address again from your browser.') from e
        labels = host.split('.')
        if len(host) > 253 or len(labels) < 2 or not all(_LABEL.match(l) for l in labels):
            raise UrlError('That host name is not valid.', 'Paste it like https://example.com/account.')
        if labels[-1] in _RESERVED_TLDS or host == 'localhost' or host.endswith('.localhost'):
            raise UrlError('That is a local or internal name.', 'Paste the public address of the service, not a local or internal one.')
        if not _TLD.match(labels[-1]):
            raise UrlError('That host name does not end in a real domain.', 'Paste it like https://example.com/account.')
        for blocked in list(_registry.blocked_domains()) + [h.lower() for h in own_hosts if h]:
            if host == blocked or host.endswith('.' + blocked):
                raise UrlError('That is a Clayrune address, not another service.', 'Paste the address of the service you want to connect.')
    for own in own_hosts:
        if own and host == str(own).lower():
            raise UrlError('That is a Clayrune address, not another service.', 'Paste the address of the service you want to connect.')
    path = parts.path if parts.path != '/' else ''
    return {'url': f'https://{host}{path}', 'host': host, 'path': path}
