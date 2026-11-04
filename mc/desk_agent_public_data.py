"""Public-data guard for the Desk agent channel (MC-1151 P1).

Spec: `docs/desk_v1/AGENT_CHANNEL_SPEC.md` section 4, last paragraph. The
agent-channel store holds PUBLIC product facts only. This module is the one
place that decides whether a string or URL an owner typed may be persisted:

  * a credential pasted into any field is REFUSED (never redacted in place,
    never echoed back, never logged -- the refusal names the field, not the
    value);
  * a public URL must be http(s), carry no userinfo (`user:pass@`) and no
    secret-bearing query parameter (`?token=`, `?api_key=`, `?sig=` ...).

URLs are DATA here. Nothing in this module (or the store) fetches, resolves,
previews or follows one.

WHY NOT `mc.desk_voice_seed._SECRET_PATTERNS`: that list also refuses every
32+ character hex run and every 40+ character base64 run. A public GitHub URL
pinned to a commit (`/blob/<40-hex sha>/README.md`) or a package integrity
hash would be refused as a "secret", which is wrong for a store whose whole
job is public identifiers. The generic high-entropy rule below therefore
skips pure hex and requires mixed case AND digits. Known limit, stated so it
is not mistaken for complete detection: an unprefixed random credential made
only of hex, or shorter than 40 characters, is not caught.

Pure, stdlib only, no I/O.
"""
from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlsplit

MAX_URL_LEN = 2048

# Prefixed tokens and assignment shapes. Each pattern is a credential SHAPE,
# not a keyword: "API key" in prose is fine, `api_key=abcd1234...` is not.
_CREDENTIAL_PATTERNS = tuple(re.compile(p, re.I) for p in (
    r'\b(?:sk|pk|rk)-[A-Za-z0-9_-]{16,}',             # OpenAI / Stripe style
    r'\bsk-ant-[A-Za-z0-9_-]{16,}',                   # Anthropic
    r'\bgh[pousr]_[A-Za-z0-9]{16,}',                  # GitHub tokens
    r'\bgithub_pat_[A-Za-z0-9_]{20,}',                # GitHub fine-grained
    r'\bglpat-[A-Za-z0-9_-]{16,}',                    # GitLab
    r'\bnpm_[A-Za-z0-9]{30,}',                        # npm
    r'\bpypi-[A-Za-z0-9_-]{30,}',                     # PyPI
    r'\bAKIA[0-9A-Z]{12,}',                           # AWS access key id
    r'\bAIza[0-9A-Za-z_-]{30,}',                      # Google API key
    r'\bxox[baprs]-[A-Za-z0-9-]{10,}',                # Slack
    r'\bey[A-Za-z0-9_-]{10,}\.ey[A-Za-z0-9_-]{10,}\.',  # JWT header.payload.
    r'-----BEGIN [A-Z ]*PRIVATE KEY-----',            # PEM private key
    r'\b(?:bearer|authorization)\b[\s:=]+(?:bearer[\s:=]+)?\S{12,}',
    r'\b(?:pass(?:word|wd)?|secret|api[_ -]?key|access[_ -]?token|'
    r'auth[_ -]?token|client[_ -]?secret|token)\s*[:=]\s*["\']?'
    # Install docs legitimately show placeholders (`API_KEY=<your key>`,
    # `token: ${TOKEN}`, `YOUR_API_KEY`, `xxxx`); those are not credentials.
    r'(?![<{$\[]|your[_-]|x{4,}|\*{4,}|\.{3})[^\s"\']{6,}',
))

# A long mixed-alphabet run: upper AND lower AND digit, 40+ chars, not pure
# hex. Catches most unprefixed generated keys without refusing commit SHAs.
_HIGH_ENTROPY = re.compile(r'[A-Za-z0-9+/_-]{40,}={0,2}')
_PURE_HEX = re.compile(r'^[0-9a-fA-F]+$')

