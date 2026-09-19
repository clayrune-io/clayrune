"""Shared fixtures for the Clayrune main-app test suite.

Mirrors the shape of control_plane/tests/conftest.py (env-isolation +
in-memory stubs + a call recorder) but for the Flask app and github_sync.

Scope (deliberately small — IMPROVEMENT_PLAN_V2.md P1-5):
  - `repo_root`        : path to the repo, also put on sys.path
  - `tmp_data_dir`     : isolates MC_DATA_DIR so importing `server` and any
                         filesystem writes land in a throwaway temp dir
  - `fake_gh`          : a programmable, recording stand-in for the `gh` CLI
                         injected into github_sync.subprocess.run
  - `gs`               : the github_sync module, register()-wired to an
                         in-memory project store, with fake_gh active
  - `project_store`    : dict-backed {project_id: project} the gs fixture
                         loads/saves through

These work standalone-ish but pytest is the supported runner.
"""
from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path

import pytest

# ── No test may start the operator's REAL remote tunnel ──────────────────────
# `import mc_remote` (pulled in transitively by `import server`) runs
# _maybe_register(), which spawns a daemon thread calling
# tunnel_supervisor.maybe_start(). On a machine that already has a device
# enrolled, that starts a LIVE attestation loop: it re-attests to the control
# plane every ~5s for the REST OF THE PYTEST SESSION and shells out to
# `tasklist` from a NON-MAIN thread (cloudflared.reap_orphans).
#
# subprocess.run is a process global, so those foreign calls land inside any
# test that has it patched. Measured 2026-09-11: 62 such spawns in one suite
# run, the last of them in the file collected immediately before
# test_spawn_sites.py, which failed intermittently with KeyError('input')
# because calls[0] was the tunnel's tasklist rather than the oneshot() call it
# was asserting the prompt-injection fence on.
#
# MC_REMOTE_ENABLED is mc_remote's own documented kill switch
# (mc_remote/config.py) and must be set BEFORE mc_remote.config is imported —
# conftest is imported before any test module, so here is the only place it
# works. setdefault so an operator can still opt a run back in.
os.environ.setdefault("MC_REMOTE_ENABLED", "0")

# A server restart re-execs with MC_RESTART_FROM_PID set, and anything an older
# server spawned (an agent shell running this suite) may still carry it. The
# port-conflict guard reads it as "wait 15s for my parent to release the port",
# so a stale value turns stranger-holder tests into long waits. Tests that need
# it set it themselves with monkeypatch.
os.environ.pop("MC_RESTART_FROM_PID", None)

_REPO_ROOT = Path(__file__).resolve().parent.parent

# ── No test may launch a REAL model CLI ──────────────────────────────────────
# Precedent: af7e0a3 (pytest spawned a real `claude auth login`). Measured
# 2026-09-18: a daemon `_do_respawn` thread leaked out of a rollover test
# (tests/test_midturn_rollover.py), outlived its monkeypatched subprocess.Popen,
# and launched the real claude.exe with the production flag set
# (--dangerously-skip-permissions) plus a handoff prompt on stdin — 5 of 10
# runs. Patching `Popen.__init__` (not the module attribute) catches every route
# in: subprocess.run/check_output, a Popen reference captured before a test
# patched it, and threads that outlive their test. Tests that install a fake
# `subprocess.Popen` never reach this. An attempt raises AND is recorded,
# because the caller is often a daemon thread whose `except Exception` would
# swallow the raise; the autouse fixture below turns every record into a test
# failure and `pytest_sessionfinish` catches the ones that land after teardown.
# MC_LIVE_CLI_TESTS=1 opts a run back in (the MC_LIVE_AUTH_TESTS precedent).
_REAL_CLI_NAMES = frozenset({"claude", "codex", "gemini", "qwen"})
_REAL_CLI_ATTEMPTS: list[str] = []


class RealCliSpawnBlocked(Exception):
    """A test tried to launch a real model CLI."""


