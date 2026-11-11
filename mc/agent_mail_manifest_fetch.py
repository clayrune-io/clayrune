"""Fetch and verify the root-signed mail-service manifest (MC-1166 unit 7e-2m).

``Enrollment.start``/``change_identity`` need ``(payload, signature)`` signed by the offline
root. Nothing else in ``mc/`` produces them, so this module GETs them from the configured mail
service origin and runs the EXISTING verifier (``verify_service_manifest``) against the pinned
root before returning anything. Unverified bytes never leave this module.

Service contract (the publish side lives in the edge repo and is not built here):

    GET {agent_mail_service_origin}/v1/mail/manifest
    200 application/json, <= 32 KiB:
        {"manifest": [11-item canonical array], "signature": "<base64url Ed25519, 64 bytes>"}

Policy: https only, no redirect of any kind, 5 s timeout, bounded read, the URL comes from
reviewed config (never from a request, an agent or a response). The verification here is
advisory for the UI: it does NOT advance the persistent rollback floor, because ``start`` runs
the same verifier again against the real ``ManifestCAS`` and that is the only authority. The
current floor is only READ (when a store exists) so a rolled-back manifest is refused early.
Remote text never reaches an exception, a log line or a response; codes are a closed set.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Mapping

from mc.agent_mail_service_manifest import ManifestRefused, ManifestState, verify_service_manifest

MANIFEST_PATH = '/v1/mail/manifest'
TIMEOUT_S = 5.0
RESPONSE_LIMIT = 32 * 1024

NOT_CONFIGURED = 'not_configured'
UNREACHABLE = 'unreachable'
BAD_SIGNATURE = 'bad_signature'
REFUSED = 'refused'
STALE = 'stale'
CODES = frozenset({NOT_CONFIGURED, UNREACHABLE, BAD_SIGNATURE, REFUSED, STALE})

_ORIGIN = re.compile(r'https://(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?')
_DETAIL = re.compile(r'[a-z0-9_]{1,40}')
# verifier reasons that mean "valid once, not valid now" rather than "not a genuine manifest"
_STALE_REASONS = frozenset({'manifest_time', 'manifest_rollback'})


def valid_service_origin(origin: Any) -> bool:
    """The fetcher's exact HTTPS hostname policy, also used at configuration write time."""
    return isinstance(origin, str) and _ORIGIN.fullmatch(origin) is not None


class ManifestFetchError(Exception):
    """``code`` is one of CODES; ``detail`` is a closed local token (a verifier reason or a
    transport tag), never remote text."""

    def __init__(self, code: str, detail: str = ''):
        super().__init__(code)
        self.code = code
        self.detail = detail if _DETAIL.fullmatch(detail or '') else ''


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None  # any redirect is a refusal, same-origin or not


class HttpsGetTransport:
    """GET-only. Returns ``{'status', 'body'}`` or raises ManifestFetchError('unreachable')."""

    def __init__(self, *, timeout_s: float = TIMEOUT_S, opener: Any = None):
        self.timeout_s = timeout_s
        self._opener = opener or urllib.request.build_opener(_NoRedirect)

    def get(self, url: str) -> dict[str, Any]:
        request = urllib.request.Request(url, method='GET', headers={'Accept': 'application/json'})
        try:
            with self._opener.open(request, timeout=self.timeout_s) as reply:
                return {'status': reply.status, 'body': reply.read(RESPONSE_LIMIT + 1)}
        except urllib.error.HTTPError as exc:
            return {'status': exc.code, 'body': b''}
        except Exception:  # URLError, timeout, TLS, reset: all one closed code, no text kept
            raise ManifestFetchError(UNREACHABLE, 'transport') from None


class _FloorView:
    """Read-only rollback sink: reports the persisted floor, never writes one."""

    def __init__(self, floor: ManifestState | None):
        self._floor = floor

    def load(self, origin: str) -> ManifestState | None:
        return self._floor

    def compare_and_set(self, origin: str, expected: ManifestState | None, value: ManifestState) -> bool:
        return True


def fetch_verified_manifest(*, origin: Any, root_public_key: Any,
                            floor: Callable[[str], ManifestState | None] | None = None,
                            transport: Any = None, now_ms: Callable[[], int] | None = None) -> dict[str, Any]:
    """Return ``{'manifest', 'signature', 'sequence', 'expires_at_ms'}`` for a manifest the pinned
    root signed and that is current. Raises ManifestFetchError with a closed code otherwise."""
    if not valid_service_origin(origin) \
            or not isinstance(root_public_key, str) or not root_public_key:
        raise ManifestFetchError(NOT_CONFIGURED)
    reply = (transport or HttpsGetTransport()).get(origin + MANIFEST_PATH)
    status, body = reply.get('status'), reply.get('body')
    if 300 <= (status if type(status) is int else 0) < 400:
        raise ManifestFetchError(REFUSED, 'redirect')
    if status != 200:
        raise ManifestFetchError(UNREACHABLE, 'http_' + str(status) if type(status) is int else 'http')
    if not isinstance(body, (bytes, bytearray)) or len(body) > RESPONSE_LIMIT:
        raise ManifestFetchError(REFUSED, 'oversize')
    try:
        value = json.loads(bytes(body).decode('utf-8'))
    except (UnicodeDecodeError, ValueError):
        raise ManifestFetchError(REFUSED, 'not_json') from None
    if not isinstance(value, Mapping) or set(value) != {'manifest', 'signature'} \
            or not isinstance(value['manifest'], list) or not isinstance(value['signature'], str):
        raise ManifestFetchError(REFUSED, 'shape')
    payload, signature = value['manifest'], value['signature']
    try:
        current = floor(origin) if floor is not None else None
    except Exception:  # no store yet / unreadable: start re-verifies against the real floor
        current = None
    clock = now_ms or (lambda: int(time.time() * 1000))
    try:
        candidate = verify_service_manifest(payload, signature, root_public_key=root_public_key,
                                            service_origin=origin, store=_FloorView(current),
                                            now_ms=clock(), enabled=True)
    except ManifestRefused as exc:
        reason = str(exc)
        if reason == 'manifest_signature':
            raise ManifestFetchError(BAD_SIGNATURE, reason) from None
        raise ManifestFetchError(STALE if reason in _STALE_REASONS else REFUSED, reason) from None
    return {'manifest': payload, 'signature': signature, 'sequence': candidate.sequence,
            'expires_at_ms': candidate.expires_at_ms}
