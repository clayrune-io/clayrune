"""Mail enrollment ceremony routes (MC-1166 7d-2e-1, contract section 1.2).

  GET  /v1/mail-enrollment/jwks       public enrollment-grant keys (NOT /v1/jwks, NOT /v1/mail-delivery/jwks)
  POST /v1/mail-enrollment/grant      mint a 60 s enrollment grant (MailGrantIssuer.issue)
  POST /v1/mail-enrollment/preflight  Worker asks: is this a real, active device on a live tunnel? (E2a)

DEFAULT OFF: unless `CLAYRUNE_MAIL_ENROLLMENT_ENABLED` is `1`/`true`, every route here
answers 404 and reads nothing.

AUTH SURFACE. `/grant` has no session, cookie, bearer or `MC-Device` header: the caller
proves possession of the device private key and the mail private key by signing the
canonical transcript `R`, which `MailGrantIssuer` verifies and then forces the device key
to equal the CP device row's key. That is NOT proof of the human or the mailbox: that is
the Worker's code confirmation and provider check. `/preflight` authenticates with the mail
Worker's Ed25519 service signature (`mail_service_auth`), never a dashboard credential.

The per-device grant budget is charged only once the caller has shown the device key the CP
holds for that `device_id` (see `_ChargedReader`), so a stranger who signs a fabricated `R`
with their own keys cannot spend a real device's budget.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from . import sessions
from .mail_authority_check import GrantRefused, Snapshot, check_authority
from .mail_authority_reader import authority_reader_from_env
from .mail_enrollment_store import EnrollmentStore
from .mail_enrollment_transcript import FIELDS, canonical_r, origin, unb64u
from .mail_grant_keyring import GrantKeyError, MailGrantKeyring, parse_grant_keys
from .mail_grants import MailGrantIssuer
from .mail_service_auth import (RefusalBucket, ServiceAuthRefused, ServiceKeyring,
                                parse_service_keys, verify_service_call)
from .routes_account import _request_id

log = logging.getLogger(__name__)

MAX_BODY_BYTES = 8 * 1024
PATH_PREFIX = "/v1/mail-enrollment"
EXPECTED_SERVICE = "http://localhost:5199"
GRANT_BUDGET_PER_HOUR = 20

_STATUS = {
    "invalid_json": 400, "bad_request": 400, "bad_transcript": 400,
    "key_proof_required": 403, "authority_refused": 403, "device_unavailable": 403,
    "device_key_unavailable": 403, "device_key_mismatch": 403, "provisioning_mismatch": 403,
    "cloudflare_mapping_mismatch": 403,
    "challenge_unavailable": 409,
    "grant_budget": 429, "abuse_limit": 429,
    "authority_unavailable": 503, "authority_version_required": 503, "authority_check_expired": 503,
    "mail_grant_disabled": 503, "not_configured": 503, "storage_or_configuration_failure": 503,
}
_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
_UUID = re.compile(r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}")
_BUCKET = RefusalBucket()
_STORE = EnrollmentStore()


@dataclass
class EnrollmentDeps:
    enabled: bool
    keyring: Optional[MailGrantKeyring] = None
    service_keys: Optional[ServiceKeyring] = None
    reader: Any = None
    issuer: str = ""
    service_origin: str = ""
    store: Any = None
    bucket: Any = None
    grant_budget: int = GRANT_BUDGET_PER_HOUR
    clock: Callable[[], int] = field(default=lambda: int(time.time() * 1000))


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true")


def get_deps() -> EnrollmentDeps:
    """Default dependency: reviewed config from the environment, read per request.

    Tests replace this with `app.dependency_overrides[get_deps]`. With the flag off this
    reads nothing else. Missing or malformed config leaves the matching piece unset and the
    routes refuse with 503 rather than guessing.
    """
    deps = EnrollmentDeps(enabled=_flag("CLAYRUNE_MAIL_ENROLLMENT_ENABLED"), store=_STORE, bucket=_BUCKET)
    if not deps.enabled:
        return deps
    try:
        deps.keyring = parse_grant_keys(os.environ.get("CLAYRUNE_MAIL_GRANT_KEYS"))
    except GrantKeyError:
        log.error("[mail-enrollment] CLAYRUNE_MAIL_GRANT_KEYS missing or invalid")
    try:
        deps.service_keys = parse_service_keys(os.environ.get("CLAYRUNE_MAIL_SERVICE_KEYS"))
    except ServiceAuthRefused:
        log.error("[mail-enrollment] CLAYRUNE_MAIL_SERVICE_KEYS missing or invalid")
    deps.service_origin = os.environ.get("CLAYRUNE_MAIL_SERVICE_ORIGIN", "").strip()
    deps.issuer = sessions.issuer()
    # Same reader as the delivery routes: reuses CLAYRUNE_MAIL_AUTHORITY_READ_ENABLED. None (flag
    # off or config incomplete) makes /grant and /preflight refuse `authority_unavailable`.
    deps.reader = authority_reader_from_env(suspended_absent_is_false=True)
    return deps


def require_enabled(deps: EnrollmentDeps = Depends(get_deps)) -> EnrollmentDeps:
    if not deps.enabled:
        raise HTTPException(status_code=404, detail="Not Found")
    return deps


router = APIRouter(dependencies=[Depends(require_enabled)])


def _err(request: Request, code: str, status: Optional[int] = None) -> JSONResponse:
    return JSONResponse(status_code=status or _STATUS.get(code, 400),
                        content={"code": code, "message": code, "request_id": _request_id(request)})


def _refuse(request: Request, deps: EnrollmentDeps, code: str, *, charge: bool = True) -> JSONResponse:
    """A refusal from a caller who has not (yet) proven the device key. These share one bounded
    bucket; once it is spent the answer is `abuse_limit`. Authenticated refusals pass charge=False."""
    if charge and not deps.bucket.allow():
        return _err(request, "abuse_limit")
    return _err(request, code)


@router.get("/jwks", tags=["mail-enrollment"])
async def jwks(request: Request, deps: EnrollmentDeps = Depends(require_enabled)):
    if deps.keyring is None:
        return _err(request, "not_configured")
    return JSONResponse(content=deps.keyring.jwks(), headers={"Cache-Control": "public, max-age=600"})


async def _json_body(request: Request, deps: EnrollmentDeps) -> "bytes | JSONResponse":
    """Shape checks shared by both POST routes. Returns the raw body or an error response."""
    if request.url.query or request.headers.get("content-type", "").strip().lower() != "application/json":
        return _refuse(request, deps, "bad_request")
    try:
        declared = int(request.headers.get("content-length", ""))
    except ValueError:
        declared = -1
    if not 0 <= declared <= MAX_BODY_BYTES:
        if not deps.bucket.allow():
            return _err(request, "abuse_limit")
        return _err(request, "bad_request", 413 if declared > MAX_BODY_BYTES else 400)
    body = await request.body()
    if len(body) != declared:
        return _refuse(request, deps, "bad_request")
    return body


def _load(body: bytes, keys: set[str]) -> Optional[dict]:
    try:
        value = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    return value if isinstance(value, dict) and set(value) == keys else None


class _ChargedReader:
    """Wraps the authority reader for one /grant call.

    `MailGrantIssuer.issue` verifies both signatures and then calls `check_authority`, whose
    first read is the device row. The signatures only prove the caller holds the keys written
    *into* R, so a stranger can name a real `device_id` and pass them. This wrapper therefore
    charges the device budget only after that first read shows the row's key equals the key in
    R (possession of the real device key). A key mismatch is left to `check_authority` to
    refuse as `device_key_mismatch` and spends nothing.
    """

    def __init__(self, reader, *, device_key: bytes, charge: Callable[[], None]):
        self._reader, self._device_key, self._charge = reader, device_key, charge
        self.possessed = False

    async def device(self, device_id: str) -> Snapshot:
        snap = await self._reader.device(device_id)
        if not self.possessed and self._holds_key(snap):
            self.possessed = True
            self._charge()
        return snap

    def _holds_key(self, snap) -> bool:
        import base64
        try:
            return base64.b64decode(snap.data.get("device_pub_b64", ""), validate=True) == self._device_key
        except (AttributeError, ValueError, TypeError):
            return False

    async def account(self, user_id): return await self._reader.account(user_id)
    async def tunnel(self, tunnel_id): return await self._reader.tunnel(tunnel_id)
    async def ingress(self, tunnel_id): return await self._reader.ingress(tunnel_id)
    async def dns(self, record_id): return await self._reader.dns(record_id)


@router.post("/grant", tags=["mail-enrollment"])
async def grant(request: Request, deps: EnrollmentDeps = Depends(require_enabled)):
    body = await _json_body(request, deps)
    if isinstance(body, JSONResponse):
        return body
    if deps.keyring is None or not origin(deps.service_origin) or not origin(deps.issuer):
        return _err(request, "not_configured")
    if deps.reader is None:
        return _err(request, "authority_unavailable")
    value = _load(body, {"r", "device_signature", "mail_signature"})
    if value is None:
        return _refuse(request, deps, "invalid_json")
    r, dsig, msig = value["r"], value["device_signature"], value["mail_signature"]
    if not isinstance(dsig, str) or not isinstance(msig, str):
        return _refuse(request, deps, "bad_request")
    try:
        canonical_r(r)
        device_key = unb64u(dict(zip(FIELDS, r))["device_public_key"], 32)
        device_id = dict(zip(FIELDS, r))["device_id"]
    except ValueError:
        return _refuse(request, deps, "bad_transcript")

    def charge() -> None:
        try:
            counted = deps.store.consume_grant_budget(device_id, deps.clock(), limit=deps.grant_budget)
        except Exception:
            log.error("[mail-enrollment] grant budget storage failure")
            raise GrantRefused("storage_or_configuration_failure") from None
        if not counted:
            raise GrantRefused("grant_budget")

    reader = _ChargedReader(deps.reader, device_key=device_key, charge=charge)
    try:
        issuer = MailGrantIssuer(reader=reader, key=deps.keyring.signing_key(), issuer=deps.issuer,
                                 service_origin=deps.service_origin, enabled=True, clock=deps.clock,
                                 expected_service=EXPECTED_SERVICE)
        token = await issuer.issue(r, device_signature=dsig, mail_signature=msig)
    except GrantRefused as e:
        code = str(e) if str(e) in _STATUS else "storage_or_configuration_failure"
        log.info("[mail-enrollment] grant refused: %s", code)
        return _refuse(request, deps, code, charge=not reader.possessed)
    except ValueError:
        # canonical_r's ValueError("bad_transcript") is not a GrantRefused (`mail_grants.py` raises it
        # before any authority read); origins were checked above, so nothing else raises ValueError.
        return _refuse(request, deps, "bad_transcript", charge=not reader.possessed)
    except Exception:
        # Storage/signing/config failures stay observable without leaking a row, key or body.
        log.error("[mail-enrollment] grant storage_or_configuration_failure")
        return _err(request, "storage_or_configuration_failure")
    return JSONResponse(content={"grant": token})


@router.post("/preflight", tags=["mail-enrollment"])
async def preflight(request: Request, deps: EnrollmentDeps = Depends(require_enabled)):
    """Worker service-to-cp: is device_id + device_public_key + tunnel_uuid + route_origin a real,
    active, non-suspended device on a live tunnel? Same cross-check the grant makes
    (`check_authority`), without minting anything. Reply is `{"ok": true}` or a closed code."""
    path = f"{PATH_PREFIX}/preflight"
    body = await _json_body(request, deps)
    if isinstance(body, JSONResponse):
        return body
    if deps.service_keys is None:
        return _err(request, "not_configured")
    try:
        verify_service_call(request.headers, body, method=request.method, path=path,
                            keyring=deps.service_keys, claim_nonce=deps.store.claim_service_nonce)
    except ServiceAuthRefused as e:
        log.info("[mail-enrollment] preflight refused: %s", e.code)
        # Refusals share one bounded bucket; a valid signature is never charged to it.
        return _err(request, "abuse_limit") if not deps.bucket.allow() else _err(request, "unauthorized", 401)
    except Exception:
        log.error("[mail-enrollment] preflight storage_or_configuration_failure (service auth)")
        return _err(request, "storage_or_configuration_failure")
    if deps.reader is None:
        return _err(request, "authority_unavailable")
    value = _load(body, {"device_id", "device_public_key", "tunnel_uuid", "route_origin"})
    if value is None:
        return _err(request, "invalid_json")
    try:
        if (not isinstance(value["device_id"], str) or not _ID.fullmatch(value["device_id"])
                or not isinstance(value["tunnel_uuid"], str) or not _UUID.fullmatch(value["tunnel_uuid"])
                or not origin(value["route_origin"])):
            raise ValueError("bad_request")
        device_key = unb64u(value["device_public_key"], 32)
    except ValueError:
        return _err(request, "bad_request")
    try:
        await check_authority(deps.reader, device_id=value["device_id"], device_public_key=device_key,
                              tunnel_uuid=value["tunnel_uuid"], route_origin=value["route_origin"],
                              expected_service=EXPECTED_SERVICE)
    except GrantRefused as e:
        code = str(e) if str(e) in _STATUS else "storage_or_configuration_failure"
        log.info("[mail-enrollment] preflight refused: %s", code)
        return _err(request, code)
    except Exception:
        log.error("[mail-enrollment] preflight storage_or_configuration_failure")
        return _err(request, "storage_or_configuration_failure")
    return JSONResponse(content={"ok": True})