def _cli_argv0(args) -> str:
    if isinstance(args, (str, bytes, os.PathLike)):
        first = os.fsdecode(args).strip().split(None, 1)
        first = first[0] if first else ""
    else:
        try:
            first = os.fsdecode(args[0]) if args else ""
        except (TypeError, IndexError):
            first = ""
    name = os.path.basename(first.strip("\"'")).lower()
    return name.rsplit(".", 1)[0] if "." in name else name


def _is_version_probe(args) -> bool:
    """`<cli> --version` is the provider-detection probe (test_providers_endpoint_ok
    runs it against every installed vendor). It starts no model session."""
    if isinstance(args, (str, bytes, os.PathLike)):
        parts = os.fsdecode(args).split()
    else:
        try:
            parts = [os.fsdecode(a) for a in args]
        except TypeError:
            return False
    return len(parts) == 2 and parts[1] in ("--version", "-v", "-V")


def _install_real_cli_guard() -> None:
    import subprocess
    if getattr(subprocess.Popen.__init__, "_mc_cli_guard", False):
        return
    real_init = subprocess.Popen.__init__

    def guarded_init(self, args, *a, **kw):
        if (_cli_argv0(args) in _REAL_CLI_NAMES and not _is_version_probe(args)
                and os.environ.get("MC_LIVE_CLI_TESTS") != "1"):
            import threading
            msg = (f"{os.environ.get('PYTEST_CURRENT_TEST', '-')} | thread "
                   f"{threading.current_thread().name} | {str(args)[:200]}")
            _REAL_CLI_ATTEMPTS.append(msg)
            raise RealCliSpawnBlocked(
                "a test tried to launch a real model CLI (blocked by "
                f"tests/conftest.py; set MC_LIVE_CLI_TESTS=1 to allow): {msg}")
        return real_init(self, args, *a, **kw)

    guarded_init._mc_cli_guard = True
    subprocess.Popen.__init__ = guarded_init


_install_real_cli_guard()


@pytest.fixture(autouse=True)
def _no_real_cli_spawn():
    """Fail the test that (or whose leaked thread) tried to launch a real CLI."""
    before = len(_REAL_CLI_ATTEMPTS)
    yield
    new = _REAL_CLI_ATTEMPTS[before:]
    if new:
        raise AssertionError("real model CLI launch attempted:\n  " + "\n  ".join(new))


@pytest.fixture(autouse=True)
def _isolated_allowance_state():
    """Importing server.py wires mc.allowance_state to the REAL
    data/allowance_state.json, so a vendor that is genuinely out of quota on
    the box running the suite (Codex, 2026-09-19) made unrelated dispatch
    tests raise "out of allowance". Every test starts with empty, unwired
    allowance state; a test that needs some sets it itself."""
    from mc import allowance_state as _as
    saved = (_as.STATE_PATH, _as._STATE)
    _as.STATE_PATH, _as._STATE = None, {}
    yield
    _as.STATE_PATH, _as._STATE = saved


def pytest_sessionfinish(session, exitstatus):
    # A leaked thread can fire after the last test's teardown; nothing else sees it.
    if _REAL_CLI_ATTEMPTS and session.exitstatus == 0:
        session.exitstatus = 1
        print("\nreal model CLI launch attempted after test teardown:\n  "
              + "\n  ".join(_REAL_CLI_ATTEMPTS))


# Make the app modules importable when running `pytest` from anywhere.
sys.path.insert(0, str(_REPO_ROOT))


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return _REPO_ROOT


@pytest.fixture
def tmp_data_dir(tmp_path, monkeypatch):
    """Point MC_DATA_DIR at a throwaway dir.

    server.py does `DATA_DIR.mkdir(...)` etc. at import time off _DATA_ROOT,
    which is `os.environ['MC_DATA_DIR']` when set. Setting this BEFORE server
    is imported keeps the real ./data tree untouched.
    """
    d = tmp_path / "mc_data"
    d.mkdir()
    monkeypatch.setenv("MC_DATA_DIR", str(d))
    # Avoid colliding with a running instance's port if anything reads it.
    monkeypatch.setenv("MC_PORT", "0")
    return d


