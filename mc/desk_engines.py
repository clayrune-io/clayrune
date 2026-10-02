"""The Desk's generation-engine connector (MC-1019, backlog c7ac1e8c). Contract:
`docs/desk_v1/GENERATION_ENGINES_SCAN.md` "Recommended connector shape",
adopted unamended as plan M26/M27 (journal c7ac1e8c, 2026-10-01).

Backend only. This is the second module in `mc/` that makes an outbound call
that costs the user money (the first is `mc/desk_publish.py`), so the same
rules hold:

  * **Credentials by vault name, never by value.** `higgsfield` (username = key
    id, secret = key secret), `gemini-api`, `openai-api`. They are resolved
    inside an adapter call and live in a local variable for the length of one
    request. No function here returns one, logs one, or writes one. Every
    error string that can carry vendor text goes through
    `secrets_store.redact` first.
  * **Submitting spends money**, so the route that reaches `submit()` refuses an
    unattended caller AND requires the retyped dashboard passcode per call
    (MC-995), exactly like Start/Approve/Renew. This module does not know about
    HTTP callers; it trusts that gate.
  * **Two render caps, both refuse before anything is sent (409).** The
    engine's per-job USD limit (set by the user on Connections, stored in the
    job store beside the spend it guards) and, for a campaign render, what is
    left of the campaign's `how.budget` (a campaign with no budget set has
    nothing left to spend). A render with NO campaign (Studio) is capped by
    the per-job limit alone, so it refuses until the user has set one for that
    engine. (Per-job limit added 2026-10-01, journal c7ac1e8c; it replaces the
    earlier "no per-job limit" rule.)
  * **Idempotent on `desk.idempotency_key`.** A retried click returns the first
    job and makes no second vendor call and no second reservation.
  * **Poll, never webhook** (scan rule 1). Output is downloaded on `ready` into
    the material library, `data/uploads/desk/library/<kind>/Generated/` (scan
    rule 2; Veo deletes after 48 h) and, when the job names a piece
    (`desk.piece_id`), attached to it. `GET job` is what advances a job; nothing here runs on a timer.
  * **Vendor HTTP is one function**, `_http_request`. Tests replace it; no test
    in the repo reaches a vendor.

Two Higgsfield routes (Ron 2026-10-02): `higgsfield_mcp` (default, "Sign in with
Higgsfield": MCP at mcp.higgsfield.ai, the user's plan CREDITS, token kept fresh by
`mc/desk_oauth.py`) and `higgsfield` (Advanced, the developer API key, USD). A credits
engine is capped by its per-job limit IN CREDITS only: the campaign budget is a USD
number and has no exchange rate to credits, so it is not applied to credit jobs.

Credentials are never sent to a host other than the vendor's own: redirects
are not followed automatically (`urllib` would forward a custom header such as
`x-goog-api-key` to wherever a 3xx points) and the presigned Higgsfield upload
URL is called without the Higgsfield key.

Model ids, prices and constraints are DATA (`_ENGINES` below, dated
`prices_read`), not code: Veo is all Preview and Google retires image models
on short notice.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import mimetypes
import re
import shutil
import socket
import subprocess
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

from mc import desk as _desk
from mc import desk_oauth as _oauth
from mc import desk_pieces as _pieces
from mc import secrets_store
from mc.core import _atomic_write_text, _log, now_iso

# -- wired by server.py -------------------------------------------------------
# Same placeholder shape as mc.desk_publish.RECEIPTS_PATH. Outside DATA_DIR
# (the LOAD-BEARING rule in CLAUDE.md); UPLOADS_ROOT is `data/uploads`, the
# directory /api/serve-image already serves from.
JOBS_PATH: Path | None = None
UPLOADS_ROOT: Path | None = None

STORE_VERSION = 1

# Vault names (Dave, 2026-10-01). `google` is a browser login, not an API key.
VAULT_HIGGSFIELD = 'higgsfield'
VAULT_HIGGSFIELD_MCP = 'oauth.higgsfield'     # the sign-in record desk_oauth keeps; never read here directly
VAULT_GEMINI = 'gemini-api'
VAULT_OPENAI = 'openai-api'

JOB_STATUSES = ('queued', 'rendering', 'ready', 'failed')
FAILURE_KINDS = ('moderation', 'auth', 'quota', 'invalid_input', 'engine', 'expired', 'canceled')

_HTTP_TIMEOUT = 30
_SYNC_TIMEOUT = 180          # OpenAI: "complex prompts may take up to 2 minutes"
_MAX_DOWNLOAD_BYTES = 300 * 1024 * 1024
_MAX_ASSET_BYTES = 20 * 1024 * 1024
_MAX_PROMPT_CHARS = 4000
_SYNC_STALE_SECONDS = 600    # a sync job still 'rendering' this long lost its process

_lock = threading.RLock()


class EngineError(Exception):
    """An engine call failed. `kind` is one of FAILURE_KINDS; `definitive` is
    True when the vendor answered with an error (nothing was accepted) and
    False when we cannot tell (timeout, dropped connection): the second case
    keeps the cost reserved, because the vendor may have accepted the job."""

    def __init__(self, kind: str, message: str, *, definitive: bool = True):
        super().__init__(message)
        self.kind = kind
        self.definitive = definitive


class NotConnected(Exception):
    """The vault entry an engine needs is missing or unusable. Carries names
    only, never a value."""

    def __init__(self, engine_id: str, reason: str, vault_entry: str):
        super().__init__(reason)
        self.engine_id = engine_id
        self.reason = reason
        self.vault_entry = vault_entry


class Refused(Exception):
    """The job was refused before anything was sent (the routes answer 400/409)."""

    def __init__(self, code: str, message: str, status: int = 409, **extra):
        super().__init__(message)
        self.code = code
        self.status = status
        self.extra = extra


# -- contract (scan §"Recommended connector shape") ---------------------------

@dataclass
class ModelDescriptor:
    model_id: str
    kind: str                                  # "video" | "image"
    label: str
    status: str = 'stable'                     # "stable" | "preview" | "deprecated"
    shutdown_date: str | None = None
    inputs: dict = field(default_factory=lambda: {
        'text': True, 'first_frame': False, 'last_frame': False,
        'reference_images_max': 0, 'reference_kinds': []})
    durations_sec: list | str = field(default_factory=list)   # [4,6,8] | [5,10] | "any" | [] (image)
    aspect_ratios: list = field(default_factory=list)         # subset of the Desk's 3 the model does natively
    resolutions: list = field(default_factory=list)
    constraints: list = field(default_factory=list)
    audio: bool = False
    price: dict = field(default_factory=dict)  # {unit, usd:{config:n}, read}
    count_max: int = 1
    # Internal: how the adapter talks to this model. Not part of the public view.
    vendor: dict = field(default_factory=dict)

    def public(self) -> dict:
        d = asdict(self)
        d.pop('vendor', None)
        return d


@dataclass
class EngineDescriptor:
    id: str
    label: str
    auth: dict                                 # {kind, vault_entry}
    job_model: str                             # "poll" | "sync"
    output_ttl_hours: int | None
    estimate: str                              # "endpoint" | "price_table"
    prices_read: str
    models: list[ModelDescriptor]
    # What the Secrets form says for this engine's vault entry. Labels and a
    # hint only, never a value; `username_label` None = the engine has no
    # username half and the form hides that field.
    credential: dict = field(default_factory=dict)
    # "usd" for a priced-in-dollars engine; "credits" for a plan-credit engine (the
    # per-job limit and the estimate are then in credits, see check_caps).
    currency: str = 'usd'
    # `group` lets Connections show two routes to one service as one card; `advanced`
    # marks the route a non-expert should not start with.
    group: str | None = None
    advanced: bool = False

    def public(self) -> dict:
        d = asdict(self)
        d['models'] = [m.public() for m in self.models]
        return d


@dataclass
class GenerationRequest:
    engine_id: str
    model_id: str
    kind: str
    prompt: str
    aspect_ratio: str
    negative_prompt: str | None = None
    duration_sec: int | None = None
    resolution: str | None = None
    audio: bool | None = None
    count: int = 1
    seed: int | None = None
    first_frame: dict | None = None            # AssetRef
    last_frame: dict | None = None
    reference_images: list = field(default_factory=list)   # [AssetRef]
    desk: dict = field(default_factory=dict)   # {piece_id, scene_id, revision, idempotency_key}


_DESK_RATIOS = ('1:1', '9:16', '16:9')

_VEO_8S_ONLY = [
    {'when': {'resolution': ['1080p', '4k']}, 'require': {'duration_sec': 8}},
    {'when': {'has_references': True}, 'require': {'duration_sec': 8}},
]


def _veo_model(model_id: str, label: str, usd: dict, *, references: int) -> ModelDescriptor:
    return ModelDescriptor(
        model_id=model_id, kind='video', label=label, status='preview',
        inputs={'text': True, 'first_frame': True, 'last_frame': False,
                'reference_images_max': references,
                'reference_kinds': ['asset'] if references else []},
        durations_sec=[4, 6, 8], aspect_ratios=['9:16', '16:9'],
        resolutions=list(usd), constraints=list(_VEO_8S_ONLY) if references else [],
        audio=True, price={'unit': 'second', 'usd': usd, 'read': '2026-09-30'},
        vendor={'adapter': 'veo'})


def _gemini_image_model(model_id: str, label: str, usd: dict, *, refs: int) -> ModelDescriptor:
    return ModelDescriptor(
        model_id=model_id, kind='image', label=label,
        inputs={'text': True, 'first_frame': False, 'last_frame': False,
                'reference_images_max': refs, 'reference_kinds': ['object'] if refs else []},
        aspect_ratios=list(_DESK_RATIOS), resolutions=list(usd),
        price={'unit': 'image', 'usd': usd, 'read': '2026-09-30'},
        vendor={'adapter': 'gemini_image'})


def _openai_image_model(model_id: str, label: str) -> ModelDescriptor:
    return ModelDescriptor(
        model_id=model_id, kind='image', label=label,
        inputs={'text': True, 'first_frame': False, 'last_frame': False,
                'reference_images_max': 4, 'reference_kinds': ['object']},
        aspect_ratios=list(_DESK_RATIOS),
        # Per-image price is Dark (scan): the figure is tokens x $30/M, labelled
        # approximate in every estimate, and the billed `usage` replaces it.
        price={'unit': 'token', 'usd': {'image_output_per_1m_tokens': 30.0}, 'read': '2026-09-30'},
        vendor={'adapter': 'openai_image', 'quality': 'medium'})


def _higgs_model(path: str, kind: str, label: str, *, durations=None, ratios=None,
                 resolutions=None, first_frame=False, count_max=1, usd=None,
                 unit='second') -> ModelDescriptor:
    return ModelDescriptor(
        model_id=path, kind=kind, label=label,
        inputs={'text': True, 'first_frame': first_frame, 'last_frame': False,
                'reference_images_max': 0, 'reference_kinds': []},
        durations_sec=list(durations or []), aspect_ratios=list(ratios or []),
        resolutions=list(resolutions or []), count_max=count_max,
        price={'unit': unit, 'usd': usd or {}, 'read': '2026-09-30'},
        vendor={'adapter': 'higgsfield'})


def _higgs_mcp_model(model_id: str, kind: str, label: str, *, count_max: int = 1) -> ModelDescriptor:
    # Prices are NOT data here: the engine quotes credits per call (get_cost), so
    # `price` only says where the number comes from. Ratios/durations are left open
    # on purpose: the server accepts or adjusts them and reports it in `adjustments`.
    return ModelDescriptor(
        model_id=model_id, kind=kind, label=label,
        inputs={'text': True, 'first_frame': False, 'last_frame': False,
                'reference_images_max': 0, 'reference_kinds': []},
        durations_sec='any' if kind == 'video' else [], count_max=count_max,
        price={'unit': 'credit', 'usd': {}, 'read': '2026-10-02'},
        vendor={'adapter': 'higgsfield_mcp'})


def _credential(vault_entry: str, secret_label: str, hint: str, url: str, *,
                username_label: str | None = None, username_required: bool = False) -> dict:
    # `entry_type` locks the Secrets form's type: a two-part credential is an
    # API key pair (the key ID rides the username slot), anything else a single
    # API key. The labels stay the engine's own wording.
    return {'vault_entry': vault_entry, 'username_label': username_label,
            'entry_type': 'api_key_pair' if username_label else 'api_key',
            'username_required': bool(username_label and username_required),
            'secret_label': secret_label, 'hint': hint, 'url': url}


# `kind` of the engine's auth + where its credential lives. Two-part entries
# (Higgsfield) read the username half through `secrets_store.get_username`.
_ENGINES: list[EngineDescriptor] = [
    EngineDescriptor(
        id='higgsfield_mcp', label='Higgsfield (sign in)',
        auth={'kind': 'oauth', 'vault_entry': VAULT_HIGGSFIELD_MCP, 'service': 'higgsfield'},
        job_model='poll', output_ttl_hours=4, estimate='endpoint', prices_read='2026-10-02',
        models=[
            _higgs_mcp_model('gpt_image_2_5', 'image', 'GPT Image 2.5 (image)', count_max=4),
            _higgs_mcp_model('soul_2', 'image', 'Soul 2 (portraits, fashion)', count_max=4),
            _higgs_mcp_model('nano_banana', 'image', 'Nano Banana (image)', count_max=4),
            _higgs_mcp_model('z_image', 'image', 'Z Image (fast, low cost)', count_max=4),
            _higgs_mcp_model('seedance_2_5', 'video', 'Seedance 2.5 (video)'),
            _higgs_mcp_model('kling3_0', 'video', 'Kling 3.0 (video)'),
        ],
        credential={'vault_entry': VAULT_HIGGSFIELD_MCP, 'kind': 'signin', 'url': 'https://higgsfield.ai'},
        currency='credits', group='higgsfield'),
    EngineDescriptor(
        id='higgsfield', label='Higgsfield (API key)',
        auth={'kind': 'key_id_secret', 'vault_entry': VAULT_HIGGSFIELD},
        job_model='poll', output_ttl_hours=168, estimate='endpoint', prices_read='2026-09-30',
        models=[
            _higgs_model('higgsfield-ai/soul/standard', 'image', 'Soul 2 (image)',
                         ratios=['1:1', '9:16', '16:9'], resolutions=['2K', '4K'],
                         count_max=4, usd={'default': 0.0032}, unit='image'),
            _higgs_model('kling-video/v2.5-turbo/pro/text-to-video', 'video',
                         'Kling 2.5 Turbo Pro (text to video)', durations=[5, 10],
                         usd={'default': 0.042}),
            _higgs_model('kling-video/v2.5-turbo/pro/image-to-video', 'video',
                         'Kling 2.5 Turbo Pro (image to video)', durations=[5, 10],
                         first_frame=True, usd={'default': 0.042}),
            _higgs_model('minimax/hailuo-2.3/standard/text-to-video', 'video',
                         'Hailuo 2.3 (text to video)', durations=[6, 10]),
            _higgs_model('minimax/hailuo-2.3/standard/image-to-video', 'video',
                         'Hailuo 2.3 (image to video)', durations=[6, 10], first_frame=True),
        ],
        credential=_credential(
            VAULT_HIGGSFIELD, 'API key secret',
            'Create an API key in the Higgsfield console; it shows a key ID and a key secret.',
            'https://console.higgsfield.ai', username_label='API key ID', username_required=True),
        group='higgsfield', advanced=True),
    EngineDescriptor(
        id='google', label='Google (Veo + Gemini image)',
        auth={'kind': 'api_key', 'vault_entry': VAULT_GEMINI},
        job_model='poll', output_ttl_hours=48, estimate='price_table', prices_read='2026-09-30',
        models=[
            _veo_model('veo-3.1-generate-preview', 'Veo 3.1',
                       {'720p': 0.40, '1080p': 0.40, '4k': 0.60}, references=3),
            _veo_model('veo-3.1-fast-generate-preview', 'Veo 3.1 Fast',
                       {'720p': 0.10, '1080p': 0.12, '4k': 0.30}, references=3),
            _veo_model('veo-3.1-lite-generate-preview', 'Veo 3.1 Lite',
                       {'720p': 0.05, '1080p': 0.08}, references=0),
            _gemini_image_model('gemini-3.1-flash-image', 'Gemini 3.1 Flash Image',
                                {'1K': 0.067, '2K': 0.101, '4K': 0.151}, refs=10),
            _gemini_image_model('gemini-3.1-flash-lite-image', 'Gemini 3.1 Flash Lite Image',
                                {'1K': 0.0336}, refs=14),
            _gemini_image_model('gemini-3-pro-image', 'Gemini 3 Pro Image',
                                {'1K': 0.134, '2K': 0.134, '4K': 0.24}, refs=6),
        ],
        credential=_credential(
            VAULT_GEMINI, 'Gemini API key',
            'Create the key in Google AI Studio; billing must be on for the project or Veo is refused.',
            'https://aistudio.google.com/apikey')),
    EngineDescriptor(
        id='openai', label='OpenAI image',
        auth={'kind': 'api_key', 'vault_entry': VAULT_OPENAI},
        job_model='sync', output_ttl_hours=0, estimate='price_table', prices_read='2026-09-30',
        models=[
            _openai_image_model('gpt-image-2.5-sunburst', 'GPT Image 2.5 (sunburst)'),
            _openai_image_model('gpt-image-2.5-flare', 'GPT Image 2.5 (flare)'),
        ],
        credential=_credential(
            VAULT_OPENAI, 'OpenAI API key',
            'Create the key on platform.openai.com; a ChatGPT or Codex plan does not include API use.',
            'https://platform.openai.com/api-keys')),
]

ENGINES: dict[str, EngineDescriptor] = {e.id: e for e in _ENGINES}


def get_engine(engine_id: str) -> EngineDescriptor | None:
    return ENGINES.get(engine_id)


def get_model(engine_id: str, model_id: str) -> ModelDescriptor | None:
    eng = ENGINES.get(engine_id)
    return next((m for m in eng.models if m.model_id == model_id), None) if eng else None


# -- HTTP (the one function tests replace) ------------------------------------

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _http_request(method: str, url: str, *, headers: dict | None = None,
                  body: bytes | None = None, timeout: float = _HTTP_TIMEOUT,
                  max_bytes: int = 8 * 1024 * 1024) -> tuple[int, dict, bytes]:
    """One vendor HTTP exchange -> (status, lower-cased headers, body).

    Never follows a redirect (a 3xx comes back as-is for the caller to decide)
    and never raises for an HTTP error status: the caller classifies it.
    Raises only for a transport failure (`URLError`, `TimeoutError`, `OSError`).
    A body past `max_bytes` raises `ValueError` rather than filling memory."""
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        resp = _OPENER.open(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        resp = e
    try:
        data = resp.read(max_bytes + 1)
        status = int(getattr(resp, 'status', None) or resp.getcode() or 0)
        hdrs = {k.lower(): v for k, v in resp.headers.items()}
    finally:
        resp.close()
    if len(data) > max_bytes:
        raise ValueError(f'response from {urllib.parse.urlsplit(url).netloc} exceeds {max_bytes} bytes')
    return status, hdrs, data


def _safe(text: Any, limit: int = 300) -> str:
    """Vendor text made safe to store or return: redacted, single-line, bounded."""
    s = secrets_store.redact(str(text or ''))
    return re.sub(r'\s+', ' ', s).strip()[:limit]


def _transport_call(*args, **kw) -> tuple[int, dict, bytes]:
    """`_http_request` with transport failures turned into a NON-definitive
    EngineError (we cannot know whether the vendor acted on the request)."""
    try:
        return _http_request(*args, **kw)
    except (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionError, OSError, ValueError) as e:
        raise EngineError('engine', f'no usable response from the engine: {_safe(e)}',
                          definitive=False) from e


def _classify_http(status: int, body: bytes) -> EngineError:
    text = _safe(body.decode('utf-8', errors='replace'))
    low = text.lower()
    if status in (401, 403):
        return EngineError('auth', f'the engine rejected the credentials (HTTP {status}): {text}')
    if status == 429 or 'concurren' in low or 'rate limit' in low or 'quota' in low \
            or 'insufficient' in low or 'balance' in low or status == 402:
        return EngineError('quota', f'engine quota, balance or rate limit (HTTP {status}): {text}')
    if status in (400, 404, 413, 415, 422):
        if 'safety' in low or 'blocked' in low or 'moderation' in low or 'nsfw' in low:
            return EngineError('moderation', f'the engine refused the content (HTTP {status}): {text}')
        return EngineError('invalid_input', f'the engine rejected the request (HTTP {status}): {text}')
    return EngineError('engine', f'engine error (HTTP {status}): {text}')


def _json_call(method: str, url: str, headers: dict, payload: Any = None, *,
               timeout: float = _HTTP_TIMEOUT, max_bytes: int = 32 * 1024 * 1024) -> dict:
    body = None
    h = dict(headers)
    if payload is not None:
        body = json.dumps(payload).encode('utf-8')
        h['Content-Type'] = 'application/json'
    status, _hdrs, data = _transport_call(method, url, headers=h, body=body,
                                          timeout=timeout, max_bytes=max_bytes)
    if not 200 <= status < 300:
        raise _classify_http(status, data)
    if not data:
        return {}
    try:
        out = json.loads(data.decode('utf-8'))
    except ValueError as e:
        raise EngineError('engine', 'the engine answered with something that is not JSON',
                          definitive=False) from e
    if not isinstance(out, dict):
        raise EngineError('engine', 'the engine answered with an unexpected JSON shape',
                          definitive=False)
    return out


def _download(url: str, *, auth_headers: dict | None = None,
              auth_host_suffix: str | None = None) -> tuple[bytes, str]:
    """Fetch an output file -> (bytes, mime). https only; follows at most 3
    redirects by hand, and attaches `auth_headers` only to a host ending in
    `auth_host_suffix` so a redirect can never carry a key elsewhere."""
    for _ in range(4):
        parts = urllib.parse.urlsplit(url)
        if parts.scheme != 'https' or not parts.hostname:
            raise EngineError('engine', 'the engine returned an output URL that is not https')
        host = parts.hostname.lower()
        hdrs = dict(auth_headers or {}) if (
            auth_host_suffix and (host == auth_host_suffix or host.endswith('.' + auth_host_suffix))
        ) else {}
        status, rh, data = _transport_call('GET', url, headers=hdrs, timeout=120,
                                           max_bytes=_MAX_DOWNLOAD_BYTES)
        if status in (301, 302, 303, 307, 308) and rh.get('location'):
            url = urllib.parse.urljoin(url, rh['location'])
            continue
        if not 200 <= status < 300:
            raise _classify_http(status, data)
        return data, (rh.get('content-type') or '').split(';')[0].strip()
    raise EngineError('engine', 'too many redirects fetching the output')


# -- credentials --------------------------------------------------------------

@dataclass
class _Creds:
    """Held in a local for one adapter call. `repr` is blanked so a stray
    log of the object cannot leak either half."""
    secret: str
    user: str = ''

    def __repr__(self) -> str:
        return '_Creds(<redacted>)'


def connection(engine_id: str, project_id: str | None = None) -> dict:
    """`{ready, reason, vault_entry, exists}` for one engine. Metadata only: the
    vault is asked whether the entry exists and decrypts, never for its value.
    `exists` tells the Connect button whether to open the form as Add or Edit."""
    eng = ENGINES[engine_id]
    name = eng.auth['vault_entry']
    if eng.auth['kind'] == 'oauth':
        st = _oauth.status(eng.auth['service'])
        return {'ready': st['state'] == 'connected', 'vault_entry': name,
                'exists': st['state'] != 'not_connected', 'state': st['state'],
                'reason': None if st['state'] == 'connected' else (
                    st['reason'] or f'{eng.label} is not signed in yet')}
    exists = False
    try:
        visible = {s['name']: s for s in secrets_store.list_secrets(project_id)}
        rec = visible.get(name)
        if rec is None:
            return {'ready': False, 'vault_entry': name, 'exists': False,
                    'reason': f"no vault entry named '{name}' (add it in Secrets)"}
        exists = True
        if not secrets_store.is_readable(name):
            return {'ready': False, 'vault_entry': name, 'exists': True,
                    'reason': f"vault entry '{name}' cannot be read (vault locked or key mismatch)"}
        if eng.auth['kind'] == 'key_id_secret' and not rec.get('username'):
            label = eng.credential.get('username_label') or 'username'
            return {'ready': False, 'vault_entry': name, 'exists': True,
                    'reason': f"vault entry '{name}' has no {label}; store the {label} in its username field"}
    except secrets_store.SecretsError as e:
        return {'ready': False, 'vault_entry': name, 'exists': exists, 'reason': _safe(e)}
    return {'ready': True, 'vault_entry': name, 'exists': True, 'reason': None}


def _creds(engine_id: str, project_id: str | None, unattended: bool) -> _Creds:
    eng = ENGINES[engine_id]
    name = eng.auth['vault_entry']
    status = connection(engine_id, project_id)
    if not status['ready']:
        raise NotConnected(engine_id, status['reason'], name)
    if eng.auth['kind'] == 'oauth':
        try:
            token = _oauth.access_token(eng.auth['service'], consumer='desk_engines',
                                        project_id=project_id, unattended=unattended)
        except _oauth.OAuthError as e:
            raise NotConnected(engine_id, str(e), name) from e
        return _Creds(secret=token)
    try:
        secret = secrets_store.get_secret_value(
            name, consumer='desk_engines', project_id=project_id, unattended=unattended)
        user = (secrets_store.get_username(name, project_id=project_id)
                if eng.auth['kind'] == 'key_id_secret' else '')
    except secrets_store.SecretsError as e:
        raise NotConnected(engine_id, _safe(e), name) from e
    return _Creds(secret=secret, user=user)


# -- assets -------------------------------------------------------------------

_ASSET_MIMES = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
                '.webp': 'image/webp', '.gif': 'image/gif'}


def _read_asset(ref: Any) -> tuple[bytes, str]:
    """An AssetRef is `{path}` and the file must live under `data/uploads/`
    (the library the Desk and /api/serve-image share). Anything else is
    refused: this is the guard that stops a render request from uploading an
    arbitrary file off the disk to a third party."""
    if not isinstance(ref, dict) or not isinstance(ref.get('path'), str) or not ref['path']:
        raise Refused('invalid_input', 'an asset must be {"path": "<file under data/uploads>"}', 400)
    if UPLOADS_ROOT is None:
        raise Refused('invalid_input', 'the uploads directory is not wired; cannot read assets', 400)
    root = Path(UPLOADS_ROOT).resolve()
    try:
        p = Path(ref['path']).resolve()
        p.relative_to(root)
    except (ValueError, OSError):
        raise Refused('invalid_input', 'an asset must live under data/uploads', 400)
    mime = _ASSET_MIMES.get(p.suffix.lower())
    if mime is None:
        raise Refused('invalid_input', f'asset type {p.suffix or "(none)"} is not png, jpeg, webp or gif', 400)
    try:
        if not p.is_file() or p.stat().st_size > _MAX_ASSET_BYTES:
            raise Refused('invalid_input', 'asset is missing or larger than 20 MB', 400)
        return p.read_bytes(), mime
    except OSError as e:
        raise Refused('invalid_input', f'asset could not be read: {_safe(e)}', 400)


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode('ascii')


# -- validation + estimate ----------------------------------------------------

def _constraint_hits(c: dict, req: GenerationRequest) -> bool:
    w = c.get('when') or {}
    if 'resolution' in w and req.resolution not in w['resolution']:
        return False
    if 'has_references' in w and bool(req.reference_images) != bool(w['has_references']):
        return False
    return True


def validate_request(model: ModelDescriptor, req: GenerationRequest) -> list[str]:
    """Why this model cannot do this request (scan rule 5). Empty = fine."""
    p: list[str] = []
    if model.kind != req.kind:
        p.append(f'{model.model_id} makes {model.kind}, not {req.kind}')
    if model.status == 'deprecated' or (
            model.shutdown_date and model.shutdown_date <= date.today().isoformat()):
        p.append(f'{model.model_id} is retired or past its shutdown date')
    if not (req.prompt or '').strip():
        p.append('prompt is empty')
    elif len(req.prompt) > _MAX_PROMPT_CHARS:
        p.append(f'prompt is over {_MAX_PROMPT_CHARS} characters')
    if model.aspect_ratios and req.aspect_ratio not in model.aspect_ratios:
        p.append(f'{model.model_id} cannot do {req.aspect_ratio} (it does {", ".join(model.aspect_ratios)})')
    if not isinstance(req.count, int) or not 1 <= req.count <= model.count_max:
        p.append(f'count must be 1 to {model.count_max}')
    if model.kind == 'video':
        durs = model.durations_sec
        if isinstance(durs, list) and req.duration_sec not in durs:
            p.append(f'{model.model_id} does {durs} seconds, not {req.duration_sec}')
    elif req.duration_sec is not None:
        p.append('duration_sec only applies to video')
    if model.resolutions and req.resolution not in model.resolutions:
        p.append(f'resolution must be one of {model.resolutions}')
    if req.audio and not model.audio:
        p.append(f'{model.model_id} cannot generate audio')
    inp = model.inputs
    if req.first_frame and not inp.get('first_frame'):
        p.append(f'{model.model_id} takes no first frame')
    if req.last_frame:
        p.append(f'{model.model_id} takes no last frame')
    if len(req.reference_images) > inp.get('reference_images_max', 0):
        p.append(f'{model.model_id} takes at most {inp.get("reference_images_max", 0)} reference images')
    if model.vendor.get('adapter') == 'higgsfield' and model.kind == 'video' \
            and 'image-to-video' in model.model_id and not req.first_frame:
        p.append(f'{model.model_id} needs a first frame')
    for c in model.constraints:
        if _constraint_hits(c, req):
            for k, v in (c.get('require') or {}).items():
                if getattr(req, k, None) != v:
                    p.append(f'with these settings {model.model_id} needs {k}={v}')
    return p


@dataclass
class Estimate:
    usd: float
    basis: str                  # "engine" | "table"
    read: str
    approximate: bool = False
    note: str | None = None
    credits: float | None = None        # a credits engine: the quote; `usd` is then 0.0
    adjustments: dict | None = None     # what the engine changed in the request (shown, never hidden)

    def public(self) -> dict:
        d = asdict(self)
        for k in ('credits', 'adjustments'):     # keep the USD engines' shape exactly as it was
            if d.get(k) is None:
                d.pop(k, None)
        return d


# PROXY, not a measurement: OpenAI publishes no per-image price for
# gpt-image-2.5 (scan "Still dark"). Output tokens for a medium-quality image
# are taken as 1056 per 1024x1024 of area (the gpt-image-1 figure, scaled by
# pixels) at the published $30 per 1M image-output tokens. Every estimate is
# flagged `approximate`, and a successful job replaces it with the billed
# `usage`. Replace this with a measured table when one exists.
_OPENAI_MEDIUM_TOKENS_PER_MP = 1056 / (1024 * 1024)
_OPENAI_USD_PER_TOKEN = 30.0 / 1_000_000
_OPENAI_SIZES = {'1:1': (1024, 1024), '16:9': (1536, 864), '9:16': (864, 1536)}


def _price_table_usd(model: ModelDescriptor, req: GenerationRequest) -> tuple[float, bool, str | None]:
    adapter = model.vendor.get('adapter')
    if adapter == 'veo':
        rate = model.price['usd'].get(req.resolution or '')
        if rate is None:
            raise Refused('estimate_unavailable', f'no price for {model.model_id} at {req.resolution}')
        return rate * (req.duration_sec or 0) * req.count, False, None
    if adapter == 'gemini_image':
        each = model.price['usd'].get(req.resolution or '')
        if each is None:
            raise Refused('estimate_unavailable', f'no price for {model.model_id} at {req.resolution}')
        return each * req.count, False, 'input tokens are billed on top (about $0.50/M)'
    if adapter == 'openai_image':
        w, h = _OPENAI_SIZES[req.aspect_ratio]
        tokens = w * h * _OPENAI_MEDIUM_TOKENS_PER_MP
        return tokens * _OPENAI_USD_PER_TOKEN * req.count, True, (
            'approximate: OpenAI publishes no per-image price for gpt-image-2.5; '
            'the billed usage replaces this figure')
    raise Refused('estimate_unavailable', f'{model.model_id} has no price table')


# -- adapters -----------------------------------------------------------------
# Each adapter is a small object with the same four methods:
#   estimate(model, req, creds) -> Estimate
#   submit(model, req, creds)   -> {'ref': {...}} for poll engines, or
#                                   {'outputs': [(bytes, mime)], 'cost_usd': float|None} for sync ones
#   poll(model, ref, creds)     -> {'state': 'queued'|'rendering'|'ready'|'failed', ...}
#   fetch(model, item, creds)   -> [(bytes, mime)]   (poll engines, on ready)

_HIGGS_BASE = 'https://api.higgsfield.ai'
_GEMINI_BASE = 'https://generativelanguage.googleapis.com'
_OPENAI_BASE = 'https://api.openai.com'
_REF_OK = re.compile(r'^[A-Za-z0-9_.\-/]{1,200}$')


class HiggsfieldAdapter:
    def _headers(self, c: _Creds) -> dict:
        return {'Authorization': f'Key {c.user}:{c.secret}', 'Accept': 'application/json'}

    def _body(self, model: ModelDescriptor, req: GenerationRequest, image_url: str | None) -> dict:
        mid = model.model_id
        b: dict = {'prompt': req.prompt}
        if mid.startswith('higgsfield-ai/soul'):
            b['num_images'] = req.count
            b['resolution'] = req.resolution or '2K'
            b['aspect_ratio'] = req.aspect_ratio
            return b
        b['duration'] = req.duration_sec
        if image_url:
            b['image_url'] = image_url
        if mid.startswith('kling-video') and req.negative_prompt:
            b['negative_prompt'] = req.negative_prompt
        return b

    def _upload_first_frame(self, ref: dict, c: _Creds) -> str:
        data, mime = _read_asset(ref)
        up = _json_call('POST', f'{_HIGGS_BASE}/files/generate-upload-url', self._headers(c),
                        {'content_type': mime})
        url, public = up.get('upload_url'), up.get('public_url')
        if not (isinstance(url, str) and isinstance(public, str)):
            raise EngineError('engine', 'the engine gave no upload URL for the first frame')
        h = {str(k): str(v) for k, v in (up.get('upload_headers') or {}).items()}
        # The presigned storage URL never sees the Higgsfield key.
        status, _rh, body = _transport_call('PUT', url, headers=h, body=data, timeout=120)
        if not 200 <= status < 300:
            raise _classify_http(status, body)
        return public

    def estimate(self, model, req, creds) -> Estimate:
        out = _json_call('POST', f'{_HIGGS_BASE}/estimate/{model.model_id}', self._headers(creds),
                         self._body(model, req, image_url=None))
        try:
            usd = float(out['usd'])
        except (KeyError, TypeError, ValueError):
            raise EngineError('engine', 'the estimate endpoint returned no usable usd figure')
        return Estimate(usd=usd, basis='engine', read=now_iso())

    def submit(self, model, req, creds) -> dict:
        image_url = self._upload_first_frame(req.first_frame, creds) if req.first_frame else None
        out = _json_call('POST', f'{_HIGGS_BASE}/{model.model_id}', self._headers(creds),
                         self._body(model, req, image_url))
        rid = out.get('request_id')
        if not isinstance(rid, str) or not _REF_OK.match(rid):
            raise EngineError('engine', 'the engine accepted the job but returned no request id',
                              definitive=False)
        return {'ref': {'request_id': rid}}

    def poll(self, model, ref, creds) -> dict:
        rid = ref.get('request_id', '')
        if not _REF_OK.match(rid):
            raise EngineError('engine', 'stored request id is malformed')
        out = _json_call('GET', f'{_HIGGS_BASE}/requests/{rid}/status', self._headers(creds))
        st = out.get('status')
        if st == 'queued':
            return {'state': 'queued'}
        if st == 'in_progress':
            return {'state': 'rendering'}
        if st == 'completed':
            urls = []
            if isinstance(out.get('video'), dict) and out['video'].get('url'):
                urls.append(out['video']['url'])
            for im in out.get('images') or []:
                if isinstance(im, dict) and im.get('url'):
                    urls.append(im['url'])
            if not urls:
                return {'state': 'failed', 'kind': 'engine', 'message': 'completed with no output URL',
                        'free': False}
            return {'state': 'ready', 'urls': urls}
        if st == 'nsfw':
            return {'state': 'failed', 'kind': 'moderation',
                    'message': 'the engine moderation filter rejected the content', 'free': True}
        if st == 'canceled':
            return {'state': 'failed', 'kind': 'canceled', 'message': 'the job was canceled', 'free': True}
        if st == 'failed':
            return {'state': 'failed', 'kind': 'engine',
                    'message': _safe(out.get('error') or out.get('detail') or 'the engine reported a failure'),
                    'free': True}
        return {'state': 'rendering'}

    def fetch(self, model, item, creds) -> list:
        return [_download(u) for u in item['urls']]


_HIGGS_MCP_URL = 'https://mcp.higgsfield.ai/mcp'
_MCP_PROTOCOL = '2025-11-25'
_MCP_TIMEOUT = 60               # job_status sync:true holds up to ~25 s server-side
_MCP_MAX_BYTES = 4 * 1024 * 1024


def _mcp_post(token: str, body: dict, *, expect_id: int | None, timeout: float = _MCP_TIMEOUT) -> dict | None:
    """One JSON-RPC POST to the Higgsfield MCP endpoint -> the `result` of the
    response with id `expect_id` (None for a notification). The server answers
    in an SSE stream (or plain JSON); the stream is read only until our id
    arrives, so a server that keeps it open cannot hang us. The one function
    the MCP tests replace. Transport failures raise a NON-definitive EngineError:
    a call that timed out may still have been acted on."""
    headers = {'Authorization': f'Bearer {token}', 'Accept': 'application/json, text/event-stream',
               'Content-Type': 'application/json', 'MCP-Protocol-Version': _MCP_PROTOCOL}
    req = urllib.request.Request(_HIGGS_MCP_URL, data=json.dumps(body).encode('utf-8'),
                                 method='POST', headers=headers)
    try:
        resp = _OPENER.open(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        resp = e
    except (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionError, OSError) as e:
        raise EngineError('engine', f'no usable response from Higgsfield: {_safe(e)}', definitive=False) from e
    try:
        status = int(getattr(resp, 'status', None) or resp.getcode() or 0)
        if status >= 400:
            if status == 401:
                raise EngineError('auth', 'Higgsfield rejected the saved sign-in (HTTP 401); sign in again on Connections')
            raise _classify_http(status, resp.read(64 * 1024))
        if expect_id is None:
            return None
        ctype = (resp.headers.get('content-type') or '').split(';')[0].strip()
        try:
            if ctype == 'text/event-stream':
                data, seen = [], 0
                for raw in resp:
                    seen += len(raw)
                    if seen > _MCP_MAX_BYTES:
                        raise ValueError('response too large')
                    line = raw.decode('utf-8', errors='replace').rstrip('\r\n')
                    if line.startswith('data:'):
                        data.append(line[5:].lstrip())
                    elif line == '' and data:
                        hit = _mcp_match(data, expect_id)
                        data = []
                        if hit is not None:
                            return hit
                hit = _mcp_match(data, expect_id) if data else None
            else:
                hit = _mcp_match([resp.read(_MCP_MAX_BYTES + 1).decode('utf-8', errors='replace')], expect_id)
        except (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionError, OSError, ValueError) as e:
            raise EngineError('engine', f'no usable response from Higgsfield: {_safe(e)}', definitive=False) from e
        if hit is None:
            raise EngineError('engine', 'Higgsfield answered with no result for the request', definitive=False)
        return hit
    finally:
        resp.close()


def _mcp_match(chunks: list, rid: int) -> dict | None:
    try:
        msg = json.loads('\n'.join(chunks))
    except ValueError:
        return None
    for m in (msg if isinstance(msg, list) else [msg]):
        if isinstance(m, dict) and m.get('id') == rid and ('result' in m or 'error' in m):
            if 'error' in m:
                err = m['error'] if isinstance(m['error'], dict) else {}
                raise EngineError('invalid_input', f"Higgsfield refused the call: {_safe(err.get('message') or m['error'])}")
            return m['result'] if isinstance(m['result'], dict) else {}
    return None


def _mcp_text(res: dict) -> str:
    return '\n'.join(c.get('text', '') for c in (res.get('content') or [])
                     if isinstance(c, dict) and c.get('type') == 'text')


def _mcp_data(res: dict) -> dict:
    sc = res.get('structuredContent', res.get('structured'))
    if isinstance(sc, dict):
        return sc
    try:
        j = json.loads(_mcp_text(res))
    except ValueError:
        return {}
    return j if isinstance(j, dict) else {}


class HiggsfieldMcpAdapter:
    """Higgsfield over MCP, on the user's own plan credits. Same four methods as
    every adapter. Proven against the live server 2026-10-02 (spike section 8):
    `get_cost: true` quotes credits and submits nothing, `use_unlim` is pinned
    false so only credits are ever spent, a job is polled with
    `job_status {jobId, sync: true}`, and the result URL is signed for ~4 hours."""

    _TOOLS = {'image': 'generate_image', 'video': 'generate_video'}

    def _call(self, creds: _Creds, tool: str, arguments: dict) -> dict:
        _mcp_post(creds.secret, {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
            'protocolVersion': _MCP_PROTOCOL, 'capabilities': {},
            'clientInfo': {'name': 'Clayrune', 'version': '1'}}}, expect_id=1)
        _mcp_post(creds.secret, {'jsonrpc': '2.0', 'method': 'notifications/initialized'}, expect_id=None)
        res = _mcp_post(creds.secret, {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
                                       'params': {'name': tool, 'arguments': arguments}}, expect_id=2)
        res = res or {}
        if res.get('isError'):
            raise _classify_http(400, _mcp_text(res).encode('utf-8'))
        return res

    @staticmethod
    def _params(model: ModelDescriptor, req: GenerationRequest, **extra) -> dict:
        p: dict = {'model': model.model_id, 'prompt': req.prompt, 'count': req.count,
                   'aspect_ratio': req.aspect_ratio, 'use_unlim': False}     # credits only, never the free allowance
        if req.kind == 'video' and req.duration_sec:
            p['duration'] = req.duration_sec
        p.update(extra)
        return {'params': p}

    def estimate(self, model, req, creds) -> Estimate:
        out = _mcp_data(self._call(creds, self._TOOLS[model.kind], self._params(model, req, get_cost=True)))
        cost = out.get('cost')
        val = (cost.get('credits_exact', cost.get('credits')) if isinstance(cost, dict) else None)
        if isinstance(val, bool) or not isinstance(val, (int, float)) or val != val or val < 0:
            raise EngineError('engine', 'Higgsfield returned no usable credit quote, so nothing was sent')
        adj = out.get('adjustments') if isinstance(out.get('adjustments'), dict) and out.get('adjustments') else None
        return Estimate(usd=0.0, credits=float(val), basis='engine', read=now_iso(), adjustments=adj)

    def submit(self, model, req, creds) -> dict:
        out = _mcp_data(self._call(creds, self._TOOLS[model.kind], self._params(model, req)))
        ids = [r.get('id') for r in (out.get('results') or []) if isinstance(r, dict)]
        if not ids or not all(isinstance(i, str) and _REF_OK.match(i) for i in ids):
            raise EngineError('engine', 'Higgsfield accepted the job but returned no job id', definitive=False)
        adj = out.get('adjustments') if isinstance(out.get('adjustments'), dict) and out.get('adjustments') else None
        return {'ref': {'job_ids': ids}, 'adjustments': adj}

    def poll(self, model, ref, creds) -> dict:
        ids = ref.get('job_ids') or []
        if not ids or not all(isinstance(i, str) and _REF_OK.match(i) for i in ids):
            raise EngineError('engine', 'stored job id is malformed')
        urls, state = [], 'ready'
        for jid in ids:
            gen = _mcp_data(self._call(creds, 'job_status', {'jobId': jid, 'sync': True})).get('generation')
            gen = gen if isinstance(gen, dict) else {}
            st = str(gen.get('status') or '').lower()
            if st in ('failed', 'error'):
                return {'state': 'failed', 'kind': 'engine', 'free': False,
                        'message': _safe(gen.get('error') or 'Higgsfield reported a failure')}
            if st == 'nsfw':
                return {'state': 'failed', 'kind': 'moderation', 'free': True,
                        'message': 'the engine moderation filter rejected the content'}
            if st in ('canceled', 'cancelled'):
                return {'state': 'failed', 'kind': 'canceled', 'message': 'the job was canceled', 'free': True}
            if st in ('completed', 'complete', 'succeeded'):
                url = (gen.get('results') or {}).get('rawUrl') if isinstance(gen.get('results'), dict) else None
                if not isinstance(url, str) or not url:
                    return {'state': 'failed', 'kind': 'engine', 'free': False,
                            'message': 'completed with no output URL'}
                urls.append(url)
            elif st in ('queued', 'pending'):
                state = 'queued' if state == 'ready' else state
            else:
                state = 'rendering'
        if state == 'ready':
            return {'state': 'ready', 'urls': urls}
        return {'state': state}

    def fetch(self, model, item, creds) -> list:
        return [_download(u) for u in item['urls']]     # signed CDN links: no Higgsfield token is sent


class VeoAdapter:
    def _headers(self, c: _Creds) -> dict:
        return {'x-goog-api-key': c.secret}

    def estimate(self, model, req, creds) -> Estimate:
        usd, approx, note = _price_table_usd(model, req)
        return Estimate(usd=usd, basis='table', read=model.price['read'], approximate=approx, note=note)

    def submit(self, model, req, creds) -> dict:
        inst: dict = {'prompt': req.prompt}
        if req.first_frame:
            data, mime = _read_asset(req.first_frame)
            inst['image'] = {'inlineData': {'mimeType': mime, 'data': _b64(data)}}
        if req.reference_images:
            refs = []
            for r in req.reference_images:
                data, mime = _read_asset(r)
                refs.append({'image': {'inlineData': {'mimeType': mime, 'data': _b64(data)}},
                             'referenceType': 'asset'})
            inst['referenceImages'] = refs
        params: dict = {'aspectRatio': req.aspect_ratio, 'resolution': req.resolution,
                        'durationSeconds': str(req.duration_sec)}
        if req.negative_prompt:
            params['negativePrompt'] = req.negative_prompt
        if req.seed is not None:
            params['seed'] = req.seed
        out = _json_call('POST', f'{_GEMINI_BASE}/v1beta/models/{model.model_id}:predictLongRunning',
                         self._headers(creds), {'instances': [inst], 'parameters': params})
        name = out.get('name')
        if not isinstance(name, str) or not _REF_OK.match(name) or '..' in name:
            raise EngineError('engine', 'the engine accepted the job but returned no operation name',
                              definitive=False)
        return {'ref': {'operation': name}}

    def poll(self, model, ref, creds) -> dict:
        name = ref.get('operation', '')
        if not _REF_OK.match(name) or '..' in name:
            raise EngineError('engine', 'stored operation name is malformed')
        out = _json_call('GET', f'{_GEMINI_BASE}/v1beta/{name}', self._headers(creds))
        if not out.get('done'):
            return {'state': 'rendering'}
        if out.get('error'):
            err = out['error'] if isinstance(out['error'], dict) else {}
            msg = _safe(err.get('message') or 'the engine reported an error')
            low = msg.lower()
            kind = 'moderation' if ('safety' in low or 'blocked' in low or 'filter' in low) else 'engine'
            # Whether Google bills a failed or blocked run is not established
            # (scan "Dark"), so the reservation stands.
            return {'state': 'failed', 'kind': kind, 'message': msg, 'free': False}
        gen = ((out.get('response') or {}).get('generateVideoResponse') or {})
        urls = []
        for s in gen.get('generatedSamples') or []:
            uri = ((s or {}).get('video') or {}).get('uri')
            if uri:
                urls.append(uri)
        if urls:
            return {'state': 'ready', 'urls': urls}
        reasons = gen.get('raiMediaFilteredReasons')
        if gen.get('raiMediaFilteredCount') or reasons:
            return {'state': 'failed', 'kind': 'moderation',
                    'message': _safe(f'blocked by the safety filter: {reasons}' if reasons
                                     else 'blocked by the safety filter'), 'free': False}
        return {'state': 'failed', 'kind': 'engine', 'message': 'finished with no video', 'free': False}

    def fetch(self, model, item, creds) -> list:
        # The video URI needs the key, but only on Google's own hosts.
        return [_download(u, auth_headers=self._headers(creds), auth_host_suffix='googleapis.com')
                for u in item['urls']]


class GeminiImageAdapter:
    def estimate(self, model, req, creds) -> Estimate:
        usd, approx, note = _price_table_usd(model, req)
        return Estimate(usd=usd, basis='table', read=model.price['read'], approximate=approx, note=note)

    def submit(self, model, req, creds) -> dict:
        parts: list = [{'type': 'text', 'text': req.prompt}]
        for r in req.reference_images:
            data, mime = _read_asset(r)
            parts.append({'type': 'image', 'mime_type': mime, 'data': _b64(data)})
        fmt: dict = {'type': 'image', 'mime_type': 'image/png', 'aspect_ratio': req.aspect_ratio}
        if req.resolution:
            fmt['image_size'] = req.resolution
        out = _json_call('POST', f'{_GEMINI_BASE}/v1beta/interactions', {'x-goog-api-key': creds.secret},
                         {'model': model.model_id, 'input': parts, 'response_format': fmt},
                         timeout=_SYNC_TIMEOUT)
        img = self._find_image(out)
        if img is None:
            raise EngineError('moderation' if re.search(r'block|safety|prohibit', json.dumps(out)[:4000], re.I)
                              else 'engine', 'the engine returned no image', definitive=True)
        try:
            raw = base64.b64decode(img['data'], validate=False)
        except (ValueError, TypeError) as e:
            raise EngineError('engine', 'the engine returned image data that is not base64',
                              definitive=False) from e
        return {'outputs': [(raw, img.get('mime_type') or 'image/png')], 'cost_usd': None}

    @staticmethod
    def _find_image(out: dict) -> dict | None:
        """The response shape is documented only as `interaction.output_image.data`
        (scan, Dark: generateContent vs interactions), so accept it at the top
        level, under `interaction`, or as an image item in an output list."""
        inner = out.get('interaction')
        roots: list[dict] = [out, inner if isinstance(inner, dict) else {}]
        for root in roots:
            oi = root.get('output_image')
            if isinstance(oi, dict) and oi.get('data'):
                return oi
            for key in ('outputs', 'output'):
                items = root.get(key)
                if isinstance(items, list):
                    for it in items:
                        if isinstance(it, dict) and it.get('type') == 'image' and it.get('data'):
                            return it
        return None


class OpenAIImageAdapter:
    def _headers(self, c: _Creds) -> dict:
        return {'Authorization': f'Bearer {c.secret}'}

    def estimate(self, model, req, creds) -> Estimate:
        usd, approx, note = _price_table_usd(model, req)
        return Estimate(usd=usd, basis='table', read=model.price['read'], approximate=approx, note=note)

    def submit(self, model, req, creds) -> dict:
        w, h = _OPENAI_SIZES[req.aspect_ratio]
        fields = {'model': model.model_id, 'prompt': req.prompt, 'size': f'{w}x{h}',
                  'quality': model.vendor.get('quality', 'medium'), 'output_format': 'png',
                  'n': str(req.count)}
        if req.reference_images:
            files = [('image[]', f'ref{i}{Path(r["path"]).suffix.lower()}', *_swap(_read_asset(r)))
                     for i, r in enumerate(req.reference_images)]
            body, ctype = _multipart(fields, files)
            status, _rh, data = _transport_call(
                'POST', f'{_OPENAI_BASE}/v1/images/edits',
                headers={**self._headers(creds), 'Content-Type': ctype}, body=body,
                timeout=_SYNC_TIMEOUT, max_bytes=64 * 1024 * 1024)
            if not 200 <= status < 300:
                raise _classify_http(status, data)
            try:
                out = json.loads(data.decode('utf-8'))
            except ValueError as e:
                raise EngineError('engine', 'the engine answered with something that is not JSON',
                                  definitive=False) from e
        else:
            fields['n'] = req.count
            out = _json_call('POST', f'{_OPENAI_BASE}/v1/images/generations', self._headers(creds),
                             fields, timeout=_SYNC_TIMEOUT, max_bytes=64 * 1024 * 1024)
        outputs = []
        for d in (out.get('data') or []) if isinstance(out, dict) else []:
            if isinstance(d, dict) and d.get('b64_json'):
                try:
                    outputs.append((base64.b64decode(d['b64_json']), 'image/png'))
                except (ValueError, TypeError):
                    pass
        if not outputs:
            raise EngineError('engine', 'the engine returned no image', definitive=True)
        return {'outputs': outputs, 'cost_usd': self._billed(out.get('usage'))}

    @staticmethod
    def _billed(usage: Any) -> float | None:
        """Cost from the response's own `usage`: $30/M image output, $8/M image
        input, $5/M text input (OpenAI pricing page, read 2026-09-30)."""
        if not isinstance(usage, dict):
            return None
        try:
            out_t = float(usage['output_tokens'])
            det = usage.get('input_tokens_details') or {}
            img_in = float(det.get('image_tokens') or 0)
            txt_raw = det.get('text_tokens')
            txt_in = float(txt_raw if txt_raw is not None else (usage.get('input_tokens') or 0) - img_in)
        except (KeyError, TypeError, ValueError):
            return None
        return round((out_t * 30 + img_in * 8 + max(txt_in, 0) * 5) / 1_000_000, 6)


def _swap(pair: tuple[bytes, str]) -> tuple[str, bytes]:
    return pair[1], pair[0]


def _multipart(fields: dict, files: list) -> tuple[bytes, str]:
    """`files` rows are (field, filename, mime, bytes)."""
    boundary = f'----deskengines{uuid.uuid4().hex}'
    out = bytearray()
    for k, v in fields.items():
        out += (f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n').encode()
    for field_name, fname, mime, data in files:
        out += (f'--{boundary}\r\nContent-Disposition: form-data; name="{field_name}"; '
                f'filename="{fname}"\r\nContent-Type: {mime}\r\n\r\n').encode()
        out += data + b'\r\n'
    out += f'--{boundary}--\r\n'.encode()
    return bytes(out), f'multipart/form-data; boundary={boundary}'


ADAPTERS: dict[str, Any] = {
    'higgsfield': HiggsfieldAdapter(),
    'higgsfield_mcp': HiggsfieldMcpAdapter(),
    'veo': VeoAdapter(),
    'gemini_image': GeminiImageAdapter(),
    'openai_image': OpenAIImageAdapter(),
}


def _adapter(model: ModelDescriptor):
    return ADAPTERS[model.vendor['adapter']]


# -- job store ----------------------------------------------------------------

def _empty_store() -> dict:
    return {'version': STORE_VERSION, 'jobs': {}, 'idem': {}, 'limits': {}, 'renders': {}, 'render_idem': {}}


def _read_store() -> dict:
    if JOBS_PATH is None or not Path(JOBS_PATH).exists():
        return _empty_store()
    try:
        data = json.loads(Path(JOBS_PATH).read_text(encoding='utf-8'))
    except Exception as e:
        # Unlike the Desk's lenient reads this one is spend accounting: an
        # empty store would read as "nothing spent" and re-open a used budget.
        raise RuntimeError(f'engine job store {JOBS_PATH} is unreadable ({e}); '
                           f'refusing to continue so spend is not undercounted') from e
    if not isinstance(data, dict) or not isinstance(data.get('jobs'), dict):
        raise RuntimeError(f'engine job store {JOBS_PATH} is malformed')
    for k in ('idem', 'limits', 'renders', 'render_idem'):
        data.setdefault(k, {})
    return data


def _write_store(store: dict) -> None:
    if JOBS_PATH is None:
        raise RuntimeError('desk_engines.JOBS_PATH is not wired; refusing to spend without a durable job record')
    Path(JOBS_PATH).parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(Path(JOBS_PATH), json.dumps(store, indent=2, ensure_ascii=False))


def _public_job(job: dict) -> dict:
    """The Job shape of the contract. `engine_ref` stays server-side."""
    out = _public_job_usd(job)
    if job.get('cost_credits') is not None:          # a credits engine: say so in the unit it spends
        out['cost_credits'] = job['cost_credits']
        out['currency'] = 'credits'
    if job.get('adjustments'):
        out['adjustments'] = job['adjustments']
    return out


def _public_job_usd(job: dict) -> dict:
    return {
        'job_id': job['job_id'], 'status': job['status'], 'failure': job.get('failure'),
        'progress': None, 'outputs': job.get('outputs') or [], 'cost_usd': job.get('cost_usd', 0.0),
        'engine_id': job['engine_id'], 'model_id': job['model_id'], 'kind': job['kind'],
        'campaign_id': job.get('campaign_id'), 'desk': job.get('desk') or {},
        'estimate': job.get('estimate'), 'created_at': job.get('created_at'),
        'updated_at': job.get('updated_at'),
    }


def get_job(job_id: str) -> dict | None:
    with _lock:
        job = _read_store()['jobs'].get(job_id)
    return _public_job(job) if job else None


# -- budget -------------------------------------------------------------------

def _campaign(campaign_id: str) -> dict | None:
    return next((c for c in _desk.list_campaigns() if c.get('id') == campaign_id), None)


def _num(v: Any) -> float:
    try:
        f = float(v)
        return f if f == f and f not in (float('inf'), float('-inf')) else 0.0
    except (TypeError, ValueError):
        return 0.0


def budget_state(campaign_id: str, *, _store: dict | None = None) -> dict:
    """`{amount, spent, remaining}` for the campaign's own `how.budget`.
    `spent` is every non-free engine job (reserved or settled) plus the
    campaign's ledger post costs, because the budget is one number the user
    set for the whole campaign. No budget set (`source: none`) means amount 0:
    nothing to spend, so every job is refused."""
    camp = _campaign(campaign_id)
    if camp is None:
        raise Refused('campaign_not_found', 'campaign not found', 404)
    b = (camp.get('how') or {}).get('budget') or {}
    amount = _num(b.get('amount')) if b.get('source', 'none') != 'none' else 0.0
    store = _store if _store is not None else _read_store()
    spent = sum(_num(j.get('cost_usd')) for j in store['jobs'].values()
                if j.get('campaign_id') == campaign_id)
    spent += sum(_num(r.get('cost')) for r in _desk.list_ledger(limit=10 ** 9)
                 if r.get('campaign_id') == campaign_id)
    return {'amount': round(amount, 6), 'spent': round(spent, 6),
            'remaining': round(max(amount - spent, 0.0), 6), 'source': b.get('source', 'none')}


# -- per-job limit (set per engine on Connections) -----------------------------

MAX_JOB_LIMIT_USD = 10_000.0


def _is_credits(engine_id: str) -> bool:
    return ENGINES[engine_id].currency == 'credits'


def _amount(est: 'Estimate', engine_id: str) -> float:
    """What a cap compares against: credits for a credits engine, else USD."""
    return float(est.credits or 0.0) if _is_credits(engine_id) else est.usd


def get_limits() -> dict:
    """`{engine_id: usd}` for every engine that has a per-job limit set."""
    with _lock:
        return {k: float(v) for k, v in _read_store()['limits'].items() if k in ENGINES}


def set_limit(engine_id: str, usd: Any) -> dict:
    """Set (a positive number) or clear (None) one engine's per-job limit, in
    the engine's own unit (USD, or credits for a credits engine). Returns
    `{engine_id, job_limit_usd}` or `{engine_id, job_limit_credits}`. The route
    that reaches this is human-only: raising a limit loosens a spending gate."""
    if engine_id not in ENGINES:
        raise Refused('unknown_engine', f'unknown engine {engine_id!r}', 404)
    unit = 'credits' if _is_credits(engine_id) else 'USD'
    if usd is not None:
        if isinstance(usd, bool) or not isinstance(usd, (int, float)) or usd != usd \
                or not 0 < usd <= MAX_JOB_LIMIT_USD:
            raise Refused('invalid_input',
                          f'the limit must be a number above 0 and at most {MAX_JOB_LIMIT_USD:,.0f} {unit} (or null to clear it)', 400)
        usd = round(float(usd), 6)
    with _lock:
        store = _read_store()
        if usd is None:
            store['limits'].pop(engine_id, None)
        else:
            store['limits'][engine_id] = usd
        _write_store(store)
    return {'engine_id': engine_id, ('job_limit_credits' if unit == 'credits' else 'job_limit_usd'): usd}


def check_caps(usd: float, engine_id: str, campaign_id: str | None, store: dict, *, estimate: dict | None = None,
               enforce_limit: bool = True) -> dict:
    """Raise `Refused` when `usd` (the estimate for ONE Render click) is over a
    cap; otherwise return `{limit, budget}` for display. `enforce_limit=False`
    is for the child jobs of a render whose total was already checked."""
    if _is_credits(engine_id):
        # Credits: the per-job limit is the ONLY cap. The campaign budget is USD
        # and there is no exchange rate to credits, so it is not applied here;
        # a credits engine therefore always needs a limit (no campaign fallback).
        climit = store['limits'].get(engine_id) if enforce_limit else None
        if enforce_limit:
            label = ENGINES[engine_id].label
            if climit is None:
                raise Refused('no_job_limit',
                              f"no per-job limit is set for {label}: set one in credits on Connections first", 409)
            if usd > climit + 1e-9:
                raise Refused('over_job_limit',
                              f"estimate {usd:g} credits is over the {climit:g} credit per-job limit for "
                              f"{label} (change it on Connections)", 409,
                              job_limit_credits=climit, **({"estimate": estimate} if estimate else {}))
        return {'limit': climit, 'budget': None}
    limit = store['limits'].get(engine_id) if enforce_limit else None
    if enforce_limit:
        if limit is None and not campaign_id:
            raise Refused('no_job_limit',
                          f"no per-job limit is set for {ENGINES[engine_id].label}: nothing else caps a render "
                          f"outside a campaign, so set one on Connections first", 409)
        if limit is not None and usd > limit + 1e-9:
            raise Refused('over_job_limit',
                          f"estimate ${usd:.4f} is over the ${limit:.2f} per-job limit for "
                          f"{ENGINES[engine_id].label} (change it on Connections)", 409,
                          job_limit_usd=limit, **({"estimate": estimate} if estimate else {}))
    budget = None
    if campaign_id:
        budget = budget_state(campaign_id, _store=store)
        if usd > budget['remaining'] + 1e-9:
            raise Refused('over_budget',
                          f"estimate ${usd:.4f} is over the campaign's remaining budget "
                          f"${budget['remaining']:.4f} (amount ${budget['amount']:.2f}, spent ${budget['spent']:.4f})",
                          409, budget=budget, **({"estimate": estimate} if estimate else {}))
    return {'limit': limit, 'budget': budget}


# -- request parsing ----------------------------------------------------------

def parse_request(d: dict) -> GenerationRequest:
    """A JSON body -> GenerationRequest, ignoring every key that is not a
    contract field (so a passcode in the body is never carried along)."""
    if not isinstance(d, dict):
        raise Refused('invalid_input', 'body must be a JSON object', 400)

    def s(k, default=None):
        v = d.get(k, default)
        return v.strip() if isinstance(v, str) else v

    def i(k):
        v = d.get(k)
        if v is None:
            return None
        if isinstance(v, bool) or not isinstance(v, int):
            raise Refused('invalid_input', f'{k} must be an integer', 400)
        return v

    refs = d.get('reference_images') or []
    if not isinstance(refs, list):
        raise Refused('invalid_input', 'reference_images must be a list', 400)
    desk_raw = d.get('desk')
    desk: dict = desk_raw if isinstance(desk_raw, dict) else {}
    for k in ('first_frame', 'last_frame'):
        if d.get(k) is not None and not isinstance(d.get(k), dict):
            raise Refused('invalid_input', f'{k} must be an asset reference', 400)
    count = d.get('count')
    if count is None:
        count = 1
    if isinstance(count, bool) or not isinstance(count, int):
        raise Refused('invalid_input', 'count must be an integer', 400)
    return GenerationRequest(
        engine_id=str(s('engine_id') or ''), model_id=str(s('model_id') or ''),
        kind=str(s('kind') or ''), prompt=str(d.get('prompt') or ''),
        aspect_ratio=str(s('aspect_ratio') or ''),
        negative_prompt=(s('negative_prompt') or None) if isinstance(d.get('negative_prompt'), (str, type(None))) else None,
        duration_sec=i('duration_sec'), resolution=s('resolution') or None,
        audio=d.get('audio') if isinstance(d.get('audio'), bool) else None,
        count=count, seed=i('seed'), first_frame=d.get('first_frame'), last_frame=d.get('last_frame'),
        reference_images=refs,
        desk={k: desk[k] for k in ('piece_id', 'scene_id', 'revision', 'idempotency_key')
              if desk.get(k) is not None})


def _resolve(req: GenerationRequest) -> tuple[EngineDescriptor, ModelDescriptor]:
    eng = ENGINES.get(req.engine_id)
    if eng is None:
        raise Refused('unknown_engine', f'unknown engine {req.engine_id!r}', 400)
    model = get_model(req.engine_id, req.model_id)
    if model is None:
        raise Refused('unknown_model', f'{req.engine_id} has no model {req.model_id!r}', 400)
    problems = validate_request(model, req)
    if problems:
        raise Refused('invalid_input', '; '.join(problems), 400, problems=problems)
    return eng, model


def _project_for(campaign_id: str) -> str | None:
    camp = _campaign(campaign_id)
    return (camp or {}).get('project_id')


# -- public operations --------------------------------------------------------

def list_engines(project_id: str | None = None) -> list[dict]:
    """Every engine descriptor with its connected status and the per-job USD
    limit the user set for it (null = none). Metadata only."""
    limits = get_limits()
    out = []
    for e in _ENGINES:
        d = e.public()
        d['connected'] = connection(e.id, project_id)
        d['job_limit_usd'] = None if e.currency == 'credits' else limits.get(e.id)
        if e.currency == 'credits':
            d['job_limit_credits'] = limits.get(e.id)
        out.append(d)
    return out


def estimate(d: dict, *, unattended: bool = False) -> dict:
    """Estimate one request. Free (a vendor estimate call costs nothing), so
    no passcode, but it does read the credential for an `endpoint` engine."""
    req = parse_request(d)
    _eng, model = _resolve(req)
    campaign_id = d.get('campaign_id')
    project_id = _project_for(campaign_id) if campaign_id else d.get('project_id')
    try:
        creds = _creds(req.engine_id, project_id, unattended) if ENGINES[req.engine_id].estimate == 'endpoint' \
            else _Creds(secret='')
        est = _adapter(model).estimate(model, req, creds)
    except NotConnected:
        raise
    except EngineError as e:
        raise Refused('estimate_failed', str(e), 502, failure=e.kind)
    return with_caps({'estimate': est.public()}, _amount(est, req.engine_id), req.engine_id, campaign_id)


def with_caps(out: dict, usd: float, engine_id: str, campaign_id: str | None) -> dict:
    """Add what the user needs BEFORE pressing Render: the engine's per-job
    limit, the campaign budget, and `refusal` = the reason a Render of `usd`
    would be refused (null when it would go through)."""
    with _lock:
        store = _read_store()
    if _is_credits(engine_id):
        out['job_limit_usd'] = None
        out['job_limit_credits'] = store['limits'].get(engine_id)
        out['currency'] = 'credits'
    else:
        out['job_limit_usd'] = store['limits'].get(engine_id)
    out['refusal'] = None
    if campaign_id and not _is_credits(engine_id):
        b = budget_state(campaign_id, _store=store)
        out['budget'] = b
        out['fits'] = usd <= b['remaining'] + 1e-9
    try:
        check_caps(usd, engine_id, campaign_id or None, store)
    except Refused as e:
        out['refusal'] = {'code': e.code, 'message': str(e)}
    return out


def _new_job(req: GenerationRequest, camp: dict | None, est: Estimate, project_id: str | None = None) -> dict:
    now = now_iso()
    return {
        'job_id': f'gen-{uuid.uuid4().hex[:10]}', 'engine_id': req.engine_id, 'model_id': req.model_id,
        'kind': req.kind, 'status': 'queued', 'failure': None, 'outputs': [],
        'cost_usd': round(est.usd, 6), 'estimate': est.public(),
        **({'cost_credits': round(est.credits, 6), 'adjustments': est.adjustments}
           if est.credits is not None else {}),
        'campaign_id': camp['id'] if camp else None,
        'project_id': camp.get('project_id') if camp else project_id,
        'desk': dict(req.desk), 'request': asdict(req), 'engine_ref': None,
        'created_at': now, 'updated_at': now,
    }


def _fail(job: dict, kind: str, message: str, *, free: bool) -> None:
    job['status'] = 'failed'
    job['failure'] = {'kind': kind, 'message': message}
    job['updated_at'] = now_iso()
    if free:
        job['cost_usd'] = 0.0
        if job.get('cost_credits') is not None:
            job['cost_credits'] = 0.0


def _render_scope(d: dict) -> tuple[str | None, dict | None, str | None]:
    """`(campaign_id, campaign, project_id)` for a render request. A request
    with no `campaign_id` is a Studio render: no campaign, so no campaign
    budget, and the engine's per-job limit is its only cap."""
    campaign_id = d.get('campaign_id')
    if campaign_id in (None, ''):
        pid = d.get('project_id')
        return None, None, pid if isinstance(pid, str) and pid else None
    if not isinstance(campaign_id, str):
        raise Refused('invalid_input', 'campaign_id must be a string', 400)
    camp = _campaign(campaign_id)
    if camp is None:
        raise Refused('campaign_not_found', 'campaign not found', 404)
    if camp.get('state') in ('done', 'dropped'):
        raise Refused('campaign_closed', f"campaign is {camp.get('state')}; it cannot render", 409)
    return campaign_id, camp, camp.get('project_id')


