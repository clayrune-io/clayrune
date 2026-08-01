"""TOTP codes — the second factor, so an agent can finish a 2FA login.

There is no API to "sync with Google Authenticator", and none is needed: Google
Authenticator is not a service, it is a client for an open standard (TOTP,
RFC 6238). Both it and Clayrune hold the same shared seed and each derives the
same 6-digit code from the current 30-second window, independently and offline.
Nothing is fetched from Google.

Three ways a seed gets in here:

1. **`otpauth://totp/...` URI** — what the enrolment QR code actually encodes.
   Most sites offer a "can't scan it?" link that shows the raw secret.
2. **`otpauth-migration://offline?data=...`** — Google Authenticator's own
   *Transfer accounts → Export* QR. That is the literal "sync with Google
   Authenticator" path: it hands you every seed it holds, in one payload.
   :func:`parse_migration_uri` decodes it (hand-rolled minimal protobuf reader —
   no dependency).
3. A bare base32 seed typed in by hand.

## The security tradeoff, stated plainly

Putting the TOTP seed in the same vault as the password **collapses two factors
into one**. Anything that can read the vault can now produce both. That is not a
flaw in this code — it is inherent to unattended 2FA automation, and it is the
same tradeoff every CI system with a stored OTP seed makes.

It is a real cost, so it is made visible rather than hidden: TOTP secrets are a
distinct `kind`, the UI badges them, and they can be marked attended-only per
secret. For a high-value account (a bank, a domain registrar, the Apple
developer account) the right answer is usually to leave 2FA un-automated and let
the agent ask.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import math
import re
import struct
import time
from urllib.parse import parse_qs, unquote, urlparse

_ALGOS = {'SHA1': hashlib.sha1, 'SHA256': hashlib.sha256, 'SHA512': hashlib.sha512}

# Algorithm / digit enums as they appear in the migration protobuf.
_MIGRATION_ALGOS = {0: 'SHA1', 1: 'SHA1', 2: 'SHA256', 3: 'SHA512', 4: 'MD5'}
_MIGRATION_DIGITS = {0: 6, 1: 6, 2: 8}


class TotpError(Exception):
    """Malformed seed, URI, or migration payload."""


# ── Seed handling ────────────────────────────────────────────────────────────

def normalize_secret(secret: str) -> str:
    """Upper-case, strip spaces and padding — how sites print seeds.

    Authenticator seeds are routinely displayed in space-separated groups
    (`jbsw y3dp ehpk 3pxp`); accepting only the tidy form would make a correct
    paste look like a bad secret.
    """
    s = re.sub(r'[\s-]', '', secret or '').upper().rstrip('=')
    if not s:
        raise TotpError('empty TOTP secret')
    if not re.fullmatch(r'[A-Z2-7]+', s):
        raise TotpError('TOTP secret must be base32 (A–Z and 2–7)')
    return s


def _decode_secret(secret: str) -> bytes:
    s = normalize_secret(secret)
    try:
        return base64.b32decode(s + '=' * (-len(s) % 8))
    except Exception as e:
        raise TotpError(f'could not decode TOTP secret: {e}') from e


def generate(secret: str,
             *,
             digits: int = 6,
             period: int = 30,
             algorithm: str = 'SHA1',
             at: float | None = None) -> str:
    """The current code for ``secret`` (RFC 6238)."""
    algo = _ALGOS.get((algorithm or 'SHA1').upper())
    if algo is None:
        raise TotpError(f'unsupported TOTP algorithm: {algorithm}')
    counter = int((time.time() if at is None else at) // max(1, period))
    mac = hmac.new(_decode_secret(secret), struct.pack('>Q', counter), algo).digest()
    offset = mac[-1] & 0x0F
    code = struct.unpack('>I', mac[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(code % (10 ** digits)).zfill(digits)


def seconds_remaining(period: int = 30, at: float | None = None) -> int:
    """Seconds until the current code expires — so a caller can avoid handing
    out a code that dies before the form is submitted.

    Rounded UP: with 0.3s left, truncation would report ``0``, which reads as
    "already expired" for a code that is in fact still valid. The result is
    therefore always ≥ 1 while a code is live.
    """
    now = time.time() if at is None else at
    return math.ceil(period - (now % period)) or period


# ── otpauth:// URIs ──────────────────────────────────────────────────────────

def parse_otpauth_uri(uri: str) -> dict:
    """Parse ``otpauth://totp/Issuer:account?secret=...`` into vault fields."""
    if not looks_like_otpauth(uri):
        raise TotpError('not an otpauth:// TOTP URI')
    parsed = urlparse(uri.strip())
    if (parsed.netloc or '').lower() != 'totp':
        raise TotpError('only TOTP URIs are supported (not HOTP)')
    q = parse_qs(parsed.query)
    secret = (q.get('secret') or [''])[0]
    if not secret:
        raise TotpError('otpauth URI has no secret parameter')

    label = unquote((parsed.path or '').lstrip('/'))
    issuer = (q.get('issuer') or [''])[0]
    account = label
    if ':' in label:
        label_issuer, account = label.split(':', 1)
        issuer = issuer or label_issuer
    return {
        'secret': normalize_secret(secret),
        'issuer': issuer.strip(),
        'account': account.strip(),
        'digits': int((q.get('digits') or ['6'])[0]),
        'period': int((q.get('period') or ['30'])[0]),
        'algorithm': ((q.get('algorithm') or ['SHA1'])[0]).upper(),
    }


