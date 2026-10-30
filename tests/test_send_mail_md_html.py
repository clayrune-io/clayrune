"""Operator email rendering and MIME construction; never contacts SMTP."""
from __future__ import annotations

import importlib.util
from email import policy
from email.parser import BytesParser
from html.parser import HTMLParser
from pathlib import Path

import pytest


_MAIL_DIR = Path(__file__).resolve().parent.parent / "tools" / "night-review"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _MAIL_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


md_html = _load("md_html")
send_mail = _load("send_mail")

# Transcribed from the supplied Check US stocks screenshot. Operator-specific
# report prose lives only in the untracked sample, not in this shared fixture.
STOCK_TABLE = """| Call date | Ticker | Dir | Call px | Basis | Basis px | Last px | % since basis | vs SPY | Stop/target | Sessions left | Verdict so far |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 2026-10-30 | XOM (lead) | bull | 185.74 | call px | 185.74 | 185.61 | -0.1% | -0.1pp | unscorable | 3 | too early |
| 2026-10-29 | MSFT (lead) | bull | 523.39 | entry level | 525.00 | 522.11 | -0.6% | +1.0pp | open | 3 | too early |
| 2026-10-28 | APH (lead) | bull | 81.08 | entry level | 81.25 | 77.60 | -4.5% | -2.6pp | target hit | 2 | right |
| 2026-10-27 | UPS (lead) | bull | 103.42 | entry level | 103.80 | 108.43 | +4.5% | +6.1pp | target hit | 1 | right |
| 2026-10-26 | VZ (lead) | bull | 43.56 | entry level | 43.90 | 45.02 | +2.6% | +4.3pp | expired, no barrier hit | 0 | wrong |
| 2026-10-23 | BE (lead) | bear | 192.35 | entry level | 189.00 | 202.20 | -7.0% | -7.4pp | stopped | 0 | wrong |"""


class Tags(HTMLParser):
    def __init__(self, rendered):
        super().__init__()
        self.tags = []
        self.text = []
        self.feed(rendered)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))

    def handle_data(self, data):
        self.text.append(data)

    def attrs(self, tag):
        return [attrs for name, attrs in self.tags if name == tag]


def test_screenshot_table_and_report_structure():
    report = ("# Check US stocks\n\n**Earlier calls**\n\n" + STOCK_TABLE
              + "\n\n**Post-mortems**\n\n- First finding\n* Second finding\n\n"
              + '<script>alert("bad")</script>')
    rendered = md_html.render(report)
    parsed = Tags(rendered)
    assert len(parsed.attrs("th")) == 12
    assert len(parsed.attrs("td")) == 6 * 12
    assert len(parsed.attrs("tr")) == 7
    assert len(parsed.attrs("h3")) == 2
    assert len(parsed.attrs("li")) == 2
    assert all("white-space:nowrap" in a["style"] for a in parsed.attrs("th"))
    assert all("border:1px solid" in a["style"] for a in parsed.attrs("td"))
    assert "background:#ffffff" in parsed.attrs("td")[0]["style"]
    assert "background:#f5f7fa" in parsed.attrs("td")[12]["style"]
    assert "font-size:13px" in parsed.attrs("table")[0]["style"]
    assert "max-width:860px" in parsed.attrs("div")[0]["style"]
    assert "font-size:14px" in parsed.attrs("div")[0]["style"]
    assert not parsed.attrs("script") and not parsed.attrs("style")
    assert '<script>alert("bad")</script>' in "".join(parsed.text)
    assert "|---|" not in rendered and "**Earlier calls**" not in rendered


