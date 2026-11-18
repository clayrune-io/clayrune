"""Does another device of the same user hold the same hostname?

PROPRIETARY AND CONFIDENTIAL.
Copyright (c) 2026 Clayrune. All rights reserved.

`_claim_username` lets a user re-claim a name they already hold, so a reinstall or
a second machine can enrol on it. When one of those devices is revoked, releasing
the username claim (and, with MB22, blanking `users/{uid}.username` and ending
every session) would leave the user's other device "not enrolled".
"""
from __future__ import annotations

import logging

from . import firestore as fs

log = logging.getLogger(__name__)


def other_devices_holding_hostname(
    user_id: str, hostname: str, *, exclude_device_id: str,
) -> list[dict] | None:
    """Device rows other than `exclude_device_id` that belong to `user_id` and claim
    `hostname`, each carrying its Firestore id as `device_id`. Must be read BEFORE
    force cleanup, which deletes every device row for the hostname. These are the
    survivors whose tunnel, DNS record and row a revoke must leave alone (MC-1210).

    Returns None when the read FAILED (rows collected before a mid-stream failure
    are discarded): "could not tell" is not "there are none", and the caller must
    not sweep the hostname or release the username on it."""
    if not (user_id and hostname):
        return []
    rows: list[dict] = []
    try:
        for d in (fs.db().collection(fs.COL_DEVICES)
                    .where("hostname_claim", "==", hostname).stream()):
            row = d.to_dict() or {}
            if d.id != exclude_device_id and row.get("user_id") == user_id:
                rows.append({**row, "device_id": d.id})
    except Exception as e:
        log.warning("revoke: could not check for other devices on %s: %s", hostname, e)
        return None
    return rows


def other_device_holds_hostname(user_id: str, hostname: str, *, exclude_device_id: str) -> bool:
    """True if a device row other than `exclude_device_id` belongs to `user_id` and
    claims `hostname`. Must be read BEFORE force cleanup, which deletes every
    device row for the hostname. A failed read answers False, as it always has;
    nothing calls this now (revoke uses `other_devices_holding_hostname` and
    branches on None), so a new caller that needs fail-closed must do the same."""
    return bool(other_devices_holding_hostname(
        user_id, hostname, exclude_device_id=exclude_device_id))