# Query parameter names that carry a credential or a signature. Matched on
# the lower-cased name, exact or as a suffix after `_`/`-` (`x_api_key`).
_SECRET_QUERY_KEYS = frozenset((
    'token', 'access_token', 'refresh_token', 'id_token', 'auth', 'auth_token',
    'authorization', 'key', 'api_key', 'apikey', 'secret', 'client_secret',
    'password', 'passwd', 'pwd', 'sig', 'signature', 'session', 'sessionid',
    'session_id', 'code', 'jwt', 'credential', 'credentials',
    'x-amz-signature', 'x-amz-credential', 'x-amz-security-token',
    'x-goog-signature', 'x-goog-credential',
))


class PublicDataRefused(ValueError):
    """A value may not enter the agent-channel store.

    `field` names where it was found; the message NEVER contains the value,
    so the refusal can be returned, raised and logged without leaking it.
    """

    def __init__(self, field: str, reason: str):
        self.field = field
        self.reason = reason
        super().__init__(f'{field}: {reason}')


def _high_entropy_hit(text: str) -> bool:
    for m in _HIGH_ENTROPY.finditer(text):
        run = m.group(0).rstrip('=')
        if _PURE_HEX.match(run):
            continue
        if (any(c.isupper() for c in run) and any(c.islower() for c in run)
                and any(c.isdigit() for c in run)):
            # A URL path segment is not a key: `/` splits it, so a run that
            # contains `/` is usually a path. Require the run to have no more
            # than one `/` before calling it a credential.
            if run.count('/') <= 1:
                return True
    return False


def carries_credential(text: str) -> bool:
    """True if `text` looks like it contains a credential. Never logs."""
    if not text:
        return False
    if any(p.search(text) for p in _CREDENTIAL_PATTERNS):
        return True
    return _high_entropy_hit(text)


def require_no_credential(field: str, text: str) -> None:
    if carries_credential(text):
        raise PublicDataRefused(field, 'looks like a credential; credentials '
                                       'are never stored in agent listings')


_SECRET_QUERY_SUFFIXES = (
    '_token', '-token', '_secret', '-secret', '_api_key', '-api-key', '_apikey',
    '_signature', '-signature', '_password', '-password', '_credential',
)


def _secret_query_key(name: str) -> bool:
    n = name.strip().lower()
    return n in _SECRET_QUERY_KEYS or n.endswith(_SECRET_QUERY_SUFFIXES)


def validate_public_url(field: str, url: str) -> str:
    """Return the URL unchanged if it is a safe public http(s) URL.

    Never fetches. Refuses: empty, over-long, control characters, non-http(s)
    schemes, missing host, embedded userinfo, secret-bearing query or
    fragment parameters, and any credential shape anywhere in the string.
    """
    if not isinstance(url, str) or not url.strip():
        raise PublicDataRefused(field, 'a public URL is required')
    if url != url.strip():
        raise PublicDataRefused(field, 'URL has surrounding whitespace')
    if len(url) > MAX_URL_LEN:
        raise PublicDataRefused(field, f'URL longer than {MAX_URL_LEN} characters')
    if any(ord(c) < 0x21 or ord(c) == 0x7f for c in url):
        raise PublicDataRefused(field, 'URL contains whitespace or control characters')
    try:
        parts = urlsplit(url)
        host = parts.hostname
        _ = parts.port  # raises ValueError on a malformed port
    except ValueError:
        raise PublicDataRefused(field, 'URL could not be parsed')
    if parts.scheme.lower() not in ('http', 'https'):
        raise PublicDataRefused(field, 'only http and https URLs are accepted')
    if not host:
        raise PublicDataRefused(field, 'URL has no host')
    if parts.username is not None or parts.password is not None or '@' in parts.netloc:
        raise PublicDataRefused(field, 'URL embeds credentials (user@host)')
    for blob in (parts.query, parts.fragment):
        for name, _value in parse_qsl(blob, keep_blank_values=True):
            if _secret_query_key(name):
                raise PublicDataRefused(
                    field, f'URL carries a secret-bearing parameter ({name.lower()!r})')
    require_no_credential(field, url)
    return url
