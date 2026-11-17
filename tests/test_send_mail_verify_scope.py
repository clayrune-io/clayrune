"""MC-1179 TL18: --verify only runs where it can mean something.

Everything is stubbed: smtplib.SMTP is a fake, _verify_delivered is a fake,
HOME points at a temp dir (sender ledger, kill-switch file). Nothing contacts
a mail server. MC_LIVE_MAIL_TESTS is set only inside the test so the pytest
guard lets main() run to the stubbed transport.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "tools" / "night-review" / "send_mail.py"
_spec = importlib.util.spec_from_file_location("send_mail_verify_scope", _SCRIPT)
send_mail = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(send_mail)


def test_can_verify_only_gmail_to_self():
    assert send_mail.can_verify("smtp.gmail.com", "a@x.com", "A@x.com")
    assert not send_mail.can_verify("smtp.gmail.com", "a@x.com", "b@x.com")
    assert not send_mail.can_verify("smtp.example.org", "a@x.com", "a@x.com")


class _FakeSMTP:
    def __init__(self, *a, **k): pass
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def ehlo(self): pass
    def starttls(self, context=None): pass
    def login(self, u, p): pass
    def send_message(self, m): pass


def _run_main(monkeypatch, tmp_path, cfg, extra):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("MC_LIVE_MAIL_TESTS", "1")
    monkeypatch.setattr(send_mail, "_load_config", lambda: cfg)
    monkeypatch.setattr(send_mail, "_smtp_connect", lambda h, p: _FakeSMTP())
    calls = []
    monkeypatch.setattr(send_mail, "_verify_delivered", lambda *a, **k: calls.append(a) or False)
    monkeypatch.setattr("sys.argv", ["send_mail.py", "--smtp-only", "--verify",
                                     "--subject", "s", "--body", "b"] + extra)
    return send_mail.main(), calls


def test_verify_is_skipped_for_another_host_and_the_send_stays_a_success(monkeypatch, tmp_path, capsys):
    rc, calls = _run_main(monkeypatch, tmp_path,
                          {"user": "a@x.com", "app_password": "p", "host": "smtp.example.org"}, [])
    assert rc == 0 and calls == []
    assert "verify skipped" in capsys.readouterr().err


def test_verify_is_skipped_when_sending_to_someone_else(monkeypatch, tmp_path):
    rc, calls = _run_main(monkeypatch, tmp_path,
                          {"user": "a@x.com", "app_password": "p"}, ["--to", "b@x.com"])
    assert rc == 0 and calls == []


def test_verify_still_runs_for_gmail_self_send(monkeypatch, tmp_path):
    rc, calls = _run_main(monkeypatch, tmp_path,
                          {"user": "a@x.com", "app_password": "p", "to": "a@x.com"}, [])
    assert rc == 3 and len(calls) == 1