def submit(d: dict, *, unattended: bool = False, _skip_limit: bool = False) -> tuple[dict, bool]:
    """Submit one render job -> (job, replay). SPENDS MONEY: the route gates
    on a human + the passcode before calling this. Raises `Refused`,
    `NotConnected` (nothing sent) or returns the job (queued/rendering/ready).
    `_skip_limit` is for the children of `render()`, whose TOTAL was already
    held to the per-job limit; the campaign budget is still checked per job."""
    req = parse_request(d)
    key = req.desk.get('idempotency_key')
    if not isinstance(key, str) or not key.strip() or len(key) > 120:
        raise Refused('invalid_input', 'desk.idempotency_key is required (a retried click must not spend twice)', 400)
    campaign_id, camp, project_id = _render_scope(d)
    _eng, model = _resolve(req)
    idem = f'{campaign_id or "-"}:{key}'

    with _lock:
        existing = _read_store()['idem'].get(idem)
        if existing:
            return _public_job(_read_store()['jobs'][existing]), True

    creds = _creds(req.engine_id, project_id, unattended)
    adapter = _adapter(model)
    try:
        est = adapter.estimate(model, req, creds)
    except EngineError as e:
        raise Refused('estimate_failed', f'could not get a price, so nothing was sent: {e}', 502, failure=e.kind)

    with _lock:
        store = _read_store()
        if idem in store['idem']:
            return _public_job(store['jobs'][store['idem'][idem]]), True
        check_caps(_amount(est, req.engine_id), req.engine_id, campaign_id, store, estimate=est.public(),
                   enforce_limit=not _skip_limit)
        job = _new_job(req, camp, est, project_id)
        store['jobs'][job['job_id']] = job
        store['idem'][idem] = job['job_id']
        _write_store(store)
    job_id = job['job_id']

    def release():
        with _lock:
            s = _read_store()
            s['jobs'].pop(job_id, None)
            s['idem'].pop(idem, None)
            _write_store(s)

    def settle(mutate: Callable[[dict], None]) -> dict:
        with _lock:
            s = _read_store()
            j = s['jobs'][job_id]
            mutate(j)
            j['updated_at'] = now_iso()
            _write_store(s)
            return dict(j)

    try:
        res = adapter.submit(model, req, creds)
    except EngineError as e:
        if e.definitive:
            release()
            raise Refused('engine_refused', str(e), 502, failure=e.kind)
        # Unknown whether the vendor accepted: keep the reservation and the
        # idempotency key, and say so, as desk_publish does for a dropped POST.
        done = settle(lambda j: _fail(j, e.kind, f'{e} The engine may have accepted this job; check '
                                                 f'its dashboard before retrying with a new key.', free=False))
        return _public_job(done), False
    except Refused:
        release()
        raise
    except Exception as e:
        # Programming error, not a vendor answer: nothing is known to have gone out.
        _log(f'[desk_engines] adapter crashed for {req.engine_id}: {_safe(e)}', flush=True)
        done = settle(lambda j: _fail(j, 'engine', 'internal error while submitting; check the server log', free=False))
        return _public_job(done), False

    if 'outputs' in res:                                   # sync engine
        try:
            saved = _save_outputs(job, req, res['outputs'])
        except (EngineError, OSError) as e:
            # The vendor already rendered (and billed) this; keep the cost.
            done = settle(lambda j: _fail(j, 'engine', f'rendered, but the output could not be saved: {_safe(e)}', free=False))
            return _public_job(done), False

        def ready(j):
            j['status'] = 'ready'
            j['outputs'] = saved
            if res.get('cost_usd') is not None:
                j['cost_usd'] = round(res['cost_usd'], 6)
                j['estimate'] = {**(j.get('estimate') or {}), 'billed_usd': j['cost_usd']}
        return _public_job(settle(ready)), False

    def accepted(j):
        j['engine_ref'] = res['ref']
        j['status'] = 'queued'
        if res.get('adjustments'):
            j['adjustments'] = res['adjustments']     # what the engine changed, shown to the user
    return _public_job(settle(accepted)), False


