"""tools/mail-mcp/imap_quote.py — the X-GM-RAW / TEXT argument must reach the
wire as one well-formed IMAP string (backlog 0a20a80c).

No mailbox is touched: a real `imaplib.IMAP4` subclass with no socket records
the bytes imaplib would have sent.
"""
import imaplib
import importlib.util
import re
from pathlib import Path


_MAIL_DIR = Path(__file__).resolve().parent.parent / "tools" / "mail-mcp"


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, _MAIL_DIR / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


iq = _load("imap_quote_under_test", "imap_quote.py")


class WireIMAP(imaplib.IMAP4):
    """imaplib.IMAP4 with the socket cut off; records everything sent."""

    def __init__(self, replies=None):  # no super().__init__: it connects
        self.state = "SELECTED"
        self.literal = None
        self.untagged_responses = {}
        self.tagged_commands = {}
        self.is_readonly = True
        self.debug = 0
        self._encoding = "ascii"
        self.tagpre = b"A"
        self.tagnum = 0
        self.tagre = re.compile(rb"(?P<tag>A\d+) (?P<type>[A-Z]+) (?P<data>.*)")
        self.sent = []
        self._replies = list(replies or [])

    def _log(self, line):
        pass

    def send(self, data):
        self.sent.append(bytes(data))

    def _get_response(self):
        return None

    def _command_complete(self, name, tag):
        typ, data = self._replies.pop(0) if self._replies else ("OK", [b"1 2"])
        self.untagged_responses["SEARCH"] = data
        return typ, data


def _wire(M):
    return b"".join(M.sent)


def _search(key, query):
    M = WireIMAP()
    iq.uid_search(M, key, query)
    return _wire(M)


def test_plain_term_is_quoted():
    assert _search("X-GM-RAW", "[Clayrune steward]") == (
        b'A0 UID SEARCH X-GM-RAW "[Clayrune steward]"\r\n')


def test_embedded_double_quotes_are_escaped():
    wire = _search("X-GM-RAW", 'subject:"Developer ID certificate"')
    assert wire == (
        b'A0 UID SEARCH X-GM-RAW "subject:\\"Developer ID certificate\\""\r\n')


def test_old_interpolation_was_malformed():
    # Pins the defect: the removed f'"{query}"' form closes the string early.
    q = 'subject:"Developer ID certificate"'
    assert f'"{q}"' == '"subject:"Developer ID certificate""'
    assert iq.quote(q) != f'"{q}"'


def test_parentheses_with_or_stay_inside_one_string():
    wire = _search("X-GM-RAW", "(from:a OR from:b)")
    assert wire == b'A0 UID SEARCH X-GM-RAW "(from:a OR from:b)"\r\n'


def test_parentheses_and_quotes_together():
    wire = _search("X-GM-RAW", '(subject:"a b" OR subject:"c d")')
    assert wire == (
        b'A0 UID SEARCH X-GM-RAW "(subject:\\"a b\\" OR subject:\\"c d\\")"\r\n')


def test_backslash_is_escaped_before_quote():
    # a\"b  ->  a\\\"b  (backslash doubled, then quote escaped)
    assert iq.quote('a\\"b') == '"a\\\\\\"b"'
    assert iq.quote("a\\b") == '"a\\\\b"'


def test_quoted_form_round_trips():
    for s in ['x', 'a"b', 'a\\b', '\\"', '"', '\\', '(a OR "b")', '']:
        q = iq.quote(s)
        inner = q[1:-1]
        assert re.sub(r'\\(.)', r'\1', inner) == s
        assert not re.search(r'(?<!\\)(?:\\\\)*"', inner)  # no bare quote


def test_non_ascii_goes_as_utf8_literal_with_charset():
    M = WireIMAP()
    iq.uid_search(M, "X-GM-RAW", "subject:café")
    payload = "subject:café".encode("utf-8")
    assert _wire(M) == (
        b"A0 UID SEARCH CHARSET UTF-8 X-GM-RAW {%d}\r\n" % len(payload)
        + payload + b"\r\n")
    assert M.literal is None  # imaplib consumed it


def test_crlf_goes_as_literal_without_charset():
    M = WireIMAP()
    iq.uid_search(M, "TEXT", "line1\r\nline2")
    assert _wire(M) == (
        b"A0 UID SEARCH TEXT {12}\r\n" + b"line1\r\nline2" + b"\r\n")


def test_search_args_plain_has_no_literal():
    args, literal = iq.search_args("X-GM-RAW", 'a "b"')
    assert literal is None
    assert args == (None, "X-GM-RAW", '"a \\"b\\""')


def test_text_fallback_uses_the_same_quoting():
    M = WireIMAP(replies=[("NO", [b"unsupported"]), ("OK", [b"7"])])
    typ, data = iq.gmail_search(M, 'subject:"Developer ID certificate"')
    assert (typ, data) == ("OK", [b"7"])
    sent = _wire(M)
    assert b'A0 UID SEARCH X-GM-RAW "subject:\\"Developer ID certificate\\""\r\n' in sent
    assert b'A1 UID SEARCH TEXT "subject:\\"Developer ID certificate\\""\r\n' in sent


def test_callers_use_the_shared_helper():
    src_server = (_MAIL_DIR / "server.py").read_text(encoding="utf-8")
    src_digest = (_MAIL_DIR / "read_digest.py").read_text(encoding="utf-8")
    for src in (src_server, src_digest):
        assert "gmail_search(" in src
        assert "f'\"{query}\"'" not in src
        assert '"TEXT", query' not in src


def test_fetch_messages_routes_through_server_gmail_search():
    seen = []

    class FakeM:
        def select(self, *a, **k):
            pass

        def uid(self, *a):
            raise AssertionError("fetch_messages must not build its own SEARCH")

        def logout(self):
            pass

    class FakeServer:
        @staticmethod
        def gmail_search(M, q):
            seen.append(q)
            return ("OK", [b""])

        def _connect(self):
            return FakeM()

    digest = _load("read_digest_under_test", "read_digest.py")
    assert digest.fetch_messages(FakeServer(), 'subject:"a b"', 5, "INBOX") == []
    assert seen == ['subject:"a b"']


def test_server_module_exposes_shared_helper():
    server = _load("mail_server_under_test", "server.py")
    assert callable(server.gmail_search)
