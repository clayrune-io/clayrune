"""Mail delivery authority routes (MC-1166 7d-2b, contract section 2).

  GET  /v1/mail-delivery/jwks    public delivery-capability keys (NOT /v1/jwks)
  POST /v1/mail-delivery/bind    create the CP binding from an enrollment grant
  POST /v1/mail-delivery/issue   mint a 60 s per-request delivery capability
  POST /v1/mail-delivery/lease   authority read for the ownership-lease renewal
  POST /v1/mail-delivery/revoke  revoke a binding generation (idempotent)

DEFAULT OFF: unless `CLAYRUNE_MAIL_DELIVERY_ENABLED` is `1`/`true`, every route
here answers 404 and reads nothing. The POST routes authenticate with the mail
Worker's Ed25519 service signature (`mail_service_auth`), never a dashboard
credential. There is no default authority reader: until a reader is wired in a
later unit, issue/lease answer 503 `authority_unavailable` (closed, not open).
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from . import sessions
from .mail_delivery_authority import DeliveryRefused, MailDeliveryAuthority
from .mail_delivery_signing import DeliveryKeyError, DeliveryKeyring, parse_delivery_keys
from .mail_delivery_store import DeliveryStore
from .mail_service_auth import (RefusalBucket, ServiceAuthRefused, ServiceKeyring,
                                parse_service_keys, verify_service_call)
from .routes_account import _request_id

log = logging.getLogger(__name__)

MAX_BODY_BYTES = 16 * 1024
PATH_PREFIX = "/v1/mail-delivery"

_STATUS = {
    "bad_request": 400, "bad_transcript": 400, "bad_grant": 400, "scope_mismatch": 400,
    "invalid_json": 400,
    "binding_required": 403, "service_key_mismatch": 403, "key_mismatch": 403,
    "authority_refused": 403, "device_unavailable": 403, "device_key_unavailable": 403,
    "device_key_mismatch": 403, "provisioning_mismatch": 403, "cloudflare_mapping_mismatch": 403,
    "binding_conflict": 409, "revoked_generation": 409, "nonce_reused": 409,
    "issue_budget": 429,
    "authority_unavailable": 503, "authority_version_required": 503, "authority_check_expired": 503,
}
_BUCKET = RefusalBucket()
_STORE = DeliveryStore()


@dataclass
class DeliveryDeps:
    enabled: bool
    keyring: Optional[DeliveryKeyring] = None
    service_keys: Optional[ServiceKeyring] = None
    authority: Optional[MailDeliveryAuthority] = None
    store: Any = None
    bucket: Any = None


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true")


def get_deps() -> DeliveryDeps:
    """Default dependency: reviewed config from the environment, read per request.

    Tests replace this with `app.dependency_overrides[get_deps]`. Missing or
    malformed config leaves the matching piece None; the routes then refuse with
    503 `not_configured` rather than guessing.
    """
    deps = DeliveryDeps(enabled=_flag("CLAYRUNE_MAIL_DELIVERY_ENABLED"), store=_STORE, bucket=_BUCKET)
    if not deps.enabled:
        return deps
    try:
        deps.keyring = parse_delivery_keys(os.environ.get("CLAYRUNE_MAIL_DELIVERY_KEYS"))
    except DeliveryKeyError:
        log.error("[mail-delivery] CLAYRUNE_MAIL_DELIVERY_KEYS missing or invalid")
    try:
        deps.service_keys = parse_service_keys(os.environ.get("CLAYRUNE_MAIL_SERVICE_KEYS"))
    except ServiceAuthRefused:
        log.error("[mail-delivery] CLAYRUNE_MAIL_SERVICE_KEYS missing or invalid")
    try:
        grant_jwks = json.loads(os.environ.get("CLAYRUNE_MAIL_GRANT_JWKS", "") or "{}").get("keys", [])
    except (ValueError, AttributeError):
        grant_jwks = []
    if deps.keyring and deps.service_keys:
        # No authority reader exists yet (contract file list has none): reader=None makes
        # issue/lease refuse `authority_unavailable`. bind/revoke work without it.
        deps.authority = MailDeliveryAuthority(
            reader=None, keyring=deps.keyring, store=_STORE, issuer=sessions.issuer(),
            service_keys=deps.service_keys, grant_jwks=grant_jwks if isinstance(grant_jwks, list) else [])
    return deps


def require_enabled(deps: DeliveryDeps = Depends(get_deps)) -> DeliveryDeps:
    if not deps.enabled:
        raise HTTPException(status_code=404, detail="Not Found")
    return deps


router = APIRouter(dependencies=[Depends(require_enabled)])


def _err(request: Request, code: str, status: Optional[int] = None) -> JSONResponse:
    return JSONResponse(status_code=status or _STATUS.get(code, 400),
                        content={"code": code, "message": code, "request_id": _request_id(request)})


@router.get("/jwks", tags=["mail-delivery"])
async def jwks(request: Request, deps: DeliveryDeps = Depends(require_enabled)):
    if deps.keyring is None:
        return _err(request, "not_configured", 503)
    return JSONResponse(content=deps.keyring.jwks(), headers={"Cache-Control": "public, max-age=600"})


async def _service_post(request: Request, deps: DeliveryDeps, name: str):
    """Shared POST front door. Returns (ServiceCall, dict body) or an error response."""
    path = f"{PATH_PREFIX}/{name}"
    if request.url.query:
        return _err(request, "bad_request")
    if request.headers.get("content-type", "").strip().lower() != "application/json":
        return _err(request, "bad_request")
    try:
        declared = int(request.headers.get("content-length", ""))
    except ValueError:
        declared = -1
    if not 0 <= declared <= MAX_BODY_BYTES:
        return _err(request, "bad_request", 413 if declared > MAX_BODY_BYTES else 400)
    body = await request.body()
    if len(body) != declared:
        return _err(request, "bad_request")
    if deps.authority is None or deps.service_keys is None:
        return _err(request, "not_configured", 503)
    try:
        call = verify_service_call(request.headers, body, method=request.method, path=path,
                                   keyring=deps.service_keys, claim_nonce=deps.store.claim_service_nonce)
    except ServiceAuthRefused as e:
        log.info("[mail-delivery] %s refused: %s", name, e.code)
        # Refusals share one bounded bucket; a valid signature is never charged to it.
        return _err(request, "unauthorized", 401) if deps.bucket.allow() else _err(request, "abuse_limit", 429)
    except Exception:
        # A nonce-store failure is an operational failure, never an auth result or an accept.
        log.error("[mail-delivery] %s storage_or_configuration_failure (service auth)", name)
        return _err(request, "storage_or_configuration_failure", 503)
    try:
        value = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return _err(request, "invalid_json")
    if not isinstance(value, dict):
        return _err(request, "invalid_json")
    return call, value


async def _run(request: Request, deps: DeliveryDeps, name: str):
    front = await _service_post(request, deps, name)
    if isinstance(front, JSONResponse):
        return front
    call, value = front
    try:
        handler = getattr(deps.authority, name)
        result = handler(call, value)
        if hasattr(result, "__await__"):
            result = await result
    except DeliveryRefused as e:
        log.info("[mail-delivery] %s refused: %s", name, e.code)
        return _err(request, e.code)
    except Exception:
        # Storage/signing/config failures stay observable without leaking a row, key or body.
        log.error("[mail-delivery] %s storage_or_configuration_failure", name)
        return _err(request, "storage_or_configuration_failure", 503)
    return JSONResponse(content=result)


@router.post("/bind", tags=["mail-delivery"])
async def bind(request: Request, deps: DeliveryDeps = Depends(require_enabled)):
    return await _run(request, deps, "bind")


@router.post("/issue", tags=["mail-delivery"])
async def issue(request: Request, deps: DeliveryDeps = Depends(require_enabled)):
    return await _run(request, deps, "issue")


@router.post("/lease", tags=["mail-delivery"])
async def lease(request: Request, deps: DeliveryDeps = Depends(require_enabled)):
    return await _run(request, deps, "lease")


@router.post("/revoke", tags=["mail-delivery"])
async def revoke(request: Request, deps: DeliveryDeps = Depends(require_enabled)):
    return await _run(request, deps, "revoke")