def poll(job_id: str, *, unattended: bool = False) -> dict | None:
    """Advance one job by asking its engine once; download output on ready.
    Free. A terminal job is returned untouched, with no vendor call."""
    with _lock:
        job = _read_store()['jobs'].get(job_id)
    if job is None:
        return None
    if job['status'] in ('ready', 'failed'):
        return _public_job(job)
    eng = ENGINES[job['engine_id']]
    model = get_model(job['engine_id'], job['model_id'])
    if model is None:      # a model dropped from the registry after submit
        return _public_job(job)

    def settle(mutate: Callable[[dict], None]) -> dict:
        with _lock:
            s = _read_store()
            j = s['jobs'][job_id]
            if j['status'] in ('ready', 'failed'):          # a concurrent poll finished it
                return _public_job(j)
            mutate(j)
            j['updated_at'] = now_iso()
            _write_store(s)
            return _public_job(j)

    if job.get('engine_ref') is None:
        # A sync job whose process died mid-call. Its cost stays reserved: we
        # cannot tell whether the vendor billed it.
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(job['created_at'].replace('Z', '+00:00'))).total_seconds()
        if eng.job_model == 'sync' and age > _SYNC_STALE_SECONDS:
            return settle(lambda j: _fail(j, 'engine', 'the render was interrupted before it finished; '
                                                       'the cost stays reserved until the engine bill is checked',
                                          free=False))
        return _public_job(job)

    try:
        creds = _creds(job['engine_id'], job.get('project_id'), unattended)
        adapter = _adapter(model)
        step = adapter.poll(model, job['engine_ref'], creds)
    except NotConnected as e:
        return {**_public_job(job), 'poll_error': {'kind': 'auth', 'message': e.reason}}
    except EngineError as e:
        if e.kind in ('auth', 'quota') or not e.definitive:
            return {**_public_job(job), 'poll_error': {'kind': e.kind, 'message': str(e)}}
        return settle(lambda j: _fail(j, e.kind, str(e), free=False))

    if step['state'] in ('queued', 'rendering'):
        return settle(lambda j: j.__setitem__('status', step['state']))
    if step['state'] == 'failed':
        return settle(lambda j: _fail(j, step['kind'], step['message'], free=step.get('free', False)))

    # ready: download now, before the engine's retention window closes.
    def stamp(j):
        j['status'] = 'rendering'
        j.setdefault('ready_seen_at', now_iso())
    seen = settle(stamp)
    with _lock:
        ready_at = _read_store()['jobs'][job_id].get('ready_seen_at') or now_iso()
    try:
        blobs = adapter.fetch(model, step, creds)
    except EngineError as e:
        ttl = eng.output_ttl_hours
        age_h = (datetime.now(timezone.utc) - datetime.fromisoformat(ready_at.replace('Z', '+00:00'))).total_seconds() / 3600
        if ttl and age_h > ttl:
            return settle(lambda j: _fail(j, 'expired', f'the output was not downloaded within {ttl} h and the engine removed it', free=False))
        return {**seen, 'poll_error': {'kind': e.kind, 'message': str(e)}}
    saved = _save_outputs(job, GenerationRequest(**{k: v for k, v in job['request'].items()}), blobs)

    def ready(j):
        j['status'] = 'ready'
        j['outputs'] = saved
    return settle(ready)


