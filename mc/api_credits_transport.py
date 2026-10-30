"""One Messages API call for the API-credits offload (docs/API_CREDITS_BUILD_SPEC.md).

Direct POST over `requests` (already a dependency; no SDK). The Messages API is
the path Anthropic's subscriber-credits page names explicitly, it returns exact
`usage`, and it carries no tools by construction, which is what a toolless
oneshot already promises.

Contract: `send()` never raises. Every outcome is a `TransportResult`; a failure
carries a typed `error_kind` so the caller can decide between "fall back" and
"fall back AND stop offloading". The API key is scrubbed from every error string
before it leaves this module.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

import requests

API_URL = 'https://api.anthropic.com/v1/messages'
API_VERSION = '2023-06-01'
DEFAULT_MAX_TOKENS = 8192

# error_kind values
CREDIT_LOW = 'credit_low'        # "Your credit balance is too low ..."
AUTH = 'auth'                    # 401 / 403: key rejected
RATE_LIMIT = 'rate_limit'        # 429
SERVER = 'server'                # 5xx / 529 overloaded
TIMEOUT = 'timeout'
NETWORK = 'network'
BAD_REQUEST = 'bad_request'      # other 4xx
BAD_RESPONSE = 'bad_response'    # 200 but unusable body
TRUNCATED = 'truncated'          # stop_reason max_tokens: output is cut short

# Kinds that mean "this key/org cannot be billed right now": the caller flips the
# budget state to stopped instead of retrying on every call.
HALTING_KINDS = frozenset({CREDIT_LOW, AUTH})

_KEY_SHAPE = re.compile(r'sk-ant-[A-Za-z0-9_\-]{6,}')


@dataclass
class TransportResult:
    ok: bool
    text: str = ''
    usage: Dict[str, int] = field(default_factory=dict)
    model: str = ''
    error_kind: str = ''
    error_message: str = ''
    status: int = 0


def scrub(text: Any, api_key: str = '') -> str:
    """Remove the key (exact value and any `sk-ant-...` shape) from a string."""
    s = str(text)
    if api_key:
        s = s.replace(api_key, '[key]')
    return _KEY_SHAPE.sub('[key]', s)


def _usage(raw: Any) -> Dict[str, int]:
    out = {'input_tokens': 0, 'output_tokens': 0,
           'cache_read_input_tokens': 0, 'cache_creation_input_tokens': 0}
    if isinstance(raw, dict):
        for k in out:
            v = raw.get(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0:
                out[k] = int(v)
    return out


def _classify_http(status: int, message: str) -> str:
    low = message.lower()
    if 'credit balance' in low and 'too low' in low:
        return CREDIT_LOW
    if status in (401, 403):
        return AUTH
    if status == 429:
        return RATE_LIMIT
    if status >= 500:
        return SERVER
    return BAD_REQUEST


def send(*, api_key: str, model: str, user_text: str, system_text: str = '',
         max_tokens: int = DEFAULT_MAX_TOKENS, timeout: float = 180,
         post: Optional[Callable[..., Any]] = None) -> TransportResult:
    """POST one system + one user message. `post` is injectable for tests."""
    post = post or requests.post
    body: Dict[str, Any] = {
        'model': model,
        'max_tokens': max(1, int(max_tokens)),
        'messages': [{'role': 'user', 'content': user_text}],
    }
    if system_text:
        body['system'] = system_text
    headers = {'x-api-key': api_key, 'anthropic-version': API_VERSION,
               'content-type': 'application/json'}
    try:
        resp = post(API_URL, headers=headers, json=body, timeout=max(1, float(timeout)))
    except requests.Timeout:
        return TransportResult(False, error_kind=TIMEOUT,
                               error_message=f'timeout after {timeout}s')
    except Exception as e:
        return TransportResult(False, error_kind=NETWORK,
                               error_message=scrub(f'{type(e).__name__}: {e}', api_key)[:300])
    status = int(getattr(resp, 'status_code', 0) or 0)
    try:
        data = resp.json()
    except Exception:
        data = None
    if status != 200:
        msg = ''
        if isinstance(data, dict):
            err = data.get('error')
            msg = str(err.get('message') if isinstance(err, dict) else err or '')
        if not msg:
            msg = str(getattr(resp, 'text', '') or '')[:300]
        msg = scrub(msg, api_key)[:300]
        return TransportResult(False, error_kind=_classify_http(status, msg),
                               error_message=f'HTTP {status}: {msg}', status=status)
    if not isinstance(data, dict) or not isinstance(data.get('content'), list):
        return TransportResult(False, error_kind=BAD_RESPONSE, status=status,
                               error_message='200 response without a content list')
    text = ''.join(b.get('text', '') for b in data['content']
                   if isinstance(b, dict) and b.get('type') == 'text'
                   and isinstance(b.get('text'), str))
    usage = _usage(data.get('usage'))
    if data.get('stop_reason') == 'max_tokens':
        return TransportResult(False, text=text, usage=usage, status=status,
                               model=str(data.get('model') or model),
                               error_kind=TRUNCATED,
                               error_message=f'output hit max_tokens={body["max_tokens"]}')
    return TransportResult(True, text=text, usage=usage, status=status,
                           model=str(data.get('model') or model))
