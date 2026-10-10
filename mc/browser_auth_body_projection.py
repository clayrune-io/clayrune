"""Bounded, allowlisted body projections for browser auth diagnostics."""
from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlsplit

BODY_LIMIT = 65536
SUCCESS_BODY_LIMIT = 8192
# Free-form upstream text is not safely redactable with a token regex alone.
# Keep diagnostic vocabulary; unknown words, identifiers and numbers are masked.
_WORDS = frozenset(('a an and are as at be been body browser by callback cannot '
    'code connection consent cookie cookies could denied domain empty error errors '
    'expired failed failure forbidden from google has have identity in internal '
    'invalid is it jetfuel login missing network not of on or origin permission '
    'please policy rate rejected request required response retry same secure server '
    'sign signed signin status storage success successful temporarily the this '
    'timed timeout to token too try typeerror unable unauthorized unexpected '
    'unknown unsupported user valid validation was web with you your '
    'referenceerror syntaxerror securityerror uncaught fetch blocked limit exceeded '
    'something went wrong again authenticate authentication access credential '
    'credentials authorization csrf mismatch challenge captcha account suspended '
    'locked forbidden malformed bad gateway unavailable service many requests '
    'log right now later').split())
_ERROR_CODES = frozenset({'invalid_request', 'invalid_token', 'invalid_grant',
    'access_denied', 'unauthorized_client', 'unsupported_response_type',
    'temporarily_unavailable', 'server_error', 'AUTHENTICATION_FAILED'})
_SECRET_ASSIGNMENT = re.compile(
    r'(?i)\b(?:[\w-]*(?:token|cookie|authorization|credential|password|secret)|'
    r'code|state|nonce|assertion|email|phone|csrf|[\w-]*id)\b["\']?\s*[=:]\s*'
    r'(?:"[^"]*"|\'[^\']*\'|[^\s,;<>]+)')
_SUBTASK_DIAGNOSTICS = {'DenyLoginSubtask': 'login denied',
                       'LoginSuccessSubtask': 'login successful'}


def safe_text(value: Any) -> str:
    """Bounded diagnostic projection, including plain text/HTML/Jetfuel errors."""
    if not isinstance(value, str):
        return ''
    value = value[:BODY_LIMIT]
    value = re.sub(r'(?is)<(script|style)\b[^>]*>.*?</\1>', ' [redacted] ', value)
    value = re.sub(r'<[^>]*>', ' ', value)
    value = _SECRET_ASSIGNMENT.sub(' [redacted] ', value)
    value = re.sub(r'https?://[^\s<>"\']+', ' [redacted] ', value)
    words = re.findall(r'[^\s]+', value)
    out: list[str] = []
    for word in words[:160]:
        # Tokens containing digits/underscores must be redacted as a whole,
        # not split into potentially recognised fragments.
        word = word.strip('.,:;!?()[]{}"\'')
        out.append(word.lower() if word.lower() in _WORDS else '[redacted]')
    return re.sub(r'(?:\[redacted\]\s*)+', '[redacted] ', ' '.join(out)).strip()[:1024]


def error_body(body: str) -> dict[str, Any]:
    """Extract only error code/message fields; discard all other JSON keys."""
    result: dict[str, Any] = {}
    try:
        parsed = json.loads(body[:BODY_LIMIT])
    except (ValueError, TypeError):
        return {'format': 'text', 'message': safe_text(body)}
    errors: list[dict[str, Any]] = []

    def walk(value: Any, depth: int = 0) -> None:
        if depth > 5 or len(errors) >= 8:
            return
        if isinstance(value, dict):
            entry: dict[str, Any] = {}
            code = value.get('code')
            if type(code) is int and -999999 <= code <= 999999:
                entry['code'] = code
            elif isinstance(code, str):
                entry['code'] = code if code in _ERROR_CODES else safe_text(code)
            for key in ('message', 'detail', 'title'):
                if isinstance(value.get(key), str):
                    entry[key] = safe_text(value[key])
            if entry:
                errors.append(entry)
            for key in ('errors', 'error'):
                child = value.get(key)
                if isinstance(child, str):
                    errors.append({'message': safe_text(child)})
                else:
                    walk(child, depth + 1)
        elif isinstance(value, list):
            for child in value[:8]:
                walk(child, depth + 1)
        elif isinstance(value, str):
            errors.append({'message': safe_text(value)})

    walk(parsed)
    result['format'] = 'json'
    result['errors'] = errors[:8]
    return result


def success_body_url(value: Any) -> bool:
    """Check the raw response URL; projected paths cannot establish eligibility."""
    if not isinstance(value, str):
        return False
    try:
        url = urlsplit(value)
        return (url.scheme == 'https' and url.port in (None, 443)
                and url.username is None and url.password is None
                and ((url.hostname == 'jf.x.com' and url.path.startswith('/onboarding/web/'))
                     or (url.hostname == 'api.x.com' and url.path.startswith('/1.1/flow/'))))
    except ValueError:
        return False


def success_body(body: str) -> dict[str, Any]:
    """Keep error diagnostics inside 2xx envelopes, never arbitrary JSON values.

    Parsing has the existing 64 KiB ceiling; serialized retention is at most
    8 KiB. The strict vocabulary masks unknown words, tokens, numbers, contacts
    and identifiers.
    Only known envelope keys are traversed; account/credential objects disappear.
    """
    raw = body.encode('utf-8', 'replace')
    truncated = len(raw) > BODY_LIMIT
    body = raw[:BODY_LIMIT].decode('utf-8', 'ignore')
    try:
        parsed = json.loads(body)
    except (ValueError, RecursionError):
        # A cut or malformed JSON envelope cannot be safely treated as prose:
        # quoted credential values may consist entirely of vocabulary words.
        if body.lstrip().startswith(('{', '[')):
            return {'format': 'json', 'errors': [], 'truncated': truncated}
        result = {'format': 'text', 'message': safe_text(body), 'truncated': truncated}
        return result
    entries: list[dict[str, Any]] = []

    def walk(value: Any, depth: int = 0) -> None:
        if depth > 5 or len(entries) >= 8:
            return
        if isinstance(value, dict):
            # Reuse the failure projection so the optional capture cannot relax
            # its privacy contract. Envelope values/keys are never copied raw.
            projected = error_body(json.dumps({k: value[k] for k in
                ('code', 'message', 'detail', 'title') if k in value}))
            entries.extend(projected.get('errors', [])[:8 - len(entries)])
            for key in ('error', 'errors', 'data', 'result', 'response', 'subtasks'):
                if key in value:
                    walk(value[key], depth + 1)
            for key in ('reason', 'denial_reason', 'status', 'subtask_id'):
                if isinstance(value.get(key), str) and len(entries) < 8:
                    text = (_SUBTASK_DIAGNOSTICS.get(value[key], safe_text(value[key]))
                            if key == 'subtask_id' else safe_text(value[key]))
                    entries.append({key: text})
        elif isinstance(value, list):
            for child in value[:8]:
                walk(child, depth + 1)
        elif isinstance(value, str):
            entries.append({'message': safe_text(value)})

    walk(parsed)
    result = {'format': 'json', 'errors': entries, 'truncated': truncated}
    while len(json.dumps(result).encode('utf-8')) > SUCCESS_BODY_LIMIT and entries:
        entries.pop()
        result['truncated'] = True
    return result