# -- outputs ------------------------------------------------------------------

_EXT = {'video/mp4': '.mp4', 'video/webm': '.webm', 'image/png': '.png', 'image/jpeg': '.jpg',
        'image/webp': '.webp', 'image/gif': '.gif'}


def _png_jpeg_size(data: bytes) -> tuple[int | None, int | None]:
    if data[:8] == b'\x89PNG\r\n\x1a\n' and len(data) >= 24:
        return int.from_bytes(data[16:20], 'big'), int.from_bytes(data[20:24], 'big')
    if data[:2] == b'\xff\xd8':
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            m = data[i + 1]
            if m in (0xC0, 0xC1, 0xC2):
                return int.from_bytes(data[i + 7:i + 9], 'big'), int.from_bytes(data[i + 5:i + 7], 'big')
            i += 2 + int.from_bytes(data[i + 2:i + 4], 'big')
    return None, None


LIBRARY_ROOT = ('desk', 'library')          # the same tree mc/desk_pieces.py lists as the Material library
GENERATED_FOLDER = 'Generated'              # <library>/<video|image>/Generated/


def _library_folder(kind: str) -> Path:
    if UPLOADS_ROOT is None:
        raise EngineError('engine', 'the uploads directory is not wired; cannot keep the output')
    folder = Path(UPLOADS_ROOT).joinpath(*LIBRARY_ROOT, 'video' if kind == 'video' else 'image', GENERATED_FOLDER)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _describe_output(path: Path, mime: str, *, width=None, height=None, duration_sec=None) -> dict:
    """What a saved output looks like to the page: the library path (relative
    to data/uploads, the form every Desk asset uses) and, for an image, the
    /api/serve-image URL the browser can draw."""
    root = Path(UPLOADS_ROOT).resolve()  # type: ignore[arg-type]
    rel = path.resolve().relative_to(root).as_posix()
    is_image = mime.startswith('image/')
    return {'local_path': str(path), 'path': rel, 'mime': mime, 'width': width, 'height': height,
            'duration_sec': duration_sec,
            'src': ('/api/serve-image?path=' + urllib.parse.quote(str(root / rel), safe='')) if is_image else None}


