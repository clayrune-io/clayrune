"""Production ``reviewed_service`` callable for MailFactory (MC-1166 thin-slice unit U1).

``MailFactory.reviewed_service()`` refuses (``service_key_changed``) until it is given a
``Callable[[], tuple[ServiceKeyCandidate, str]]``. ``build(factory)`` makes that callable. Each
call that is not served from cache:

1. reads ``agent_mail_service_origin`` / ``agent_mail_root_public_key`` from the factory's config,
2. GETs the manifest with the EXISTING fetcher (``fetch_verified_manifest``: https only, no
   redirect, bounded, root-signature verified),
3. re-verifies it with ``verify_service_manifest`` against ``ManifestCAS`` over the factory's
   checked pull store, which advances the persistent rollback floor (the only authority),
4. returns ``(candidate, manifest_digest)`` where ``manifest_digest`` is the same
   ``sha256(canonical_manifest_bytes(payload))`` that ``ManifestState.digest`` stores and that
   ``Signup`` writes to ``mail_pull_generations.manifest_digest`` (``agent_mail_enrollment_manifest
   .manifest_digest``), so the callable's digest compares equal to the stored row.

The verified pair is cached in memory until ``min(candidate.expires_at_ms, now + 10 min)`` so the
per-call authority checks (every send, ticket and ack) do not each cost an HTTPS round trip. A
cache entry is never used past the manifest's own expiry, never survives a config change, and a
failed refresh never falls back to it or to any account key. Failure is always an
``EnrollmentRefused``/``ManifestRefused`` with a closed code; remote text is never kept.

Precondition (not created here): the store must already hold ``mail_enrollment_manifests``. This
module never writes schema; without the table every refresh fails closed.
"""
from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any, Callable

from mc.agent_mail_enrollment_keys import EnrollmentRefused
from mc.agent_mail_enrollment_manifest import manifest_digest
from mc.agent_mail_enrollment_state import ManifestCAS
from mc.agent_mail_manifest_fetch import ManifestFetchError, fetch_verified_manifest
from mc.agent_mail_service_manifest import ManifestRefused, ServiceKeyCandidate, verify_service_manifest
from mc.core import _log

if TYPE_CHECKING:
    from mc.agent_mail_factory import MailFactory

# Same config keys as mc.agent_mail_factory (ORIGIN_KEY / ROOT_KEY); a test pins them equal. Not
# imported so the factory can import this module without a cycle.
ORIGIN_KEY, ROOT_KEY = 'agent_mail_service_origin', 'agent_mail_root_public_key'
CACHE_MS = 10 * 60 * 1000

_UNAVAILABLE = 'service_unavailable'


def build(factory: 'MailFactory', *, transport: Any = None) -> Callable[[], tuple[ServiceKeyCandidate, str]]:
    """Return the callable to pass as ``MailFactory(reviewed_service=...)``.

    ``transport`` is a GET transport (``.get(url) -> {'status','body'}``); None = the real HTTPS
    one. Nothing is read, fetched or written until the callable is invoked.
    """
    lock = threading.Lock()
    cache: list[tuple[str, str, ServiceKeyCandidate, str, int]] = []  # (origin, root, pin, digest, until_ms)

    def now_ms() -> int:
        return int(factory.clock() * 1000)

    def reviewed() -> tuple[ServiceKeyCandidate, str]:
        origin, root = factory.config.get(ORIGIN_KEY), factory.config.get(ROOT_KEY)
        if not (isinstance(origin, str) and origin and isinstance(root, str) and root):
            cache.clear()
            raise EnrollmentRefused(_UNAVAILABLE)
        with lock:
            now = now_ms()
            if cache and cache[0][:2] == (origin, root) and now < cache[0][4]:
                return cache[0][2], cache[0][3]
            cache.clear()  # an expired or mismatched entry is never a fallback
            try:
                store = factory.require_pull_store()
                cas = ManifestCAS(store)
                fetched = fetch_verified_manifest(origin=origin, root_public_key=root, floor=cas.load,
                                                  transport=transport, now_ms=now_ms)
                payload, signature = fetched['manifest'], fetched['signature']
                candidate = verify_service_manifest(payload, signature, root_public_key=root,
                                                    service_origin=origin, store=cas, now_ms=now_ms(),
                                                    enabled=True)
                digest = manifest_digest(payload)
            except (ManifestRefused, EnrollmentRefused):
                raise
            except ManifestFetchError as exc:
                _log(f'[agent-mail] reviewed_service fetch refused: {exc.code}', flush=True)
                raise EnrollmentRefused(_UNAVAILABLE) from None
            except Exception as exc:  # store held, floor table missing, sqlite error: all closed
                _log(f'[agent-mail] reviewed_service failed: {type(exc).__name__}', flush=True)
                raise EnrollmentRefused(_UNAVAILABLE) from None
            cache.append((origin, root, candidate, digest, min(candidate.expires_at_ms, now + CACHE_MS)))
            return candidate, digest

    return reviewed
