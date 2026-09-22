"""send_mail._smtp_connect retries a transient name-resolution failure
(2026-09-22: [Errno 11002] getaddrinfo failed lost a nightly email) and
nothing else. Never contacts SMTP: smtplib.SMTP is patched out."""
from __future__ import annotations

import importlib.util
import socket
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "tools" / "night-review" / "send_mail.py"
_spec = importlib.util.spec_from_file_location("send_mail_under_test", _SCRIPT)
send_mail = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(send_mail)


def _fake_smtp(outcomes):
    calls = []

    def ctor(host, port, timeout):
        calls.append((host, port))
        o = outcomes.pop(0)
        if isinstance(o, BaseException):
            raise o
        return o
    return ctor, calls


def test_retries_gaierror_then_connects(monkeypatch):
    sentinel = object()
    ctor, calls = _fake_smtp([socket.gaierror(11002, "getaddrinfo failed"),
                              socket.gaierror(11002, "getaddrinfo failed"),
                              sentinel])
    monkeypatch.setattr(send_mail.smtplib, "SMTP", ctor)
    slept = []
    got = send_mail._smtp_connect("h", 587, waits=(1, 2, 3), sleep=slept.append)
    assert got is sentinel
    assert slept == [1, 2]
    assert len(calls) == 3


def test_gives_up_after_last_wait(monkeypatch):
    ctor, calls = _fake_smtp([socket.gaierror(11002, "x")] * 3)
    monkeypatch.setattr(send_mail.smtplib, "SMTP", ctor)
    slept = []
    with pytest.raises(socket.gaierror):
        send_mail._smtp_connect("h", 587, waits=(1, 2), sleep=slept.append)
    assert slept == [1, 2]
    assert len(calls) == 3


def test_other_errors_are_not_retried(monkeypatch):
    ctor, calls = _fake_smtp([ConnectionRefusedError("no")])
    monkeypatch.setattr(send_mail.smtplib, "SMTP", ctor)
    slept = []
    with pytest.raises(ConnectionRefusedError):
        send_mail._smtp_connect("h", 587, waits=(1,), sleep=slept.append)
    assert slept == [] and len(calls) == 1
