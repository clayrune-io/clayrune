"""Agent-mail first for send_mail.py (tools/night-review/agent_mail_route.py).

The HTTP layer is a fake everywhere: no test here contacts a server, Gmail or the
network. The send_mail.main() tests replace SMTP with an in-memory transport.
"""
from __future__ import annotations

import importlib.util
import re
import threading
from pathlib import Path

import pytest

_MAIL_DIR = Path(__file__).resolve().parent.parent / "tools" / "night-review"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _MAIL_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


send_mail = _load("send_mail")  # puts tools/night-review on sys.path
import agent_mail_route as route  # noqa: E402

ENV = {"CLAYRUNE_PROJECT_ID": "proj1", "CLAYRUNE_SESSION_ID": "sess1"}
ACCEPTED = (200, {"intent_id": "abc123", "state": "accepted", "reason": "",
                  "message_id": "m@x", "transport_state": "accepted"})


class Fake:
    def __init__(self, result=ACCEPTED, exc=None):
        self.result, self.exc, self.calls = result, exc, []

    def __call__(self, url, payload, timeout):
        self.calls.append((url, payload, timeout))
        if self.exc:
            raise self.exc
        return self.result


class Sink:
    def __init__(self):
        self.text = ""

    def write(self, s):
        self.text += s

    def flush(self):
        pass


def run(fake, subject="[Clayrune steward] FYI: x", body="hello", env=ENV, **kw):
    out, err = Sink(), Sink()
    ok = route.attempt(subject, body, env=env, post=fake, out=out, err=err, **kw)
    return ok, out.text, err.text


# ───────────────────────── accepted ─────────────────────────
def test_accepted_prints_transport_line_and_posts_the_exact_field_set():
    fake = Fake()
    ok, out, err = run(fake)
    assert ok is True and err == ""
    assert out == "[send_mail] sent via agent-mail, intent abc123\n"
    (url, payload, timeout), = fake.calls
    assert url == "http://127.0.0.1:5199/api/project/proj1/agent/mail/note"
    assert set(payload) == {"session_id", "request_id", "subject", "body"}
    assert payload["session_id"] == "sess1" and payload["body"] == "hello"
    assert timeout <= 20


def test_verify_counts_acceptance_as_verified_and_says_so():
    ok, out, _ = run(Fake(), verify=True)
    assert ok and "sent via agent-mail, intent abc123" in out
    assert "no IMAP check" in out


def test_port_from_env_and_project_is_url_quoted():
    fake = Fake()
    run(fake, env={**ENV, "CLAYRUNE_PORT": "6001", "CLAYRUNE_PROJECT_ID": "a b/c"})
    assert fake.calls[0][0] == "http://127.0.0.1:6001/api/project/a%20b%2Fc/agent/mail/note"


def test_flags_override_env():
    fake = Fake()
    run(fake, env={}, project="p9", session="s9")
    url, payload, _ = fake.calls[0]
    assert "/project/p9/" in url and payload["session_id"] == "s9"


# ───────────────────────── fallbacks ─────────────────────────
@pytest.mark.parametrize("result,exc,needle", [
    ((404, {"error": "not_found"}), None, "HTTP 404 not_found"),
    ((404, {"error": "session_unavailable"}), None, "HTTP 404 session_unavailable"),
    ((409, {"error": "consent_required"}), None, "HTTP 409 consent_required"),
    ((503, {"error": "vault_locked"}), None, "HTTP 503 vault_locked"),
    ((500, None), None, "HTTP 500"),
    ((200, {"intent_id": "i", "state": "uncertain", "reason": "transport_uncertain"}), None,
     "not accepted: state uncertain transport_uncertain"),
    ((200, {"state": "accepted"}), None, "not accepted"),          # no intent id
    ((200, "not a dict"), None, "HTTP 200"),
    (None, ConnectionRefusedError("refused"), "ConnectionRefusedError"),
    (None, OSError("boom"), "OSError"),
])
def test_every_failure_falls_back_with_one_stderr_line(result, exc, needle):
    ok, out, err = run(Fake(result=result, exc=exc))
    assert ok is False and out == ""
    assert err.count("\n") == 1
    assert err.startswith("[send_mail] agent-mail unavailable (")
    assert needle in err and err.rstrip().endswith("; sending via Gmail")


def test_server_hint_is_surfaced_on_the_one_fallback_line():
    hint = "Clayrune's secrets vault is locked.\nUnlock it in Settings.\x1b " + "x" * 600
    ok, out, err = run(Fake(result=(503, {"error": "vault_locked", "hint": hint})))
    assert ok is False and out == ""
    assert err.count("\n") == 1 and "\x1b" not in err
    assert "HTTP 503 vault_locked: Clayrune's secrets vault is locked. Unlock it in Settings." in err
    assert len(err) < route.HINT_MAX_CHARS + 150 and err.rstrip().endswith("; sending via Gmail")


def test_timeout_falls_back(monkeypatch):
    monkeypatch.setattr(route, "TIMEOUT_S", 0.05)
    gate = threading.Event()

    def slow(url, payload, timeout):
        gate.wait(5)
        return ACCEPTED

    ok, out, err = run(slow)
    gate.set()
    assert ok is False and out == "" and "timeout" in err


