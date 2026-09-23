"""Fail loudly when a test writes to LIVE operator state (MC-965).

WHY THIS EXISTS
---------------
`server.py` resolves `_DATA_ROOT` to the repo root, so `DATA_DIR`,
`SCHEDULES_PATH`, `CONFIG_PATH`, the delegation SQLite db and the scheduler's
`.schedule_claims` all point at the operator's REAL files unless a test sets
`MC_DATA_DIR` first. In an agent worktree it is worse than it looks:
`data/projects` and `data/uploads` are SYMLINKS back into the main checkout
(measured 2026-09-22), so a "throwaway worktree" write lands on the live
records every other session is reading.

Per-test monkeypatching cannot prove absence. This guard is process-wide: it
wraps the lowest-level write primitives (`os.open`, `builtins.open`/`io.open`,
`os.mkdir`, `os.remove`/`unlink`, `os.rmdir`, `os.rename`/`replace`,
`sqlite3.connect`) plus `socket.connect` to the live server port, and refuses
any WRITE whose target resolves under a live root. Reads are untouched - many
tests legitimately read `data/skills/builtin` or `data/agent_reference`.

MODES (env `MC_LIVE_STATE_GUARD`)
  enforce  (default) raise + record; the autouse fixture fails the test
  report              record only, never raise (used to enumerate offenders)
  off                 no patching at all

`MC_LIVE_STATE_ALLOW` is a `;`-separated list of absolute paths a run may
write anyway (the MC_LIVE_AUTH_TESTS / MC_LIVE_CLI_TESTS precedent).
"""
from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

_MODE = os.environ.get("MC_LIVE_STATE_GUARD", "enforce").strip().lower()

_REPO_ROOT = Path(__file__).resolve().parent.parent


class LiveStateWriteBlocked(Exception):
    """A test tried to write to the operator's live state."""


VIOLATIONS: list[str] = []
# Opt-in diagnostic: which tests READ live state. A read is not a violation,
# but a test whose result depends on the operator's live files is exactly how
# "3 runs, 3 different failure sets" happens. Enumerate with
# MC_LIVE_STATE_GUARD=report MC_LIVE_STATE_TRACE_READS=1.
READS: list[str] = []
_TRACE_READS = os.environ.get("MC_LIVE_STATE_TRACE_READS") == "1"
_lock = threading.Lock()


def _norm(p) -> str:
    try:
        return os.path.abspath(os.fsdecode(p)).rstrip("\\/").lower()
    except Exception:
        return ""


def _live_prefixes() -> tuple[str, ...]:
    """Every root a test must not write to, plus the real path behind each
    symlink (a worktree's data/projects points at the main checkout)."""
    home = Path.home()
    seeds = [
        _REPO_ROOT / "data",
        _REPO_ROOT / "config.json",
        home / ".clayrune",
    ]
    try:
        seeds.extend((_REPO_ROOT / "data").iterdir())
    except OSError:
        pass
    out: set[str] = set()
    for s in seeds:
        n = _norm(s)
        if n:
            out.add(n)
        try:
            r = _norm(s.resolve())
            if r:
                out.add(r)
        except OSError:
            pass
    return tuple(sorted(out))


_LIVE: tuple[str, ...] = ()
_ALLOW: tuple[str, ...] = ()


def _under(path, roots: tuple[str, ...]) -> bool:
    n = _norm(path)
    if not n:
        return False
    for p in roots:
        if n == p or n.startswith(p + os.sep):
            return True
    return False


def _under_live(path) -> bool:
    if isinstance(path, int):          # already-open fd, nothing to resolve
        return False
    if _ALLOW and _under(path, _ALLOW):
        return False
    return _under(path, _LIVE)


def _record(op: str, path) -> None:
    try:
        target = os.fsdecode(path)
    except Exception:
        target = repr(path)
    msg = (f"{os.environ.get('PYTEST_CURRENT_TEST', '-')} | "
           f"thread {threading.current_thread().name} | {op} -> {target[:200]}")
    with _lock:
        VIOLATIONS.append(msg)


def _record_read(op: str, path) -> None:
    try:
        target = os.fsdecode(path)
    except Exception:
        target = repr(path)
    with _lock:
        READS.append(f"{os.environ.get('PYTEST_CURRENT_TEST', '-')} | {op} -> {target[:200]}")


def _refuse(op: str, path):
    _record(op, path)
    if _MODE != "report":
        raise LiveStateWriteBlocked(
            "a test tried to write LIVE operator state (blocked by "
            f"tests/live_state_guard.py): {op} -> {path}\n"
            "Point the module constant at tmp_path, or use the tmp_data_dir "
            "fixture. MC_LIVE_STATE_GUARD=report enumerates instead of failing.")


_WRITE_FLAGS = 0
for _flag in ("O_WRONLY", "O_RDWR", "O_CREAT", "O_TRUNC", "O_APPEND"):
    _WRITE_FLAGS |= getattr(os, _flag, 0)


