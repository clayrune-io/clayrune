"""MC-1179 MB33: a failed suspension lookup must leave a log line.

`verify._is_user_suspended` answered "not suspended" on any exception reading
the user row and logged nothing, so a Firestore fault that let a suspended
account through the verify chain could not be seen afterwards. The answer is
unchanged (the attest route re-reads the row and checks entitlement); the
failure is now observable.
"""
from __future__ import annotations

import logging

from control_plane.app import firestore as cp_fs, verify


def test_a_failed_lookup_is_logged_and_still_answers_not_suspended(monkeypatch, caplog):
    def boom():
        raise RuntimeError("firestore down\nforged line")
    monkeypatch.setattr(cp_fs, "db", boom)

    with caplog.at_level(logging.WARNING, logger=verify.log.name):
        assert verify._is_user_suspended("user-1") is False

    lines = [r.getMessage() for r in caplog.records if r.name == verify.log.name]
    assert len(lines) == 1, lines
    assert "suspension lookup failed" in lines[0]
    assert "RuntimeError" in lines[0]
    assert "user-1" in lines[0]
    assert "\n" not in lines[0]  # exception text is scrubbed, not interpolated raw


def test_a_readable_row_logs_nothing(mem_firestore, caplog):
    mem_firestore.collection(cp_fs.COL_USERS).document("u-ok").set({"suspended": False})
    mem_firestore.collection(cp_fs.COL_USERS).document("u-bad").set({"suspended": True})
    with caplog.at_level(logging.WARNING, logger=verify.log.name):
        assert verify._is_user_suspended("u-ok") is False
        assert verify._is_user_suspended("u-bad") is True
        assert verify._is_user_suspended("u-missing") is False
    assert [r for r in caplog.records if r.name == verify.log.name] == []
