"""The live-state guard must block every route a test could write live state by.

MC-965. A guard that covers `open()` but not `os.open()`, or `Path.write_text`
but not `sqlite3.connect`, is worse than none: it reads as proof and is not.
Each test here is one real write route used by this codebase.

The probe paths are never created - `enforce` mode raises before the syscall,
which is exactly what is being asserted.
"""
from __future__ import annotations

import os
import socket
import sqlite3
from pathlib import Path

import pytest

import live_state_guard as guard

# The blocking assertions only hold in `enforce` mode; the isolation
# assertions at the bottom hold in every mode.
enforcing = pytest.mark.skipif(
    guard._MODE != "enforce",
    reason="guard is in report/off mode; nothing raises")

LIVE_DATA = Path(guard._REPO_ROOT) / "data"


@pytest.fixture(autouse=True)
def _drop_expected_violations():
    """Swallow the violations these tests provoke on purpose.

    Inner autouse fixture, so its teardown runs BEFORE conftest's
    `_no_live_state_writes`, which would otherwise fail every test here.
    """
    before = len(guard.VIOLATIONS)
    yield
    del guard.VIOLATIONS[before:]


@enforcing
def test_path_write_text_is_blocked():
    with pytest.raises(guard.LiveStateWriteBlocked):
        (LIVE_DATA / "mc965_probe.txt").write_text("x", encoding="utf-8")


@enforcing
def test_builtin_open_for_write_is_blocked():
    with pytest.raises(guard.LiveStateWriteBlocked):
        open(LIVE_DATA / "mc965_probe.txt", "w").close()


@enforcing
def test_os_open_with_create_is_blocked():
    with pytest.raises(guard.LiveStateWriteBlocked):
        os.open(str(LIVE_DATA / "mc965_probe.txt"), os.O_CREAT | os.O_WRONLY)


@enforcing
def test_the_symlinked_projects_dir_is_blocked():
    """In an agent worktree data/projects is a SYMLINK into the main checkout -
    the write looks local and is not."""
    with pytest.raises(guard.LiveStateWriteBlocked):
        (LIVE_DATA / "projects" / "mc965_probe.json").write_text("{}", encoding="utf-8")


@enforcing
def test_write_json_atomic_is_blocked():
    """The app's own state-write helper (tmp file + os.replace)."""
    from mc.atomic_json import write_json_atomic
    with pytest.raises(guard.LiveStateWriteBlocked):
        write_json_atomic(LIVE_DATA / "mc965_probe.json", {})


@enforcing
def test_sqlite_connect_is_blocked():
    """sqlite opens the file in C, so os.open never sees it."""
    with pytest.raises(guard.LiveStateWriteBlocked):
        sqlite3.connect(LIVE_DATA / "delegation_delivery.sqlite3")


@enforcing
def test_clayrune_home_is_blocked():
    with pytest.raises(guard.LiveStateWriteBlocked):
        (Path.home() / ".clayrune" / "mc965_probe.txt").write_text("x", encoding="utf-8")


@enforcing
def test_unlink_of_live_state_is_blocked():
    with pytest.raises(guard.LiveStateWriteBlocked):
        os.unlink(LIVE_DATA / "settings.json")


@enforcing
def test_connecting_to_the_running_server_is_blocked():
    sock = socket.socket()
    try:
        with pytest.raises(guard.LiveStateWriteBlocked):
            sock.connect(("127.0.0.1", 5199))
    finally:
        sock.close()


@enforcing
def test_reads_and_tmp_writes_still_work(tmp_path):
    """The guard must not turn into a blanket filesystem ban."""
    settings = LIVE_DATA / "settings.json"
    if settings.exists():
        assert settings.read_text(encoding="utf-8") is not None
    (tmp_path / "ok.txt").write_text("fine", encoding="utf-8")
    assert (tmp_path / "ok.txt").read_text(encoding="utf-8") == "fine"


@enforcing
def test_no_probe_file_was_ever_created():
    for name in ("mc965_probe.txt", "mc965_probe.json"):
        assert not (LIVE_DATA / name).exists()
    assert not (LIVE_DATA / "projects" / "mc965_probe.json").exists()
    assert not (Path.home() / ".clayrune" / "mc965_probe.txt").exists()


# ── the session must be bound to a throwaway root, not the operator's ────────
# These hold in every guard mode: they assert the conftest ISOLATION, not the
# guard's blocking. A regression here is silent - the suite still passes, it
# just starts reading whatever the running Clayrune wrote that hour.

def test_the_session_data_root_is_not_the_repo():
    import server
    repo = Path(guard._REPO_ROOT).resolve()
    assert Path(server._DATA_ROOT).resolve() != repo
    assert Path(server.DATA_DIR).resolve() != (repo / "data" / "projects").resolve()
    assert Path(server.CONFIG_PATH).resolve() != (repo / "config.json").resolve()


def test_the_scheduler_claims_dir_is_not_the_live_one():
    from mc.blueprints import scheduler_routes as sr
    repo = Path(guard._REPO_ROOT).resolve()
    assert Path(sr.SCHEDULES_PATH).resolve() != (repo / "data" / "schedules.json").resolve()
    assert Path(sr._claims_dir()).resolve() != (repo / "data" / ".schedule_claims").resolve()


def test_clayrune_home_is_not_the_operators():
    from mc.secrets_store import clayrune_home
    assert clayrune_home().resolve() != (Path.home() / ".clayrune").resolve()