def install() -> None:
    """Idempotent. Call once, before any test module is imported."""
    global _LIVE, _ALLOW
    if _MODE == "off" or getattr(install, "_done", False):
        return
    _LIVE = _live_prefixes()
    _ALLOW = tuple(_norm(x) for x in
                   os.environ.get("MC_LIVE_STATE_ALLOW", "").split(";") if x.strip())

    import builtins
    import io
    import socket
    import sqlite3

    real_os_open = os.open

    def guarded_os_open(path, flags, *a, **kw):
        if flags & _WRITE_FLAGS:
            if _under_live(path):
                _refuse("os.open", path)
        elif _TRACE_READS and _under_live(path):
            _record_read("os.open", path)
        return real_os_open(path, flags, *a, **kw)

    real_open = builtins.open

    def guarded_open(file, mode="r", *a, **kw):
        if any(c in mode for c in "wax+"):
            if _under_live(file):
                _refuse(f"open({mode!r})", file)
        elif _TRACE_READS and _under_live(file):
            _record_read(f"open({mode!r})", file)
        return real_open(file, mode, *a, **kw)

    def _wrap_target(mod, name, label):
        real = getattr(mod, name)

        def guarded(path, *a, **kw):
            if _under_live(path):
                _refuse(label, path)
            return real(path, *a, **kw)

        guarded._mc_live_guard = True          # type: ignore[attr-defined]
        setattr(mod, name, guarded)

    def _wrap_move(mod, name, label):
        real = getattr(mod, name)

        def guarded(src, dst, *a, **kw):
            if _under_live(dst):
                _refuse(label, dst)
            elif _under_live(src):
                _refuse(label + " (source)", src)
            return real(src, dst, *a, **kw)

        guarded._mc_live_guard = True          # type: ignore[attr-defined]
        setattr(mod, name, guarded)

    guarded_os_open._mc_live_guard = True      # type: ignore[attr-defined]
    guarded_open._mc_live_guard = True         # type: ignore[attr-defined]
    os.open = guarded_os_open
    builtins.open = guarded_open
    io.open = guarded_open                     # pathlib.Path.open goes through io
    # `os.mkdir` is deliberately NOT wrapped. `import server` runs
    # `mkdir(parents=True, exist_ok=True)` on data/{projects,uploads,memory,
    # media,hiveminds,coordination} at import time, and an empty scaffold dir
    # is not operator state - nothing is read back out of it. Wrapping it only
    # made a fresh clone fail at COLLECTION, before any test ran. Every file
    # that would go INTO those dirs is still caught by open()/os.open, and
    # shutil.rmtree still trips os.unlink/os.rmdir.
    for _name in ("remove", "unlink", "rmdir"):
        _wrap_target(os, _name, "os." + _name)
    for _name in ("rename", "replace"):
        _wrap_move(os, _name, "os." + _name)

    real_sqlite_connect = sqlite3.connect

    def guarded_sqlite(database, *a, **kw):
        # sqlite opens the file in C, so os.open never sees it.
        if str(database) != ":memory:" and _under_live(database):
            _refuse("sqlite3.connect", database)
        return real_sqlite_connect(database, *a, **kw)

    guarded_sqlite._mc_live_guard = True       # type: ignore[attr-defined]
    sqlite3.connect = guarded_sqlite

    # -- the running Clayrune on :5199 is live state too ----------------------
    live_port = int(os.environ.get("MC_LIVE_SERVER_PORT", "5199") or 0)
    # The delegation startup harness registers its own children with the real
    # server on purpose, and only runs when the operator sets this.
    allow_server = bool(os.environ.get("MC_TEST_PROCESS_REGISTER_URL"))
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def _is_live_server(address) -> bool:
        if allow_server or not live_port:
            return False
        try:
            host, port = address[0], int(address[1])
        except (TypeError, IndexError, ValueError, KeyError):
            return False
        return port == live_port and str(host) in (
            "127.0.0.1", "localhost", "::1", "0.0.0.0")

    def guarded_connect(self, address):
        if _is_live_server(address):
            _refuse("socket.connect", str(address))
        return real_connect(self, address)

    def guarded_connect_ex(self, address):
        if _is_live_server(address):
            _refuse("socket.connect_ex", str(address))
        return real_connect_ex(self, address)

    guarded_connect._mc_live_guard = True      # type: ignore[attr-defined]
    guarded_connect_ex._mc_live_guard = True   # type: ignore[attr-defined]
    socket.socket.connect = guarded_connect
    socket.socket.connect_ex = guarded_connect_ex

    install._done = True                       # type: ignore[attr-defined]


def dump(stream=sys.stderr) -> None:
    if VIOLATIONS:
        print("\nLIVE-STATE WRITES ATTEMPTED (%d):" % len(VIOLATIONS), file=stream)
        for v in VIOLATIONS:
            print("  " + v, file=stream)
    report = os.environ.get("MC_LIVE_STATE_REPORT")
    if not report:
        return
    try:
        path = Path(report)
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = ["WRITE\t" + v for v in VIOLATIONS] + ["READ\t" + r for r in READS]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception as exc:                   # best-effort, never break teardown
        print("  (report not written: %s)" % exc, file=stream)