def looks_like_otpauth(text: str) -> bool:
    return str(text or '').strip().lower().startswith('otpauth://')


def looks_like_migration(text: str) -> bool:
    return str(text or '').strip().lower().startswith('otpauth-migration://')


# ── Google Authenticator export payload ──────────────────────────────────────
#
# A minimal protobuf reader. Pulling in `protobuf` for one message with seven
# scalar fields would be a heavier dependency than the parser itself, and this
# payload's schema is fixed and public:
#
#   MigrationPayload { repeated OtpParameters otp_parameters = 1; ... }
#   OtpParameters { bytes secret=1; string name=2; string issuer=3;
#                   Algorithm algorithm=4; DigitCount digits=5;
#                   OtpType type=6; int64 counter=7; }

def _read_varint(buf: bytes, i: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        if i >= len(buf):
            raise TotpError('truncated migration payload')
        byte = buf[i]
        i += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, i
        shift += 7
        if shift > 63:
            raise TotpError('malformed varint in migration payload')


def _iter_fields(buf: bytes):
    """Yield ``(field_number, value)`` — value is int or bytes."""
    i = 0
    while i < len(buf):
        key, i = _read_varint(buf, i)
        field, wire = key >> 3, key & 0x07
        if wire == 0:
            value, i = _read_varint(buf, i)
        elif wire == 2:
            length, i = _read_varint(buf, i)
            if i + length > len(buf):
                raise TotpError('truncated length-delimited field')
            value, i = buf[i:i + length], i + length
        elif wire == 5:
            value, i = buf[i:i + 4], i + 4
        elif wire == 1:
            value, i = buf[i:i + 8], i + 8
        else:
            raise TotpError(f'unsupported protobuf wire type {wire}')
        yield field, value


def parse_migration_uri(uri: str) -> list[dict]:
    """Decode a Google Authenticator *Export accounts* QR payload.

    Returns one dict per account, shaped like :func:`parse_otpauth_uri`. HOTP
    entries are skipped — they are counter-based, and a vault that hands out
    codes without tracking the counter would desynchronise the account.
    """
    if not looks_like_migration(uri):
        raise TotpError('not an otpauth-migration:// URI')
    data = (parse_qs(urlparse(uri.strip()).query).get('data') or [''])[0]
    if not data:
        raise TotpError('migration URI has no data parameter')
    raw = unquote(data)
    try:
        blob = base64.b64decode(raw + '=' * (-len(raw) % 4))
    except Exception as e:
        raise TotpError(f'migration payload is not valid base64: {e}') from e

    out: list[dict] = []
    for field, value in _iter_fields(blob):
        if field != 1 or not isinstance(value, bytes):
            continue  # version / batch metadata
        entry: dict = {'digits': 6, 'period': 30, 'algorithm': 'SHA1',
                       'issuer': '', 'account': ''}
        otp_type = 2
        for sub, sval in _iter_fields(value):
            if sub == 1 and isinstance(sval, bytes):
                entry['secret'] = base64.b32encode(sval).decode('ascii').rstrip('=')
            elif sub == 2 and isinstance(sval, bytes):
                entry['account'] = sval.decode('utf-8', 'replace')
            elif sub == 3 and isinstance(sval, bytes):
                entry['issuer'] = sval.decode('utf-8', 'replace')
            elif sub == 4 and isinstance(sval, int):
                entry['algorithm'] = _MIGRATION_ALGOS.get(sval, 'SHA1')
            elif sub == 5 and isinstance(sval, int):
                entry['digits'] = _MIGRATION_DIGITS.get(sval, 6)
            elif sub == 6 and isinstance(sval, int):
                otp_type = sval
        # An account label often arrives as "Issuer:user@example.com".
        if ':' in entry['account'] and not entry['issuer']:
            entry['issuer'], entry['account'] = entry['account'].split(':', 1)
        if otp_type == 1:
            continue  # HOTP — counter-based, deliberately unsupported
        if entry.get('secret'):
            out.append(entry)
    if not out:
        raise TotpError('no TOTP accounts found in that export')
    return out


def suggested_name(entry: dict) -> str:
    """A vault name for an imported account: ``issuer.account``, sanitised to
    the vault's naming rules."""
    parts = [entry.get('issuer') or '', entry.get('account') or '']
    slug = '.'.join(re.sub(r'[^a-z0-9]+', '-', p.strip().lower()).strip('-')
                    for p in parts if p.strip())
    slug = re.sub(r'\.+', '.', slug).strip('.') or 'totp'
    return (slug[:60] + '.totp') if not slug.endswith('totp') else slug[:64]
