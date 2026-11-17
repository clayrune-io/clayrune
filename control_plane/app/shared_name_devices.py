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


def other_device_holds_hostname(user_id: str, hostname: str, *, exclude_device_id: str) -> bool:
    """True if a device row other than `exclude_device_id` belongs to `user_id` and
    claims `hostname`. Must be read BEFORE force cleanup, which deletes every
    device row for the hostname."""
    if not (user_id and hostname):
        return False
    try:
        for d in (fs.db().collection(fs.COL_DEVICES)
                    .where("hostname_claim", "==", hostname).stream()):
            if d.id != exclude_device_id and (d.to_dict() or {}).get("user_id") == user_id:
                return True
    except Exception as e:  # revoke is best-effort; a failed read must not stop it
        log.warning("revoke: could not check for other devices on %s: %s", hostname, e)
    return False