def _attach_to_piece(piece_id: str | None, outputs: list[dict]) -> None:
    """Attach saved outputs to the piece that asked for them. The files are
    already in the library, so a refusal here (piece deleted meanwhile, 50
    assets already) keeps the output and says why on it; it never loses it."""
    if not piece_id:
        return
    for o in outputs:
        try:
            _pieces.add_asset(piece_id, path=o['path'], title=Path(o['path']).name)
            o['attached_to'] = piece_id
        except Exception as e:
            o['attach_error'] = _safe(e)
            _log(f'[desk_engines] could not attach {o["path"]} to piece {piece_id}: {_safe(e)}', flush=True)


def _save_outputs(job: dict, req: GenerationRequest, blobs: list) -> list[dict]:
    """Write each output into the material library
    (`data/uploads/desk/library/<video|image>/Generated/`, the tree the Desk
    materials list and /api/serve-image share), never left only on the vendor
    (Veo removes it after 48 h), and attach it to `desk.piece_id` when the job
    names a piece. (The clips of a stitched render name no piece: the finished
    video attaches instead, see `render()`.)"""
    folder = _library_folder(req.kind)
    out = []
    for n, (data, mime) in enumerate(blobs):
        mime = mime if mime in _EXT else ('video/mp4' if req.kind == 'video' else 'image/png')
        path = folder / f"{job['job_id']}-{n}{_EXT.get(mime) or mimetypes.guess_extension(mime) or ''}"
        path.write_bytes(data)
        w, h = _png_jpeg_size(data)
        out.append(_describe_output(path, mime, width=w, height=h,
                                    duration_sec=req.duration_sec if req.kind == 'video' else None))
    _attach_to_piece((job.get('desk') or {}).get('piece_id'), out)
    return out