@pytest.mark.parametrize("subject,body,needle", [
    ("x" * 201, "b", "subject"),
    ("bad\nsubject", "b", "subject"),
    ("  ", "b", "subject"),
    ("s", "   ", "body"),
    ("s", "é" * 7001, "bytes, over the"),   # 14002 bytes
])
def test_unacceptable_content_falls_back_without_calling_the_route(subject, body, needle):
    fake = Fake()
    ok, out, err = run(fake, subject=subject, body=body)
    assert ok is False and fake.calls == [] and needle in err


def test_body_at_the_limit_is_sent_whole_not_truncated():
    fake = Fake()
    body = "a" * route.BODY_MAX_BYTES
    ok, _, _ = run(fake, body=body)
    assert ok and fake.calls[0][1]["body"] == body


# ───────────────────────── no session context ─────────────────────────
@pytest.mark.parametrize("env", [{}, {"CLAYRUNE_PROJECT_ID": "p"}, {"CLAYRUNE_SESSION_ID": "s"}])
def test_no_session_context_is_silent_and_never_calls_the_route(env):
    fake = Fake()
    ok, out, err = run(fake, env=env)
    assert (ok, out, err) == (False, "", "") and fake.calls == []


def test_real_poster_is_never_used_under_pytest(monkeypatch):
    called = []
    monkeypatch.setattr(route.urllib.request, "urlopen", lambda *a, **k: called.append(1))
    out, err = Sink(), Sink()
    assert route.attempt("s", "b", env=ENV, out=out, err=err) is False
    assert called == [] and err.text == ""


# ───────────────────────── request_id ─────────────────────────
def test_request_id_is_deterministic_and_valid_for_the_server():
    a = route.request_id("s", "subj", "body")
    assert a == route.request_id("s", "subj", "body")
    assert a != route.request_id("s2", "subj", "body")
    assert a != route.request_id("s", "subj2", "body")
    assert a != route.request_id("s", "subj", "body2")
    assert a != route.request_id("s", "subjb", "ody")  # separator, not concatenation
    assert re.fullmatch(r"[A-Za-z0-9_-]{1,128}", a)   # mc.agent_mail_signing.ID


def test_retry_posts_the_same_request_id():
    f1, f2 = Fake(), Fake()
    run(f1)
    run(f2)
    assert f1.calls[0][1]["request_id"] == f2.calls[0][1]["request_id"]


# ───────────────────────── send_mail.main() call-in ─────────────────────────
class Transport:
    sent: list = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def ehlo(self):
        pass

    def starttls(self, *, context):
        pass

    def login(self, u, p):
        pass

    def send_message(self, m):
        type(self).sent.append(m)


@pytest.fixture
def main_env(monkeypatch, tmp_path):
    Transport.sent = []
    monkeypatch.setattr(send_mail.Path, "home", lambda: tmp_path)
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MC_LIVE_MAIL_TESTS", "1")
    monkeypatch.setattr(send_mail, "_smtp_connect", lambda h, p: Transport())
    monkeypatch.setattr(send_mail, "_load_config", lambda: {
        "user": "a@example.com", "app_password": "test-only", "to": "b@example.com"})
    for k in (*ENV, "CLAYRUNE_PORT"):
        monkeypatch.delenv(k, raising=False)
    return monkeypatch


def _main(monkeypatch, *argv):
    monkeypatch.setattr(send_mail.sys, "argv", ["send_mail.py", "--subject", "S", "--body", "B", *argv])
    return send_mail.main()


def test_main_agent_mail_accepted_sends_no_smtp(main_env, capsys):
    fake = Fake()
    main_env.setattr(route, "_http_post", fake)
    main_env.setenv("CLAYRUNE_PROJECT_ID", "proj1")
    main_env.setenv("CLAYRUNE_SESSION_ID", "sess1")
    assert _main(main_env) == 0
    assert Transport.sent == [] and len(fake.calls) == 1
    assert "sent via agent-mail, intent abc123" in capsys.readouterr().out


def test_main_falls_through_to_smtp_when_route_fails(main_env, capsys):
    main_env.setattr(route, "_http_post", Fake(result=(404, {"error": "not_found"})))
    main_env.setenv("CLAYRUNE_PROJECT_ID", "proj1")
    main_env.setenv("CLAYRUNE_SESSION_ID", "sess1")
    assert _main(main_env) == 0
    assert len(Transport.sent) == 1
    cap = capsys.readouterr()
    assert "agent-mail unavailable (HTTP 404 not_found); sending via Gmail" in cap.err
    assert "sent 'S' to b@example.com" in cap.out


def test_main_no_session_env_is_smtp_silently(main_env, capsys):
    fake = Fake()
    main_env.setattr(route, "_http_post", fake)
    assert _main(main_env) == 0
    assert len(Transport.sent) == 1 and fake.calls == []
    assert "agent-mail" not in capsys.readouterr().err


@pytest.mark.parametrize("extra", [["--smtp-only"], ["--to", "c@example.com"],
                                   ["--message-id", "m@example.com"]])
def test_main_smtp_only_and_recipient_or_message_id_pin_skip_agent_mail(main_env, extra):
    fake = Fake()
    main_env.setattr(route, "_http_post", fake)
    main_env.setenv("CLAYRUNE_PROJECT_ID", "proj1")
    main_env.setenv("CLAYRUNE_SESSION_ID", "sess1")
    assert _main(main_env, *extra) == 0
    assert len(Transport.sent) == 1 and fake.calls == []
