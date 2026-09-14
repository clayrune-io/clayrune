"""tools/night-review/send_mail.py must refuse to send while running under
pytest, unless explicitly opted in with MC_LIVE_MAIL_TESTS=1.

On 2026-09-11 tests/test_tunnel_watchdog.py sent four real "Remote access
DOWN" emails to Ron before those specific tests were patched. The ledger
already recorded PYTEST_CURRENT_TEST for every call but nothing stopped the
send itself. mc/workflows.py already gates its own two mail call sites
(_notify_approval_waiting, _send_operator_notification) on the same
PYTEST_CURRENT_TEST / MC_LIVE_MAIL_TESTS pair; this closes the mailer itself,
which is the one place every caller funnels through.

Every test here runs the real script as a subprocess with PYTEST_CURRENT_TEST
forced on and MC_LIVE_MAIL_TESTS forced off, so a bug in the guard fails
loud (a real SMTP attempt would hang/err on missing creds or, worse, send
real mail) rather than silently passing. Nothing here ever sets
MC_LIVE_MAIL_TESTS -- this suite must never actually contact SMTP.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent.parent / "tools" / "night-review" / "send_mail.py"


def _run(*, extra_env: dict | None = None, args: list[str] | None = None) -> subprocess.CompletedProcess:
    import os
    env = dict(os.environ)
    env["PYTEST_CURRENT_TEST"] = "tests/test_send_mail_pytest_guard.py::fake"
    env.pop("MC_LIVE_MAIL_TESTS", None)
    if extra_env:
        env.update(extra_env)
    cmd = [sys.executable, str(_SCRIPT), "--subject", "should never send",
           "--body", "refused-before-smtp"]
    if args:
        cmd += args
    return subprocess.run(cmd, capture_output=True, text=True, timeout=30, env=env)


def test_refuses_under_pytest_without_opt_in():
    r = _run()
    assert r.returncode == 5, r.stderr
    assert "refused" in r.stderr.lower()
    assert "sent '" not in r.stdout, "must not print the real-send confirmation line"


def test_refusal_happens_before_any_credential_or_body_resolution():
    """The guard fires even with no --body/--body-file/stdin and no creds
    configured -- proof it runs before those checks, i.e. before anything
    that could reach smtplib."""
    import os
    env = dict(os.environ)
    env["PYTEST_CURRENT_TEST"] = "tests/test_send_mail_pytest_guard.py::fake"
    env.pop("MC_LIVE_MAIL_TESTS", None)
    r = subprocess.run(
        [sys.executable, str(_SCRIPT), "--subject", "no body given at all"],
        capture_output=True, text=True, timeout=30, env=env,
    )
    assert r.returncode == 5, r.stderr
    assert "no body" not in r.stderr.lower(), \
        "body-resolution error means the guard did NOT fire first"


def test_ledger_line_is_marked_refused(tmp_path):
    """The sender ledger (~/.clayrune/sent_mail.log) still records the
    attempt -- just tagged 'refused' instead of 'attempted' -- so an audit of
    the ledger can tell a blocked test apart from a real send."""
    import os
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    env = dict(os.environ)
    env["PYTEST_CURRENT_TEST"] = "tests/test_send_mail_pytest_guard.py::fake"
    env.pop("MC_LIVE_MAIL_TESTS", None)
    env["USERPROFILE"] = str(fake_home)  # Path.home() on Windows
    env["HOME"] = str(fake_home)          # Path.home() on POSIX
    r = subprocess.run(
        [sys.executable, str(_SCRIPT), "--subject", "ledger check", "--body", "x"],
        capture_output=True, text=True, timeout=30, env=env,
    )
    assert r.returncode == 5, r.stderr
    ledger = fake_home / ".clayrune" / "sent_mail.log"
    assert ledger.exists()
    line = ledger.read_text(encoding="utf-8").strip().splitlines()[-1]
    assert line.endswith("refused"), line


def test_opting_in_with_MC_LIVE_MAIL_TESTS_bypasses_the_guard_but_still_needs_creds():
    """MC_LIVE_MAIL_TESTS=1 is the documented escape hatch -- prove it changes
    the code path (falls through to the credentials check, exit 2) WITHOUT
    ever supplying real credentials, so this test still cannot send mail."""
    import os
    fake_home_env = {"MC_LIVE_MAIL_TESTS": "1"}
    env = dict(os.environ)
    env.update(fake_home_env)
    env["PYTEST_CURRENT_TEST"] = "tests/test_send_mail_pytest_guard.py::fake"
    # Blank out any real creds this box has configured so a guard bug can
    # never reach smtplib for real even on the opt-in path.
    env.pop("NIGHT_MAIL_USER", None)
    env.pop("NIGHT_MAIL_APP_PASSWORD", None)
    env["USERPROFILE"] = env["HOME"] = str(Path(__file__).resolve().parent)  # no night-mail.json here
    r = subprocess.run(
        [sys.executable, str(_SCRIPT), "--subject", "opt-in path", "--body", "x"],
        capture_output=True, text=True, timeout=30, env=env,
    )
    assert r.returncode == 2, r.stderr  # "credentials not configured", not the pytest refusal
    assert "refused" not in r.stderr.lower()