# -- storyboard render (video): one clip per scene, then stitch / crop ----------
#
# A video render is the persisted storyboard (mc/desk_storyboard.py) turned into
# one engine job per scene, then joined by ffmpeg on THIS machine (no vendor
# account, no cost; Remotion was dropped, journal c7ac1e8c item 5). The whole
# render is one Render click, so the per-job limit is checked against the TOTAL
# of its scenes, and a campaign render is checked against what is left of
# `how.budget` for that total too, before the first clip is sent. ffmpeg missing
# never fails a paid render: the clips are already in the library, the render is
# `held` with the reason, and nothing is installed for the user.

RENDER_STATUSES = ('queued', 'rendering', 'ready', 'held', 'failed')
_RATIO_NATIVE_FALLBACK = '16:9'          # what a model with no 1:1 is generated at, then cropped
_FFMPEG_TIMEOUT = 600
_finalizing: set[str] = set()


def _ffmpeg() -> str | None:
    """The ffmpeg on this host's PATH, or None. Never installed for the user."""
    return shutil.which('ffmpeg')


def _ffprobe() -> str | None:
    return shutil.which('ffprobe')


def _run_ffmpeg(cmd: list[str]) -> tuple[int, str]:
    """Run one ffmpeg command -> (returncode, stderr tail). The one place the
    join starts a subprocess, so tests replace it."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=_FFMPEG_TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        return 124, f'timed out after {_FFMPEG_TIMEOUT}s'
    except OSError as e:
        return 127, _safe(e)
    return p.returncode, (p.stderr or '')[-600:]


def stitch_command(ffmpeg: str, list_file: str, out_path: str, *, crop_square: bool, copy: bool) -> list[str]:
    """The ffmpeg argv that joins the clips named in `list_file` (concat
    demuxer) into `out_path`. Stream copy only when nothing changes the
    pictures (`copy` and no crop); a 1:1 crop is a centre square, which has to
    re-encode the video."""
    cmd = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-y', '-f', 'concat', '-safe', '0', '-i', list_file]
    if copy and not crop_square:
        cmd += ['-c', 'copy']
    else:
        if crop_square:
            cmd += ['-vf', "crop='min(iw,ih)':'min(iw,ih)'"]
        cmd += ['-c:v', 'libx264', '-preset', 'medium', '-crf', '18', '-pix_fmt', 'yuv420p', '-c:a', 'aac']
    cmd += ['-movflags', '+faststart', out_path]
    return cmd


def _concat_list(paths: list[Path]) -> str:
    def q(p: Path) -> str:
        return str(p).replace('\\', '/').replace("'", "'\\''")
    return ''.join(f"file '{q(p)}'\n" for p in paths)


def _codecs_match(paths: list[Path]) -> bool:
    """True only when ffprobe says every clip has the same video codec, size,
    pixel format and audio codec, the condition under which the concat demuxer
    can copy streams. No ffprobe, or any probe failure, is a mismatch: the
    caller then re-encodes, which is always correct, only slower."""
    probe = _ffprobe()
    if probe is None:
        return False
    seen = None
    for p in paths:
        cmd = [probe, '-v', 'error', '-show_entries', 'stream=codec_type,codec_name,width,height,pix_fmt',
               '-of', 'json', str(p)]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)
            streams = json.loads(res.stdout or '{}').get('streams') or []
        except (OSError, subprocess.TimeoutExpired, ValueError):
            return False
        if res.returncode != 0 or not streams:
            return False
        sig = sorted((s.get('codec_type'), s.get('codec_name'), s.get('width'), s.get('height'), s.get('pix_fmt'))
                     for s in streams)
        if seen is None:
            seen = sig
        elif sig != seen:
            return False
    return True


def _scene_prompt(scene: dict) -> str:
    label, line = (scene.get('label') or '').strip(), (scene.get('line') or '').strip()
    return f'{label}. {line}' if label and line else (line or label)


def _snap_duration(model: ModelDescriptor, seconds) -> int:
    """The shortest length this model offers that covers the scene (the longest,
    when the scene is longer than any it offers)."""
    need = max(1, int(math.ceil(float(seconds or 1))))
    durs = model.durations_sec
    if isinstance(durs, list) and durs:
        longer = [d for d in durs if d >= need]
        return min(longer) if longer else max(durs)
    return need


def _owner_scope(owner: Any, d: dict) -> tuple[str, str, str | None, str | None]:
    """`(kind, id, campaign_id, project_id)` of whose storyboard this is. A
    piece's campaign is read from the piece, never taken from the request, so a
    caller cannot dodge a campaign budget by leaving it out."""
    if not isinstance(owner, dict) or owner.get('kind') not in ('piece', 'studio') \
            or not isinstance(owner.get('id'), str) or not owner['id']:
        raise Refused('invalid_input', 'owner must be {"kind": "piece"|"studio", "id": "<id>"}', 400)
    kind, oid = owner['kind'], owner['id']
    if kind == 'piece':
        try:
            piece = _pieces.get_stored_piece(oid)
        except _pieces.PieceError as e:
            raise Refused('piece_not_found', str(e), e.status)
        campaign_id = piece.get('campaign_id')
        camp = _campaign(campaign_id) if campaign_id else None
        if camp is not None and camp.get('state') in ('done', 'dropped'):
            raise Refused('campaign_closed', f"campaign is {camp.get('state')}; it cannot render", 409)
        return kind, oid, campaign_id or None, (camp or {}).get('project_id')
    pid = d.get('project_id')
    return kind, oid, None, pid if isinstance(pid, str) and pid else None


def _plan_render(d: dict, *, unattended: bool) -> dict:
    """Validate a storyboard render and price it. Sends nothing, spends nothing.
    Raises `Refused` (the request or the storyboard cannot be rendered) or
    `NotConnected`."""
    from mc import desk_storyboard as _storyboard
    if not isinstance(d, dict):
        raise Refused('invalid_input', 'body must be a JSON object', 400)
    kind, oid, campaign_id, project_id = _owner_scope(d.get('owner'), d)
    try:
        board = _storyboard.get_storyboard(kind, oid)
    except _pieces.PieceError as e:
        raise Refused('storyboard_unavailable', str(e), e.status)
    scenes = board['scenes']
    if not scenes:
        raise Refused('no_scenes', 'this storyboard has no scenes to render: add one first', 409)

    engine_id, model_id = str(d.get('engine_id') or '').strip(), str(d.get('model_id') or '').strip()
    eng = ENGINES.get(engine_id)
    if eng is None:
        raise Refused('unknown_engine', f'unknown engine {engine_id!r}', 400)
    model = get_model(engine_id, model_id)
    if model is None:
        raise Refused('unknown_model', f'{engine_id} has no model {model_id!r}', 400)
    if model.kind != 'video':
        raise Refused('invalid_input', f'{model_id} makes {model.kind}, not video', 400)

    ratio = str(d.get('aspect_ratio') or '').strip()
    if ratio not in _DESK_RATIOS:
        raise Refused('invalid_input', f'aspect_ratio must be one of {", ".join(_DESK_RATIOS)}', 400)
    gen_ratio, crop = ratio, False
    if model.aspect_ratios and ratio not in model.aspect_ratios:
        if ratio == '1:1' and _RATIO_NATIVE_FALLBACK in model.aspect_ratios:
            gen_ratio, crop = _RATIO_NATIVE_FALLBACK, True
        else:
            raise Refused('invalid_input', f'{model_id} cannot do {ratio} (it does {", ".join(model.aspect_ratios)})', 400)
    resolution = d.get('resolution') if isinstance(d.get('resolution'), str) and d.get('resolution') else None
    if resolution is None and model.resolutions:
        resolution = model.resolutions[0]
    audio = d.get('audio') if isinstance(d.get('audio'), bool) else None

    creds = None
    adapter = _adapter(model)
    if eng.estimate == 'endpoint':
        creds = _creds(engine_id, project_id, unattended)
    problems, rows, total, total_amount, approximate = [], [], 0.0, 0.0, False
    root = Path(UPLOADS_ROOT).resolve() if UPLOADS_ROOT is not None else None
    for n, sc in enumerate(scenes, 1):
        where = f'scene {n} ({sc.get("label") or "untitled"})'
        pic = sc.get('picture')
        first_frame, refs = None, []
        if pic:
            ref = {'path': str(root / pic['path'])} if root is not None else None
            if model.inputs.get('first_frame'):
                first_frame = ref
            elif model.inputs.get('reference_images_max', 0) >= 1:
                refs = [ref]
            else:
                problems.append(f'{where} has a picture, but {model_id} takes no picture: choose an '
                                f'image-to-video model, or remove the picture')
                continue
        secs = _snap_duration(model, sc.get('duration_sec'))
        req = GenerationRequest(
            engine_id=engine_id, model_id=model_id, kind='video', prompt=_scene_prompt(sc),
            aspect_ratio=gen_ratio, duration_sec=secs, resolution=resolution, audio=audio,
            first_frame=first_frame, reference_images=refs, desk={'scene_id': sc['id']})
        bad = validate_request(model, req)
        if bad:
            problems.extend(f'{where}: {b}' for b in bad)
            continue
        try:
            est = adapter.estimate(model, req, creds or _Creds(secret=''))
        except Refused as e:
            problems.append(f'{where}: {e}')
            continue
        except EngineError as e:
            raise Refused('estimate_failed', f'could not get a price for {where}, so nothing was sent: {e}', 502, failure=e.kind)
        total += est.usd
        total_amount += _amount(est, engine_id)
        approximate = approximate or est.approximate
        rows.append({'scene': sc, 'req': req, 'est': est, 'requested_sec': sc.get('duration_sec')})
    if problems:
        raise Refused('invalid_input', '; '.join(problems), 400, problems=problems)
    return {'kind': kind, 'owner_id': oid, 'campaign_id': campaign_id, 'project_id': project_id,
            'engine_id': engine_id, 'model_id': model_id, 'aspect_ratio': ratio, 'generated_ratio': gen_ratio,
            'crop_square': crop, 'rows': rows, 'total_usd': round(total, 6), 'total_amount': round(total_amount, 6),
            'currency': eng.currency, 'approximate': approximate,
            'needs_ffmpeg': crop or len(rows) > 1}


def _plan_estimate(plan: dict) -> dict:
    est = {'usd': plan['total_usd'], 'approximate': plan['approximate']}
    if plan['currency'] == 'credits':
        est['credits'] = plan['total_amount']
    return est


def _plan_public(plan: dict) -> dict:
    return {
        'clips': len(plan['rows']), 'total_usd': plan['total_usd'], 'approximate': plan['approximate'],
        **({'total_credits': plan['total_amount'], 'currency': 'credits'} if plan['currency'] == 'credits' else {}),
        'aspect_ratio': plan['aspect_ratio'], 'generated_ratio': plan['generated_ratio'], 'crop': plan['crop_square'],
        'needs_ffmpeg': plan['needs_ffmpeg'],
        'ffmpeg_available': (_ffmpeg() is not None) if plan['needs_ffmpeg'] else None,
        'scenes': [{'scene_id': r['scene']['id'], 'label': r['scene'].get('label') or '',
                    'requested_sec': r['requested_sec'], 'duration_sec': r['req'].duration_sec,
                    'has_picture': bool(r['scene'].get('picture')), 'usd': round(r['est'].usd, 6),
                    **({'credits': r['est'].credits} if r['est'].credits is not None else {})}
                   for r in plan['rows']],
    }


def estimate_render(d: dict, *, unattended: bool = False) -> dict:
    """What a storyboard render would cost and whether it would be refused,
    BEFORE the user presses Render. Free: a vendor estimate call costs nothing,
    so no passcode."""
    plan = _plan_render(d, unattended=unattended)
    out = {'plan': _plan_public(plan), 'estimate': _plan_estimate(plan)}
    return with_caps(out, plan['total_amount'], plan['engine_id'], plan['campaign_id'])


def _public_render(r: dict, jobs: dict) -> dict:
    kids = [{'scene_id': c['scene_id'], 'label': c.get('label', ''), 'job_id': c['job_id'],
             'status': (jobs.get(c['job_id']) or {}).get('status', 'queued'),
             'failure': (jobs.get(c['job_id']) or {}).get('failure')} for c in r.get('children') or []]
    done = sum(1 for k in kids if k['status'] == 'ready')
    return {
        'render_id': r['render_id'], 'kind': 'video', 'status': r['status'], 'hold': r.get('hold'),
        'failure': r.get('failure'), 'owner': r['owner'], 'campaign_id': r.get('campaign_id'),
        'engine_id': r['engine_id'], 'model_id': r['model_id'], 'aspect_ratio': r['aspect_ratio'],
        'progress': {'ready': done, 'total': r.get('clip_count', len(kids))}, 'scenes': kids,
        'outputs': r.get('outputs') or [], 'clips': r.get('clips') or [],
        'cost_usd': round(sum(_num((jobs.get(c['job_id']) or {}).get('cost_usd')) for c in r.get('children') or []), 6),
        **({'cost_credits': round(sum(_num((jobs.get(c['job_id']) or {}).get('cost_credits')) for c in r.get('children') or []), 6),
            'currency': 'credits'} if (r.get('estimate') or {}).get('credits') is not None else {}),
        'estimate': r.get('estimate'), 'created_at': r.get('created_at'), 'updated_at': r.get('updated_at'),
    }


def _render_view(render_id: str) -> dict | None:
    with _lock:
        store = _read_store()
    r = store['renders'].get(render_id)
    return _public_render(r, store['jobs']) if r else None


def get_render(render_id: str) -> dict | None:
    return _render_view(render_id)


def latest_render(owner_kind: str, owner_id: str) -> dict | None:
    """The newest render of one owner's storyboard, so a reloaded page can show
    it again (progress, or the finished video)."""
    with _lock:
        store = _read_store()
    mine = [r for r in store['renders'].values()
            if r['owner'] == {'kind': owner_kind, 'id': owner_id}]
    if not mine:
        return None
    mine.sort(key=lambda r: r.get('created_at') or '')
    return _public_render(mine[-1], store['jobs'])


def render(d: dict, *, unattended: bool = False) -> tuple[dict, bool]:
    """Start a storyboard render -> (render, replay). SPENDS MONEY (one engine
    job per scene): the route gates on a human + the passcode first. Refuses
    before the first clip is sent when the TOTAL is over the engine's per-job
    limit or the campaign's remaining budget."""
    plan = _plan_render(d, unattended=unattended)
    key = d.get('idempotency_key')
    if not isinstance(key, str) or not key.strip() or len(key) > 120:
        raise Refused('invalid_input', 'idempotency_key is required (a retried click must not spend twice)', 400)
    ridem = f"{plan['kind']}:{plan['owner_id']}:{key}"
    with _lock:
        existing = _read_store()['render_idem'].get(ridem)
    if existing:
        return _render_view(existing), True  # type: ignore[return-value]

    campaign_id = plan['campaign_id']
    with _lock:
        store = _read_store()
        if ridem in store['render_idem']:
            return _public_render(store['renders'][store['render_idem'][ridem]], store['jobs']), True
        check_caps(plan['total_amount'], plan['engine_id'], campaign_id, store,
                   estimate=_plan_estimate(plan))
        now = now_iso()
        rec = {
            'render_id': f'rnd-{uuid.uuid4().hex[:10]}', 'status': 'queued', 'hold': None, 'failure': None,
            'owner': {'kind': plan['kind'], 'id': plan['owner_id']}, 'campaign_id': campaign_id,
            'project_id': plan['project_id'], 'engine_id': plan['engine_id'], 'model_id': plan['model_id'],
            'aspect_ratio': plan['aspect_ratio'], 'crop_square': plan['crop_square'],
            'clip_count': len(plan['rows']), 'children': [], 'outputs': [], 'clips': [],
            'estimate': _plan_estimate(plan),
            'created_at': now, 'updated_at': now,
        }
        store['renders'][rec['render_id']] = rec
        store['render_idem'][ridem] = rec['render_id']
        _write_store(store)
    rid = rec['render_id']

    def mutate(fn: Callable[[dict], None]) -> None:
        with _lock:
            s = _read_store()
            fn(s['renders'][rid])
            s['renders'][rid]['updated_at'] = now_iso()
            _write_store(s)

    def unwind() -> None:
        with _lock:
            s = _read_store()
            s['renders'].pop(rid, None)
            s['render_idem'].pop(ridem, None)
            _write_store(s)

    for n, row in enumerate(plan['rows']):
        sid = row['scene']['id']
        body = asdict(row['req'])
        body['desk'] = {'scene_id': sid,
                        'idempotency_key': 'r-' + hashlib.sha1(f'{ridem}|{sid}'.encode()).hexdigest()[:32]}
        if campaign_id:
            body['campaign_id'] = campaign_id
        elif plan['project_id']:
            body['project_id'] = plan['project_id']
        try:
            job, _replay = submit(body, unattended=unattended, _skip_limit=True)
        except (Refused, NotConnected) as e:
            if n == 0:                       # nothing was sent: leave no trace
                unwind()
                raise
            msg = str(e) if isinstance(e, Refused) else e.reason
            mutate(lambda r, n=n, msg=msg: r.update(status='failed', failure={
                'kind': 'partial', 'message': f'scene {n + 1} was not sent ({msg}); the {n} earlier '
                                              f'clip{"s" if n != 1 else ""} already went out and are billed'}))
            break
        mutate(lambda r, j=job, s=row['scene']: r['children'].append(
            {'scene_id': s['id'], 'label': s.get('label') or '', 'job_id': j['job_id']}))
        if job['status'] == 'failed':
            mutate(lambda r, j=job, n=n: r.update(status='failed', failure={
                'kind': 'clip_failed',
                'message': f"scene {n + 1}: {(j.get('failure') or {}).get('message', 'the engine failed')}"}))
            break
    else:
        mutate(lambda r: r.update(status='rendering'))
    # A sync engine (or a vendor that finishes at once) may already be done.
    return poll_render(rid, unattended=unattended), False  # type: ignore[return-value]


