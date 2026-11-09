"""Strict signed Worker delivery schema. No MIME, extraction or executable text."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from mc.agent_mail_signing import HEX, ID, IngressRefused, VerifiedRequest

MAX_REQUEST_BYTES = 128 * 1024
MAX_TEXT_BYTES = 16 * 1024
_FIELDS = frozenset({'version', 'event_id', 'install_id', 'generation', 'thread_id',
                     'incoming_message_id', 'text', 'body_digest', 'auth',
                     'received_at', 'expires_at'})
_RESULT = r'(?:pass|fail|softfail|neutral|none|temperror|permerror|missing|other)'
_MID = re.compile(r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~.@-]+(?:\[[A-Za-z0-9.:-]*\])?")


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise IngressRefused('malformed_envelope')
        out[key] = value
    return out


def parse_envelope(raw: bytes, signed: VerifiedRequest, now_ms: int) -> dict[str, Any]:
    """Only called AFTER verification. Reject extra routing/permission fields."""
    if len(raw) > MAX_REQUEST_BYTES:
        raise IngressRefused('body_limit')
    try:
        env = json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs,
                         parse_constant=lambda _: None)
        if not isinstance(env, dict) or env.keys() != _FIELDS:
            raise IngressRefused('malformed_envelope')
        if (type(env['version']) is not int or env['version'] != 1
                or type(env['generation']) is not int
                or env['generation'] != signed.pin.generation
                or env['install_id'] != signed.pin.install_id):
            raise IngressRefused('envelope_binding')
        if any(not isinstance(env[k], str) or not HEX.fullmatch(env[k])
               for k in ('event_id', 'body_digest')):
            raise IngressRefused('malformed_envelope')
        if not isinstance(env['thread_id'], str) or not ID.fullmatch(env['thread_id']):
            raise IngressRefused('malformed_envelope')
        mid = env['incoming_message_id']
        # Unit 2 keeps null/invalid identifiers HELD at the Worker, not deliverable.
        if not isinstance(mid, str) or len(mid) > 8192 or not _MID.fullmatch(mid):
            raise IngressRefused('invalid_message_id')
        text = env['text']
        if (not isinstance(text, str) or not text.strip() or '\x00' in text
                or len(text.encode('utf-8')) > MAX_TEXT_BYTES):
            raise IngressRefused('invalid_text')
        if hashlib.sha256(text.encode('utf-8')).hexdigest() != env['body_digest']:
            raise IngressRefused('text_digest')
        identity = json.dumps([env['install_id'], env['generation'], env['thread_id'], mid],
                              separators=(',', ':')).encode()
        if hashlib.sha256(identity).hexdigest() != env['event_id']:
            raise IngressRefused('event_identity')
        for key in ('received_at', 'expires_at'):
            if type(env[key]) is not int or not 0 < env[key] <= 2**53 - 1:
                raise IngressRefused('invalid_expiry')
        if (env['received_at'] > now_ms + 300_000
                or env['expires_at'] <= env['received_at'] or env['expires_at'] <= now_ms):
            raise IngressRefused('event_expired')
        auth = env['auth']
        if (not isinstance(auth, dict)
                or auth.keys() != {'ingress', 'spf', 'dkim', 'dmarc', 'aligned'}
                or any(not isinstance(v, str) or len(v) > 8192 for v in auth.values())
                or auth['ingress'] != 'cloudflare'
                or auth['aligned'] not in ('spf', 'dkim', 'spf+dkim')
                or not re.fullmatch(r'pass(?:\+pass)*', auth['dmarc'])
                or any(not re.fullmatch(_RESULT + r'(?:\+' + _RESULT + r')*', auth[k])
                       for k in ('spf', 'dkim'))
                or any('pass' not in auth[k].split('+')
                       for k in auth['aligned'].split('+'))):
            raise IngressRefused('auth_evidence')
        return env
    except (UnicodeError, ValueError, RecursionError) as exc:
        if isinstance(exc, IngressRefused):
            raise
        raise IngressRefused('malformed_envelope') from None