@pytest.mark.parametrize("length,tag", [(80, "h3"), (81, "p")])
def test_only_short_single_bold_span_becomes_heading(length, tag):
    # Count displayed characters, not the longer HTML entity encoding.
    rendered = md_html.render("**" + "<&" * (length // 2) + "x" * (length % 2) + "**")
    assert len(Tags(rendered).attrs(tag)) == 1


def test_multiple_bold_spans_and_whole_long_paragraph_stay_paragraphs():
    rendered = md_html.render("**One** and **two**\n\n**" + "Long report. " * 20 + "**")
    assert len(Tags(rendered).attrs("p")) == 2
    assert len(Tags(rendered).attrs("strong")) == 3
    assert not Tags(rendered).attrs("h3")


def test_headings_inline_styles_line_breaks_lists_and_rule():
    rendered = md_html.render("# One\n## Two\n### Three\n\nFirst line\nSecond **bold** "
                              "and *italic*, `**literal** <x>` and "
                              '[site](https://example.com/?a=1&b=2).\n\n'
                              "3. numbered\n4. next\n- bullet\n* other\n\n---")
    parsed = Tags(rendered)
    for tag in ("h1", "h2", "h3", "br", "strong", "em", "code", "a", "ol", "ul", "hr"):
        assert len(parsed.attrs(tag)) == 1, tag
    assert len(parsed.attrs("li")) == 4
    assert parsed.attrs("ol")[0]["start"] == "3"
    assert parsed.attrs("a")[0]["href"] == "https://example.com/?a=1&b=2"
    assert "**literal** <x>" in "".join(parsed.text)


def test_fenced_code_is_literal_and_escaped():
    rendered = md_html.render('```python\n<script>\n**bold** | cells\n- item\n```\n\nAfter')
    parsed = Tags(rendered)
    assert len(parsed.attrs("pre")) == 1
    assert not parsed.attrs("script") and not parsed.attrs("strong")
    assert not parsed.attrs("table") and not parsed.attrs("li")
    assert "<script>\n**bold** | cells\n- item" in "".join(parsed.text)
    assert "After" in rendered
    assert len(Tags(md_html.render("```\nunclosed <x>")).attrs("pre")) == 1


def test_unknown_syntax_unsafe_links_and_attributes_are_text():
    rendered = md_html.render('#### Unknown\n> quote <img src=x onerror=bad>\n'
                              '![image](https://example.com/image.png)\n'
                              '[bad](javascript:alert) [data](data:text/html,hi)\n'
                              '[safe](https://example.com/"onclick="bad)')
    parsed = Tags(rendered)
    assert not parsed.attrs("img") and not parsed.attrs("h4")
    assert "#### Unknown" in rendered and "&lt;img" in rendered
    assert "[bad](javascript:alert)" in rendered
    assert "[data](data:text/html,hi)" in rendered
    assert "![image](https://example.com/image.png)" in rendered
    assert len(parsed.attrs("a")) == 1
    assert set(parsed.attrs("a")[0]) == {"href", "style"}


def test_table_without_outer_pipes_and_cell_pipes_and_malformed_rows():
    rendered = md_html.render("A | B\n:--- | ---:\n`a|b` | x\\|y\n"
                              "one | two | extra\n\n| not | a table |\n| -- | -- |")
    parsed = Tags(rendered)
    assert len(parsed.attrs("table")) == 1
    assert len(parsed.attrs("td")) == 2
    assert "a|b" in "".join(parsed.text) and "x|y" in "".join(parsed.text)
    assert "one | two | extra" in rendered
    assert "| -- | -- |" in rendered


@pytest.mark.parametrize("body", ["", "Unicode: café 🐦\n**Résumé**", STOCK_TABLE])
def test_build_message_keeps_plain_body_and_adds_html(body, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("message construction must never contact SMTP")

    monkeypatch.setattr(send_mail.smtplib, "SMTP", forbidden)
    msg = send_mail.build_message("Subject", body, "sender@example.com", "to@example.com",
                                  message_id="fixed@example.com")
    # Exercise the actual wire format and UTF-8 decoding, not just Python fields.
    decoded = BytesParser(policy=policy.default).parsebytes(msg.as_bytes())
    assert decoded.get_content_type() == "multipart/alternative"
    assert [p.get_content_type() for p in decoded.iter_parts()] == ["text/plain", "text/html"]
    assert decoded.get_body(preferencelist=("plain",)).get_content() == body + ("" if body.endswith("\n") else "\n")
    assert decoded.get_body(preferencelist=("html",)).get_content().rstrip("\n") == md_html.render(body)
    assert decoded["Subject"] == "Subject"
    assert decoded["From"] == "sender@example.com"
    assert decoded["To"] == "to@example.com"
    assert decoded["Message-ID"] == "<fixed@example.com>"


def test_plain_only_skips_renderer_and_generates_message_id(monkeypatch):
    import md_html as imported_renderer

    def forbidden(body):
        pytest.fail("plain-only must skip rendering")

    monkeypatch.setattr(imported_renderer, "render", forbidden)
    msg = send_mail.build_message("Subject", "**original**", "a@example.com", "b@example.com",
                                  plain_only=True)
    assert msg.get_content_type() == "text/plain"
    assert msg.get_content() == "**original**\n"
    assert msg["Message-ID"].endswith("@clayrune.local>")


def test_renderer_error_returns_plain_message_and_logs(monkeypatch, capsys):
    import md_html as imported_renderer

    def broken(body):
        raise RuntimeError("test render failed")

    monkeypatch.setattr(imported_renderer, "render", broken)
    msg = send_mail.build_message("Subject", "**original**", "a@example.com", "b@example.com",
                                  message_id="<already@bracketed>")
    assert msg.get_content_type() == "text/plain"
    assert msg.get_content() == "**original**\n"
    assert msg["Message-ID"] == "<already@bracketed>"
    assert "HTML rendering failed; sending plain text only: test render failed" in capsys.readouterr().err


@pytest.mark.parametrize("flag", [[], ["--plain-only"]])
def test_cli_plain_flag_keeps_pytest_smtp_guard(flag, monkeypatch, tmp_path, capsys):
    def forbidden(*args, **kwargs):
        pytest.fail("pytest guard must refuse before SMTP")

    monkeypatch.setattr(send_mail.Path, "home", lambda: tmp_path)
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(send_mail.smtplib, "SMTP", forbidden)
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "test_cli_plain_flag")
    monkeypatch.delenv("MC_LIVE_MAIL_TESTS", raising=False)
    monkeypatch.setattr(send_mail.sys, "argv", ["send_mail.py", "--subject", "test", "--body", "x", *flag])
    assert send_mail.main() == 5
    assert "refused: running under pytest" in capsys.readouterr().err


@pytest.mark.parametrize("mode", ["html", "plain", "broken", "missing"])
def test_cli_constructs_and_hands_message_to_fake_transport(mode, monkeypatch, tmp_path, capsys):
    """Exercise the CLI handoff with synthetic config and an in-memory transport.

    Opt-in is confined to this test after replacing SMTP. No real config,
    credentials or network are used, even if the transport path regresses.
    """
    import builtins
    import md_html as imported_renderer

    def forbidden(*args, **kwargs):
        pytest.fail("real SMTP must never be constructed")

    sent = []

    class Transport:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def ehlo(self):
            pass

        def starttls(self, *, context):
            assert context == "fake-tls-context"

        def login(self, user, password):
            assert (user, password) == ("a@example.com", "test-only")

        def send_message(self, message):
            sent.append(message)

    monkeypatch.setattr(send_mail.smtplib, "SMTP", forbidden)
    monkeypatch.setattr(send_mail, "_smtp_connect", lambda host, port: Transport())
    monkeypatch.setattr(send_mail.ssl, "create_default_context", lambda: "fake-tls-context")
    monkeypatch.setattr(send_mail, "_load_config", lambda: {
        "user": "a@example.com", "app_password": "test-only", "to": "b@example.com"})
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MC_LIVE_MAIL_TESTS", "1")
    args = ["send_mail.py", "--subject", "Synthetic", "--body", "**Keep this**",
            "--message-id", "cli@example.com"]
    if mode == "plain":
        args.append("--plain-only")
    elif mode == "broken":
        def broken(body):
            raise RuntimeError("synthetic renderer bug")
        monkeypatch.setattr(imported_renderer, "render", broken)
    elif mode == "missing":
        original_import = builtins.__import__

        def without_renderer(name, *args, **kwargs):
            if name == "md_html":
                raise ImportError("synthetic missing renderer")
            return original_import(name, *args, **kwargs)
        monkeypatch.setattr(builtins, "__import__", without_renderer)
    monkeypatch.setattr(send_mail.sys, "argv", args)
    assert send_mail.main() == 0
    assert len(sent) == 1
    assert sent[0]["Message-ID"] == "<cli@example.com>"
    assert sent[0].get_body(preferencelist=("plain",)).get_content() == "**Keep this**\n"
    assert bool(sent[0].get_body(preferencelist=("html",))) == (mode == "html")
    if mode in ("broken", "missing"):
        assert "HTML rendering failed; sending plain text only" in capsys.readouterr().err