def poll_render(render_id: str, *, unattended: bool = False) -> dict | None:
    """Advance one render: poll every clip job once, and when all are ready
    stitch / crop them into the library and attach the result. Free. A render
    that is `held` (ffmpeg missing, or the join failed) tries again here."""
    with _lock:
        store = _read_store()
    r = store['renders'].get(render_id)
    if r is None:
        return None
    if r['status'] in ('ready', 'failed'):
        return _public_render(r, store['jobs'])
    children = r.get('children') or []
    jobs = {}
    for c in children:
        j = poll(c['job_id'], unattended=unattended)
        if j is not None:
            jobs[c['job_id']] = j
    bad = [c for c in children if (jobs.get(c['job_id']) or {}).get('status') == 'failed']
    if bad:
        c = bad[0]
        msg = ((jobs[c['job_id']].get('failure') or {}).get('message')) or 'the engine failed'
        with _lock:
            s = _read_store()
            rr = s['renders'][render_id]
            if rr['status'] not in ('ready', 'failed'):
                rr.update(status='failed', updated_at=now_iso(), failure={
                    'kind': 'clip_failed', 'message': f"scene “{c.get('label') or c['scene_id']}”: {msg}"})
                _write_store(s)
        return _render_view(render_id)
    if len(children) < r.get('clip_count', 0) or any(
            (jobs.get(c['job_id']) or {}).get('status') != 'ready' for c in children):
        with _lock:
            s = _read_store()
            rr = s['renders'][render_id]
            if rr['status'] in ('queued', 'rendering') and children:
                rr['status'] = 'rendering'
                _write_store(s)
        return _render_view(render_id)
    return _finalize_render(render_id, jobs)


