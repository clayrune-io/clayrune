"""Firestore half of the production AuthorityReader: device and account rows (MC-1166 7d-2r).

READ ONLY. The only client methods touched are `collection().document().get()`;
nothing here writes, updates, deletes or queries. The client is injected as a
zero-argument factory and built on first use, so importing this module or
constructing the reader does no I/O and needs no credentials.

Rows are projected through a FIXED allowlist, server-side (`field_paths`) and
again here: the device row carries the Cloudflare tunnel token and enrollment
token hashes, and none of that may enter a `Snapshot`. The snapshot `version` is
the document's Firestore `update_time` (authoritative, assigned by the backend),
never a client-supplied field.

Failures: a missing device row refuses `device_unavailable`, a missing user row
`authority_refused`; every other failure (timeout, transport, permission,
malformed document) propagates as `AuthorityReadError`, which
`check_authority` turns into `authority_unavailable`. Nothing is swallowed into
success.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Callable

from .firestore import COL_DEVICES, COL_USERS
from .mail_authority_check import GrantRefused, Snapshot

log = logging.getLogger(__name__)

READ_TIMEOUT_S = 8.0
DEVICE_FIELDS = ("device_id", "user_id", "device_pub_b64", "revoked_at", "provisioning_state",
                 "cf_tunnel_uuid", "cf_dns_record_id", "hostname_claim")
ACCOUNT_FIELDS = ("user_id", "suspended")
_DOC_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")


class AuthorityReadError(Exception):
    """A backend read failed or returned something unusable. Carries a constant only."""


def _version(snap: Any) -> str:
    ut = getattr(snap, "update_time", None)
    if ut is None:
        raise AuthorityReadError("firestore_no_update_time")
    rfc = getattr(ut, "rfc3339", None)  # DatetimeWithNanoseconds keeps nanosecond precision
    v = rfc() if callable(rfc) else (ut.isoformat() if hasattr(ut, "isoformat") else None)
    if not isinstance(v, str) or not 1 <= len(v) <= 256:
        raise AuthorityReadError("firestore_bad_update_time")
    return v


class FirestoreAuthorityReads:
    """`device` and `account` of the AuthorityReader protocol.

    `suspended_absent_is_false` (default False, fail closed): users rows are created
    by /v1/enroll WITHOUT a `suspended` key and only gain one when an admin
    suspends or unsuspends. `check_authority` requires an explicit `False`, so with
    the default a never-suspended user is refused `authority_refused`. Setting this
    True maps ONLY an absent key to False; a present non-bool value is left as-is
    and still refuses. Which behaviour production wants is an open decision.
    """

    def __init__(self, client_factory: Callable[[], Any], *, timeout: float = READ_TIMEOUT_S,
                 suspended_absent_is_false: bool = False):
        self._factory = client_factory
        self._client: Any = None
        self._timeout = timeout
        self._absent_ok = suspended_absent_is_false

    def _read_sync(self, collection: str, doc_id: str, fields: tuple[str, ...]):
        if self._client is None:
            self._client = self._factory()
        ref = self._client.collection(collection).document(doc_id)
        return ref.get(field_paths=list(fields), timeout=self._timeout)

    async def _read(self, collection: str, doc_id: str, fields: tuple[str, ...]):
        if not isinstance(doc_id, str) or not _DOC_ID.fullmatch(doc_id):
            raise AuthorityReadError("firestore_bad_id")
        try:
            snap = await asyncio.wait_for(
                asyncio.to_thread(self._read_sync, collection, doc_id, fields), self._timeout + 2)
        except asyncio.TimeoutError:
            log.warning("[mail-authority] firestore %s read timed out", collection)
            raise AuthorityReadError("firestore_timeout") from None
        except Exception as e:
            # Class name only: a provider message can carry resource paths or row fragments.
            log.warning("[mail-authority] firestore %s read failed: %s", collection, type(e).__name__)
            raise AuthorityReadError("firestore_read_failed") from None
        if not getattr(snap, "exists", False):
            return None, None
        row = snap.to_dict()
        if not isinstance(row, dict):
            raise AuthorityReadError("firestore_bad_row")
        return {k: row[k] for k in fields if k in row}, _version(snap)

    async def device(self, device_id: str) -> Snapshot:
        data, version = await self._read(COL_DEVICES, device_id, DEVICE_FIELDS)
        if data is None or version is None:
            raise GrantRefused("device_unavailable")
        return Snapshot(data, version)

    async def account(self, user_id: str) -> Snapshot:
        data, version = await self._read(COL_USERS, user_id, ACCOUNT_FIELDS)
        if data is None or version is None:
            raise GrantRefused("authority_refused")
        if self._absent_ok and "suspended" not in data:
            data["suspended"] = False
        return Snapshot(data, version)
