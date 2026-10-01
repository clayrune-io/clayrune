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
  * **The only render cap is the campaign's `how.budget`.** A job whose
    estimate exceeds what is left of it is refused (409, nothing sent). There
    is deliberately no per-job limit (Dave, 2026-10-01). A campaign with no
    budget set has nothing left to spend, so it refuses too.
  * **Idempotent on `desk.idempotency_key`.** A retried click returns the first
    job and makes no second vendor call and no second reservation.
  * **Poll, never webhook** (scan rule 1). Output is downloaded on `ready` into
    `data/uploads/desk/generated/<campaign>/` (scan rule 2; Veo deletes after
    48 h). `GET job` is what advances a job; nothing here runs on a timer.
  * **Vendor HTTP is one function**, `_http_request`. Tests replace it; no test
    in the repo reaches a vendor.

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
import json
import mimetypes
import re
import socket
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


# `kind` of the engine's auth + where its credential lives. Two-part entries
# (Higgsfield) read the username half through `secrets_store.get_username`.
_ENGINES: list[EngineDescriptor] = [
    EngineDescriptor(
        id='higgsfield', label='Higgsfield',
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
        ]),
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
        ]),
    EngineDescriptor(
        id='openai', label='OpenAI image',
        auth={'kind': 'api_key', 'vault_entry': VAULT_OPENAI},
        job_model='sync', output_ttl_hours=0, estimate='price_table', prices_read='2026-09-30',
        models=[
            _openai_image_model('gpt-image-2.5-sunburst', 'GPT Image 2.5 (sunburst)'),
            _openai_image_model('gpt-image-2.5-flare', 'GPT Image 2.5 (flare)'),
        ]),
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
    """`{ready, reason, vault_entry}` for one engine. Metadata only: the vault
    is asked whether the entry exists and decrypts, never for its value."""
    eng = ENGINES[engine_id]
    name = eng.auth['vault_entry']
    try:
        visible = {s['name']: s for s in secrets_store.list_secrets(project_id)}
        rec = visible.get(name)
        if rec is None:
            return {'ready': False, 'vault_entry': name,
                    'reason': f"no vault entry named '{name}' (add it in Secrets)"}
        if not secrets_store.is_readable(name):
            return {'ready': False, 'vault_entry': name,
                    'reason': f"vault entry '{name}' cannot be read (vault locked or key mismatch)"}
        if eng.auth['kind'] == 'key_id_secret' and not rec.get('username'):
            return {'ready': False, 'vault_entry': name,
                    'reason': f"vault entry '{name}' has no username; store the key id as its username"}
    except secrets_store.SecretsError as e:
        return {'ready': False, 'vault_entry': name, 'reason': _safe(e)}
    return {'ready': True, 'vault_entry': name, 'reason': None}


def _creds(engine_id: str, project_id: str | None, unattended: bool) -> _Creds:
    eng = ENGINES[engine_id]
    name = eng.auth['vault_entry']
    status = connection(engine_id, project_id)
    if not status['ready']:
        raise NotConnected(engine_id, status['reason'], name)
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

    def public(self) -> dict:
        return asdict(self)


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
    'veo': VeoAdapter(),
    'gemini_image': GeminiImageAdapter(),
    'openai_image': OpenAIImageAdapter(),
}


def _adapter(model: ModelDescriptor):
    return ADAPTERS[model.vendor['adapter']]


# -- job store ----------------------------------------------------------------

def _empty_store() -> dict:
    return {'version': STORE_VERSION, 'jobs': {}, 'idem': {}}


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
    data.setdefault('idem', {})
    return data


def _write_store(store: dict) -> None:
    if JOBS_PATH is None:
        raise RuntimeError('desk_engines.JOBS_PATH is not wired; refusing to spend without a durable job record')
    Path(JOBS_PATH).parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(Path(JOBS_PATH), json.dumps(store, indent=2, ensure_ascii=False))


def _public_job(job: dict) -> dict:
    """The Job shape of the contract. `engine_ref` stays server-side."""
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
    """Every engine descriptor with its connected status. Metadata only."""
    out = []
    for e in _ENGINES:
        d = e.public()
        d['connected'] = connection(e.id, project_id)
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
    out = {'estimate': est.public()}
    if campaign_id:
        b = budget_state(campaign_id)
        out['budget'] = b
        out['fits'] = est.usd <= b['remaining'] + 1e-9
    return out


