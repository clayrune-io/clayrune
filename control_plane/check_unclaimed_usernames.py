#!/usr/bin/env python3
"""Pre-deploy check for MB22: count user rows that carry a username but hold no claim.

PROPRIETARY AND CONFIDENTIAL.
Copyright (c) 2026 Clayrune. All rights reserved.

Sign-in and session refresh now mint only while `usernames/{u}.user_id` is the
user. A user row with a username and no matching claim document can no longer sign
in until it re-enrols. Enrolment is the only writer of both, so the expected count
is 0; run this before deploying that change.

READ-ONLY: it reads `users` and `usernames` and writes nothing. It prints counts
only — no user id, email or username.

    python -m control_plane.check_unclaimed_usernames

Env as for the other operator scripts: GOOGLE_APPLICATION_CREDENTIALS,
FIRESTORE_PROJECT, FIRESTORE_DATABASE. Exit code: 0 when the count is 0, 1 when
it is not.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def count_unclaimed(db) -> dict:
    """{"with_username": n, "unclaimed": n}. `unclaimed` = a username whose
    `usernames/{name}` doc is missing or names a different user."""
    with_username = unclaimed = 0
    for u in db.collection("users").stream():
        row = u.to_dict() or {}
        name = row.get("username")
        if not name:
            continue
        with_username += 1
        snap = db.collection("usernames").document(name).get()
        owner = (snap.to_dict() or {}).get("user_id") if snap.exists else None
        if owner != (row.get("user_id") or u.id):
            unclaimed += 1
    return {"with_username": with_username, "unclaimed": unclaimed}


def main() -> int:
    from control_plane.app import firestore as fs

    r = count_unclaimed(fs.db())
    print(f"users with a username: {r['with_username']}")
    print(f"without a matching claim: {r['unclaimed']}")
    return 0 if r["unclaimed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
