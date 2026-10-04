"""What the Service step was given: a name or an address (Ron 2026-10-03: "add
service only from url or name").

    https://higgsfield.ai/x   an address (anything with ://)
    higgsfield.ai             a bare domain: an address, https:// is assumed
    Higgsfield, linkedin, X   a name: its label or an alias in the registry

A name is looked up in the registry (`registry.lookup_name`) and nowhere else. A
name the registry does not know does NOT cause any web, DNS or MCP-registry
lookup in this slice (that is unknown-service discovery, a later slice): it
raises `UnknownNameError`, which asks the person for the service's web address and
carries the registry's closest names so they can pick one. Pure and offline, like
`url_check`; `resolve` returns the same `{url, host, path}` the address route
did, from the registry's canonical address for a name.
"""
from __future__ import annotations

import re

from mc.desk_connect import registry, url_check
from mc.desk_connect.url_check import UrlError

MAX_NAME = 120
# A bare domain, optionally with a path: letters/digits/hyphens in labels, a letters-only last label.
_BARE_DOMAIN = re.compile(r'^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*\.[a-z]{2,63}(?:/\S*)?$', re.I)


class UnknownNameError(UrlError):
    """A name the registry does not know. `suggestions` are registry briefs."""

    code = 'unknown_name'

    def __init__(self, message: str, hint: str, suggestions: list[dict]):
        super().__init__(message, hint)
        self.suggestions = suggestions


def classify(text: str) -> str:
    """'url' | 'name'. Anything with :// is an address; a single word with a dot that
    has the shape of a domain is an address too (aliases may not contain a dot, so
    "higgsfield.ai" can never be read as the name "Higgsfield AI"); the rest is a name."""
    if '://' in text:
        return 'url'
    if _BARE_DOMAIN.match(text):
        return 'url'
    return 'name'


def resolve(raw, own_hosts=()) -> dict:
    """`{url, host, path, input_kind, service}` or raises UrlError / UnknownNameError.
    `service` is the registry record a NAME matched (None for an address: the
    caller looks the host up as before)."""
    if not isinstance(raw, str):
        raise UrlError('That must be text.', 'Type the name of a service or paste its web address.')
    text = raw.strip()
    if not text:
        raise UrlError('Type the name of a service or paste its web address.',
                       'For example Higgsfield, or https://plausible.io.')
    kind = classify(text)
    if kind == 'url':
        if '://' not in text:
            text = 'https://' + text
        return {**url_check.check_url(text, own_hosts=own_hosts), 'input_kind': 'url', 'service': None}
    if len(text) > MAX_NAME:
        raise UrlError(f'That is longer than {MAX_NAME} characters.', 'Type just the name of the service, or paste its web address.')
    svc = registry.lookup_name(text)
    if svc is None:
        raise UnknownNameError(
            f'Clayrune does not know a service called “{text}” yet.',
            'Paste the service\'s web address instead, for example https://plausible.io.',
            registry.suggest(text))
    return {**url_check.check_url(svc['url'], own_hosts=own_hosts), 'input_kind': 'name', 'service': svc}