# ─── Fake gh CLI ─────────────────────────────────────────────────────────────


class _CompletedProcess:
    """Minimal stand-in for subprocess.CompletedProcess."""

    def __init__(self, returncode: int, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class FakeGh:
    """Programmable, recording replacement for `gh` via subprocess.run.

    Usage in a test::

        fake_gh.on(["issue", "list"], stdout=json.dumps([...]))
        fake_gh.on(["issue", "create"], stdout="https://github.com/o/r/issues/7")
        ...
        assert fake_gh.count(["issue", "close"]) == 0

    Matching: a handler registered with prefix tokens matches if those tokens
    appear in order anywhere in the gh argv (after the leading 'gh'). Last
    registered matching handler wins, so tests can override defaults.
    """

    def __init__(self):
        self.calls: list[list[str]] = []          # full argv incl. 'gh'
        self._handlers: list[tuple[list[str], object]] = []

    # -- configuration -------------------------------------------------------

    def on(self, match: list[str], *, returncode: int = 0,
           stdout: str = "", stderr: str = "", callback=None):
        """Register a response for argv containing `match` tokens in order.

        `callback(argv) -> (returncode, stdout, stderr)` takes precedence and
        lets a test vary the response per call (e.g. unique issue numbers).
        """
        self._handlers.append((list(match), callback or (returncode, stdout, stderr)))
        return self

    # -- query ---------------------------------------------------------------

    def _argv_matches(self, match: list[str], argv: list[str]) -> bool:
        i = 0
        for tok in argv:
            if i < len(match) and tok == match[i]:
                i += 1
        return i == len(match)

    def count(self, match: list[str]) -> int:
        return sum(1 for c in self.calls if self._argv_matches(match, c[1:]))

    def calls_matching(self, match: list[str]) -> list[list[str]]:
        return [c for c in self.calls if self._argv_matches(match, c[1:])]

    # -- the subprocess.run shim --------------------------------------------

    def run(self, cmd, **kwargs):
        argv = list(cmd)
        self.calls.append(argv)
        gh_args = argv[1:] if argv and argv[0] == "gh" else argv
        # Last matching handler wins.
        for match, resp in reversed(self._handlers):
            if self._argv_matches(match, gh_args):
                if callable(resp):
                    rc, out, err = resp(argv)
                else:
                    rc, out, err = resp
                return _CompletedProcess(rc, out, err)
        # Unconfigured gh call → empty success (mirrors `gh` with no output).
        return _CompletedProcess(0, "", "")


@pytest.fixture
def fake_gh(monkeypatch):
    from mc import github_sync
    fg = FakeGh()
    monkeypatch.setattr(github_sync.subprocess, "run", fg.run)
    return fg


@pytest.fixture
def project_store() -> dict:
    return {}


@pytest.fixture
def gs(fake_gh, project_store, monkeypatch):
    """github_sync, register()-wired to the in-memory project_store.

    Rate limit + per-project lock state are module globals; clear them so
    tests don't interfere with each other.
    """
    from mc import github_sync
    importlib.reload(github_sync)
    # fake_gh patched the pre-reload module; re-patch the fresh one.
    fg = fake_gh
    monkeypatch.setattr(github_sync.subprocess, "run", fg.run)

    activity_log: list[tuple[str, str]] = []

    def _log_activity(pid, msg):
        activity_log.append((pid, msg))

    def _load_project(pid):
        p = project_store.get(pid)
        return json.loads(json.dumps(p)) if p is not None else None

    def _save_project(pid, project):
        project_store[pid] = json.loads(json.dumps(project))

    _now = ["2026-05-17T00:00:00Z"]

    def _now_iso():
        return _now[0]

    github_sync.register(
        popen_flags=0,
        startupinfo=None,
        log_activity=_log_activity,
        load_project=_load_project,
        save_project=_save_project,
        now_iso=_now_iso,
    )
    github_sync._activity_log = activity_log   # test introspection
    github_sync._set_now = lambda s: _now.__setitem__(0, s)
    return github_sync
