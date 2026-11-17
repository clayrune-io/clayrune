"""tools/mail-mcp/server.py and read_digest.py: a hostile uid or mailbox must not
reach the wire, a refused search is not read as ids, a non-object JSON line
does not end the server (MC-1179 pass 12 TL19, TL20).

No mailbox is touched: `_connect` is replaced by a recording fake.
"""
import importlib.util
import io
import sys
from pathlib import Path

import pytest

_MAIL_DIR = Path(__file__).resolve().parent.parent / "tools" / "mail-mcp"


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, _MAIL_DIR / filename)
    mod = importlib.util.module_from_spec(spec)
    before = sys.path[:]
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.path[:] = before
    return mod


srv = _load("mail_mcp_server_under_test", "server.py")
iq = sys.modules.get("imap_quote") or _load("imap_quote_for_tools", "imap_quote.py")
digest = _load("read_digest_under_test", "read_digest.py")

HOSTILE = "1 (FLAGS)\r\nX1 SELECT INBOX\r\nX2 UID STORE 1:* +FLAGS (\\Deleted)\r\nX3 EXPUNGE"


class FakeM:
    def __init__(self, select_typ="OK", search_typ="OK", search_data=None):
        self.calls = []
        self._select_typ = select_typ
        self._search_typ = search_typ
        self._search_data = search_data if search_data is not None else [b""]

    def select(self, mailbox, readonly=False):
        self.calls.append(("select", mailbox, readonly))
        return self._select_typ, [b"1"]

    def uid(self, *args):
        self.calls.append(("uid",) + args)
        if args[0] == "SEARCH":
            return self._search_typ, self._search_data
        return "OK", [None]

    def logout(self):
        pass


def _no_connect(monkeypatch):
    def boom():
        raise AssertionError("must refuse before connecting")
    monkeypatch.setattr(srv, "_connect", boom)


def test_safe_uid_accepts_digits_only():
    assert srv.safe_uid("123") == b"123"
    assert srv.safe_uid(77) == b"77"
    for bad in [HOSTILE, "1:*", "1 2", "", "abc", "-1", "1\n"]:
        if bad == "1\n":  # surrounding whitespace is stripped, not injected
            assert srv.safe_uid(bad) == b"1"
            continue
        with pytest.raises(ValueError):
            srv.safe_uid(bad)


def test_read_email_hostile_uid_never_reaches_imap(monkeypatch):
    _no_connect(monkeypatch)
    with pytest.raises(ValueError):
        srv.tool_read_email(HOSTILE)


def test_read_email_hostile_mailbox_never_reaches_imap(monkeypatch):
    _no_connect(monkeypatch)
    with pytest.raises(ValueError):
        srv.tool_read_email("1", mailbox="INBOX\r\nX1 SELECT INBOX")
    with pytest.raises(ValueError):
        srv.tool_list_recent(3, mailbox="INBOX\r\nX1 EXPUNGE")


def test_mailbox_with_a_space_is_sent_as_one_quoted_atom(monkeypatch):
    M = FakeM()
    monkeypatch.setattr(srv, "_connect", lambda: M)
    srv.tool_list_recent(3, mailbox="[Gmail]/All Mail")
    assert M.calls[0] == ("select", '"[Gmail]/All Mail"', True)


def test_failed_select_is_reported_not_searched(monkeypatch):
    M = FakeM(select_typ="NO")
    monkeypatch.setattr(srv, "_connect", lambda: M)
    out = srv.tool_list_recent(3, mailbox="Nope")
    assert out.startswith("(cannot open mailbox Nope")
    assert [c[0] for c in M.calls] == ["select"]


def test_refused_search_is_not_read_as_a_list_of_ids(monkeypatch):
    M = FakeM(search_typ="BAD", search_data=[b"Could not parse command 12 34"])
    monkeypatch.setattr(srv, "_connect", lambda: M)
    out = srv.tool_search_email("subject:x")
    assert out.startswith("(search failed:")
    assert "Could not parse command" in out
    # no FETCH was attempted with the words of the error as uids
    assert not any(c[:2] == ("uid", "FETCH") for c in M.calls)


def test_non_object_json_line_does_not_end_the_server(monkeypatch, capsys):
    lines = "[]\n1\n\"x\"\n" + '{"jsonrpc":"2.0","id":7,"method":"tools/list"}\n'
    monkeypatch.setattr(sys, "stdin", io.StringIO(lines))
    srv._serve()
    out = capsys.readouterr().out
    assert '"id": 7' in out


def test_read_digest_refuses_a_hostile_mailbox_and_a_refused_search():
    M = FakeM(search_typ="BAD", search_data=[b"nope"])

    class MS:
        def _connect(self):
            return M

        gmail_search = staticmethod(lambda m, q: m.uid("SEARCH", None, "ALL"))

    with pytest.raises(ValueError):
        digest.fetch_messages(MS(), "q", 1, "INBOX\r\nX1 EXPUNGE")
    with pytest.raises(RuntimeError):
        digest.fetch_messages(MS(), "q", 1, "INBOX")