def _finalize_render(render_id: str, jobs: dict) -> dict | None:
    with _lock:
        if render_id in _finalizing:
            return _render_view(render_id)
        _finalizing.add(render_id)
    try:
        return _join_clips(render_id, jobs)
    finally:
        with _lock:
            _finalizing.discard(render_id)


def _join_clips(render_id: str, jobs: dict) -> dict | None:
    """All clips are downloaded: join / crop them with ffmpeg, attach the
    result, or hold the render with the reason. Runs outside the store lock."""
    with _lock:
        r = dict(_read_store()['renders'][render_id])
    if r['status'] in ('ready', 'failed'):
        return _render_view(render_id)
    clips = [o for c in r['children'] for o in (jobs[c['job_id']].get('outputs') or [])]
    clip_paths = [Path(o['local_path']) for o in clips]

    def settle(**fields) -> dict | None:
        with _lock:
            s = _read_store()
            s['renders'][render_id].update(fields, updated_at=now_iso())
            _write_store(s)
        return _render_view(render_id)

    def hold(kind: str, message: str) -> dict | None:
        return settle(status='held', hold={'kind': kind, 'message': message}, clips=clips)

    if not clip_paths or any(not p.is_file() for p in clip_paths):
        return hold('clips_missing', 'a downloaded clip is missing from the library, so the render cannot be joined')
    total_sec = sum(_num(o.get('duration_sec')) for o in clips) or None

    if len(clips) == 1 and not r['crop_square']:
        outputs = [dict(clips[0])]
    else:
        ffmpeg = _ffmpeg()
        if ffmpeg is None:
            what = 'cropped to 1:1' if len(clips) == 1 else ('joined' + (' and cropped to 1:1' if r['crop_square'] else ''))
            return hold('ffmpeg_missing',
                        f'ffmpeg is not installed on this machine, so the {len(clips)} clip{"s" if len(clips) != 1 else ""} '
                        f'could not be {what}. They are saved in your Material library (video / {GENERATED_FOLDER}). '
                        f'Install ffmpeg yourself, then open this render again. Nothing is installed for you.')
        out_path = _library_folder('video') / f'{render_id}.mp4'
        list_file = out_path.with_suffix('.concat.txt')
        try:
            list_file.write_text(_concat_list(clip_paths), encoding='utf-8')
            cmd = stitch_command(ffmpeg, str(list_file), str(out_path), crop_square=bool(r['crop_square']),
                                 copy=_codecs_match(clip_paths))
            code, tail = _run_ffmpeg(cmd)
        finally:
            try:
                list_file.unlink()
            except OSError:
                pass
        if code != 0 or not out_path.is_file():
            try:
                out_path.unlink()
            except OSError:
                pass
            _log(f'[desk_engines] ffmpeg join failed for {render_id}: rc={code} {tail}', flush=True)
            return hold('stitch_failed', f'ffmpeg could not join the clips (exit {code}). The clips are saved in your '
                                         f'Material library. {tail.strip()[-300:]}')
        outputs = [_describe_output(out_path, 'video/mp4', duration_sec=total_sec)]
    _attach_to_piece(r['owner']['id'] if r['owner']['kind'] == 'piece' else None, outputs)
    return settle(status='ready', hold=None, failure=None, outputs=outputs, clips=clips)
