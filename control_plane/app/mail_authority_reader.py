"""Production AuthorityReader: Firestore (device, account) + Cloudflare (tunnel, ingress, DNS).

MC-1166 unit 7d-2r. One reader, shared by the 7a enrollment-grant issuer
(`MailGrantIssuer(reader=...)`) and the delivery issuer
(`MailDeliveryAuthority(reader=...)`); both call `check_authority`, which is
unchanged. READS ONLY: no write, update, delete or query reaches either backend.

DEFAULT OFF and fail closed. `authority_reader_from_env()` returns None unless
`CLAYRUNE_MAIL_AUTHORITY_READ_ENABLED` is `1`/`true` AND every name below is set;
None makes issue/lease answer 503 `authority_unavailable`. A backend failure
at read time does the same. No credential is created or defaulted here: config
names env vars only and the deployment injects their values from the vault:

  CLAYRUNE_MAIL_AUTHORITY_READ_ENABLED  flag
  CLAYRUNE_MAIL_AUTHORITY_CF_TOKEN      Cloudflare API token, injected from the vault.
                                        Deliberately NOT `CLOUDFLARE_API_TOKEN`
                                        (that one can write); no fallback to it.
  CLOUDFLARE_ACCOUNT_ID, CLOUDFLARE_ZONE_ID   pinned, existing names, no discovery
  FIRESTORE_PROJECT / FIRESTORE_DATABASE / FIRESTORE_EMULATOR_HOST   as `app.firestore`
"""
from __future__ import annotations

import hashlib
import logging
import os
from typing import Any, Callable, Mapping, Optional

from .mail_authority_check import AuthorityReader, Snapshot
from .mail_authority_cloudflare import CfGet, CloudflareAuthorityReads
from .mail_authority_firestore import FirestoreAuthorityReads

log = logging.getLogger(__name__)

ENV_ENABLED = "CLAYRUNE_MAIL_AUTHORITY_READ_ENABLED"
ENV_CF_TOKEN = "CLAYRUNE_MAIL_AUTHORITY_CF_TOKEN"
ENV_ACCOUNT = "CLOUDFLARE_ACCOUNT_ID"
ENV_ZONE = "CLOUDFLARE_ZONE_ID"
CF_TIMEOUT_S = 8.0

_cache: dict[tuple, "ProductionAuthorityReader"] = {}


class ProductionAuthorityReader:
    """Satisfies `AuthorityReader` by delegation; holds no state of its own."""

    def __init__(self, firestore: FirestoreAuthorityReads, cloudflare: CloudflareAuthorityReads):
        self._fs, self._cf = firestore, cloudflare

    async def device(self, device_id: str) -> Snapshot: return await self._fs.device(device_id)
    async def account(self, user_id: str) -> Snapshot: return await self._fs.account(user_id)
    async def tunnel(self, tunnel_id: str) -> Snapshot: return await self._cf.tunnel(tunnel_id)
    async def ingress(self, tunnel_id: str) -> Snapshot: return await self._cf.ingress(tunnel_id)
    async def dns(self, record_id: str) -> Snapshot: return await self._cf.dns(record_id)


def _flag(env: Mapping[str, str], name: str) -> bool:
    return env.get(name, "").strip().lower() in ("1", "true")


def _default_firestore_client() -> Any:
    from . import firestore  # the existing lazy singleton; builds the real client on first read
    return firestore.db()


def _default_cf_get(token: str) -> CfGet:
    """GET-only view of the existing Cloudflare client. Built lazily inside the running loop."""
    holder: dict[str, Any] = {}

    async def get(path: str) -> Any:
        if "client" not in holder:
            from .cloudflare import CloudflareClient
            holder["client"] = CloudflareClient(token=token, timeout=CF_TIMEOUT_S)
        return await holder["client"]._call("GET", path)

    return get


def authority_reader_from_env(
        env: Optional[Mapping[str, str]] = None, *,
        firestore_client_factory: Optional[Callable[[], Any]] = None,
        cf_get: Optional[CfGet] = None,
        suspended_absent_is_false: bool = False) -> Optional[AuthorityReader]:
    """The configured production reader, or None (refuse closed). Does no I/O."""
    env = os.environ if env is None else env
    if not _flag(env, ENV_ENABLED):
        return None
    token = env.get(ENV_CF_TOKEN, "").strip()
    account, zone = env.get(ENV_ACCOUNT, "").strip(), env.get(ENV_ZONE, "").strip()
    needed = ((ENV_ACCOUNT, account), (ENV_ZONE, zone)) + (() if cf_get else ((ENV_CF_TOKEN, token),))
    missing = [n for n, v in needed if not v]  # injected `cf_get` (tests) stands in for the token
    if missing:
        log.error("[mail-authority] reader enabled but not configured: missing %s", ", ".join(missing))
        return None
    key = (account, zone, hashlib.sha256(token.encode()).hexdigest(), suspended_absent_is_false,
           firestore_client_factory, cf_get)
    cached = _cache.get(key)
    if cached is not None:
        return cached
    try:
        reader = ProductionAuthorityReader(
            FirestoreAuthorityReads(firestore_client_factory or _default_firestore_client,
                                    suspended_absent_is_false=suspended_absent_is_false),
            CloudflareAuthorityReads(cf_get or _default_cf_get(token), account_id=account, zone_id=zone))
    except ValueError:
        log.error("[mail-authority] reader enabled but %s/%s are malformed", ENV_ACCOUNT, ENV_ZONE)
        return None
    _cache.clear()  # one live config at a time; a changed config replaces, never accumulates
    _cache[key] = reader
    return reader