def _new_job(req: GenerationRequest, camp: dict, est: Estimate) -> dict:
    now = now_iso()
    return {
        'job_id': f'gen-{uuid.uuid4().hex[:10]}', 'engine_id': req.engine_id, 'model_id': req.model_id,
        'kind': req.kind, 'status': 'queued', 'failure': None, 'outputs': [],
        'cost_usd': round(est.usd, 6), 'estimate': est.public(),
        'campaign_id': camp['id'], 'project_id': camp.get('project_id'),
        'desk': dict(req.desk), 'request': asdict(req), 'engine_ref': None,
        'created_at': now, 'updated_at': now,
    }


def _fail(job: dict, kind: str, message: str, *, free: bool) -> None:
    job['status'] = 'failed'
    job['failure'] = {'kind': kind, 'message': message}
    job['updated_at'] = now_iso()
    if free:
        job['cost_usd'] = 0.0


def submit(d: dict, *, unattended: bool = False) -> tuple[dict, bool]:
    """Submit one render job -> (job, replay). SPENDS MONEY: the route gates
    on a human + the passcode before calling this. Raises `Refused`,
    `NotConnected` (nothing sent) or returns the job (queued/rendering/ready)."""
    req = parse_request(d)
    key = req.desk.get('idempotency_key')
    if not isinstance(key, str) or not key.strip() or len(key) > 120:
        raise Refused('invalid_input', 'desk.idempotency_key is required (a retried click must not spend twice)', 400)
    campaign_id = d.get('campaign_id')
    if not isinstance(campaign_id, str) or not campaign_id:
        raise Refused('invalid_input', 'campaign_id is required: the campaign budget is the only cap on a render', 400)
    camp = _campaign(campaign_id)
    if camp is None:
        raise Refused('campaign_not_found', 'campaign not found', 404)
    if camp.get('state') in ('done', 'dropped'):
        raise Refused('campaign_closed', f"campaign is {camp.get('state')}; it cannot render", 409)
    _eng, model = _resolve(req)
    idem = f'{campaign_id}:{key}'

    with _lock:
        existing = _read_store()['idem'].get(idem)
        if existing:
            return _public_job(_read_store()['jobs'][existing]), True

    creds = _creds(req.engine_id, camp.get('project_id'), unattended)
    adapter = _adapter(model)
    try:
        est = adapter.estimate(model, req, creds)
    except EngineError as e:
        raise Refused('estimate_failed', f'could not get a price, so nothing was sent: {e}', 502, failure=e.kind)

    with _lock:
        store = _read_store()
        if idem in store['idem']:
            return _public_job(store['jobs'][store['idem'][idem]]), True
        b = budget_state(campaign_id, _store=store)
        if est.usd > b['remaining'] + 1e-9:
            raise Refused('over_budget',
                          f"estimate ${est.usd:.4f} is over the campaign's remaining budget "
                          f"${b['remaining']:.4f} (amount ${b['amount']:.2f}, spent ${b['spent']:.4f})",
                          409, estimate=est.public(), budget=b)
        job = _new_job(req, camp, est)
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


def _save_outputs(job: dict, req: GenerationRequest, blobs: list) -> list[dict]:
    """Write each output under `data/uploads/desk/generated/<campaign>/` and
    describe it. The library path is the one the Desk materials list and
    /api/serve-image share."""
    if UPLOADS_ROOT is None:
        raise EngineError('engine', 'the uploads directory is not wired; cannot keep the output')
    folder = Path(UPLOADS_ROOT) / 'desk' / 'generated' / re.sub(r'[^A-Za-z0-9_-]', '_', job.get('campaign_id') or 'misc')
    folder.mkdir(parents=True, exist_ok=True)
    out = []
    for n, (data, mime) in enumerate(blobs):
        mime = mime if mime in _EXT else ('video/mp4' if req.kind == 'video' else 'image/png')
        path = folder / f"{job['job_id']}-{n}{_EXT.get(mime) or mimetypes.guess_extension(mime) or ''}"
        path.write_bytes(data)
        w, h = _png_jpeg_size(data)
        out.append({'local_path': str(path), 'mime': mime, 'width': w, 'height': h,
                    'duration_sec': req.duration_sec if req.kind == 'video' else None})
    return out
