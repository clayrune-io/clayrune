"""Who a username belongs to, and what releasing one takes with it.

PROPRIETARY AND CONFIDENTIAL.
Copyright (c) 2026 Clayrune. All rights reserved.

`usernames/{name}` is the claim; `users/{uid}.username` is a copy of it that the
session JWT's `u` claim is minted from. The edge admits a request when `u` equals
the subdomain, so the copy must never outlive the claim (MB22): device revoke used
to delete the claim and leave the copy and the user's sessions, and the old owner
kept getting `u` = the name after another account claimed it.
"""
from __future__ import annotations

import logging

from . import firestore as fs, sessions

log = logging.getLogger(__name__)


def holds_claim(user_id: str, username: str) -> bool:
    """True only if `usernames/{username}` exists and names `user_id`."""
    if not user_id or not username:
        return False
    snap = fs.db().collection("usernames").document(username).get()
    return bool(snap.exists and (snap.to_dict() or {}).get("user_id") == user_id)


def forget_released_username(user_id: str, username: str) -> None:
    """The claim on `username` was just released by `user_id`: clear it from their
    user row and end their sessions, so nothing keeps minting `u` = `username`.

    Only clears the row when it still names `username`, so a user who has since
    moved to another name is left alone.
    """
    uref = fs.db().collection(fs.COL_USERS).document(user_id)
    snap = uref.get()
    if snap.exists and (snap.to_dict() or {}).get("username") == username:
        uref.set({"username": ""}, merge=True)
    sessions.revoke_all(user_id)
