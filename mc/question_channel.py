"""Offline question channel — deliver an agent's question when nobody is watching.

An agent raises a question (an ```mc:question``` fence — see
`agent_runtime.MC_TOOL_PROTOCOL_PROMPT`). If the user is looking at the session,
the existing SSE → interactive-form path handles it and this module does nothing.

If the run is **unattended** — a schedule, a steward cycle, the night review, or
simply a tab the user closed — that form renders to an empty room. The agent then
hangs until the guardian notices, and the question is effectively lost. This
module is the fallback: it sends the question over the user's configured channel
so they can answer it **offline**, and feeds the reply back into the same
follow-up path the UI uses.

## Attended vs unattended

A question is *attended* if an SSE viewer polled the session recently
(`session['_last_sse_poll_time']` — the same heartbeat the guardian already uses
to decide a question "may have been missed").

The **grace window is the whole trick**. Deliver instantly and a user who opens
the tab three seconds later gets a pointless email; never deliver and an
unattended run hangs forever. So: raise → wait `question_channel_grace_s` →
re-check for a viewer → deliver only if there is still nobody there.

## Posture

Best-effort, and **never load-bearing**: every entry point swallows its own
errors. A dead mailbox must not break an agent turn. Same rule as Scribe and the
Distiller.

## Reply → resume

The subject carries the question id. `poll_replies()` matches an inbound reply to
a pending question, maps the body to an option (a number, a label, or free text)
and answers via `POST /agent/followup` — the exact path the chat form uses, so
there is no second resume mechanism to keep in sync.

**Idempotency is load-bearing**: a question is delivered once and answered once.
Anything else trains the user to ignore the channel.

## Subject match is not enough (fixed 2026-09-14)

We mail the question to the operator's own inbox, so the *outgoing* question
mail itself lands in INBOX carrying the same `[Clayrune question]` subject and
`q:<qid>` we search for — `handle_reply` used to treat that as an answer, so
every emailed question answered itself with its own first line within one poll
interval, and any real human reply arrived too late (`_answered` already held
the qid). Same hole, worse: anyone who can land a message in that inbox with a
guessable 8-hex qid got their first line POSTed to `/agent/followup` on a
fully-tooled session — the exact class AGENT_RULES.md's mail-laundering rule
exists to close, bypassed because this path never went through it.

The fix: a reply is accepted only if its `In-Reply-To`/`References` header
actually names the Message-ID of the question mail *we* sent (recorded in
`_outbox[qid]['message_id']` at send time; see `--message-id` on
`send_mail.py`). Subject + qid alone is now necessary but not sufficient. The
sender address is checked too, but only as an extra filter — `From:` is
unauthenticated and never proof by itself.

Free-text questions (no options at all) are never auto-answered from mail:
`match_answer` only resolves an option-bearing question, and a mismatch or a
no-option question leaves the question open rather than passing raw text to
`/agent/followup`. See the module docstring on `match_answer` for why.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.request
from email.utils import make_msgid
from pathlib import Path
from typing import Any, Dict, Optional

from mc import state
from mc.core import _log

# Question ids we have already delivered / already answered. In-memory is the
# right scope: a restart drops the agent sessions these refer to anyway.
_delivered: set[str] = set()
_answered: set[str] = set()
_lock = threading.Lock()

# qid -> everything poll_replies() needs to answer it without touching sessions.
_outbox: Dict[str, Dict[str, Any]] = {}

_SUBJECT_TAG = "[Clayrune question]"
_QID_RE = re.compile(r"\bq:([0-9a-f]{8})\b")

_MAILER = Path(__file__).resolve().parent.parent / "tools" / "night-review" / "send_mail.py"


# ─── Config ──────────────────────────────────────────────────────────────────


def _cfg(project: Optional[dict], key: str, default: Any) -> Any:
    """Per-project value, falling back to global CONFIG, falling back to default."""
    if project:
        v = project.get(key)
        if v not in (None, ""):
            return v
    v = state.CONFIG.get(key)
    return default if v in (None, "") else v


def channel_for(project: Optional[dict]) -> str:
    """`email` | `off`. Default email — a question nobody sees is the bug."""
    return str(_cfg(project, "question_channel", "email")).lower()


def grace_seconds(project: Optional[dict]) -> int:
    try:
        return max(0, int(_cfg(project, "question_channel_grace_s", 45)))
    except (TypeError, ValueError):
        return 45


# ─── Attended? ───────────────────────────────────────────────────────────────


def is_attended(session: dict, *, grace: int) -> bool:
    """Is a human actually looking at this session right now?

    The signal is the SSE poll heartbeat, which only ticks while a browser is
    streaming this session. `trigger_type` alone is NOT enough: a scheduled run
    the user happens to be watching should still answer in the chat, and a manual
    run whose tab was closed is just as unattended as a cron job.
    """
    last = session.get("_last_sse_poll_time") or 0
    return (time.time() - last) <= grace


# ─── Rendering ───────────────────────────────────────────────────────────────


def render(project_name: str, session: dict, qid: str, questions: list) -> tuple[str, str]:
    """(subject, body). The body must stand alone — it may be read days later, on
    a phone, by someone with no memory of this session."""
    subject = f"{_SUBJECT_TAG} {project_name} · q:{qid[:8]}"

    task = (session.get("task") or "").strip()
    lines = [
        "An agent is waiting on your answer. Nobody was watching the session, so",
        "it is coming to you here instead.",
        "",
        f"Project : {project_name}",
    ]
    if task:
        lines.append(f"Task    : {task[:300]}")
    trig = session.get("trigger_type") or "manual"
    lines += [f"Trigger : {trig}", "", "-" * 60, ""]

    for qi, q in enumerate(questions, 1):
        if not isinstance(q, dict):
            continue
        header = (q.get("header") or "").strip()
        text = (q.get("question") or "").strip()
        lines.append(f"Q{qi}. {text}" + (f"   [{header}]" if header else ""))
        opts = q.get("options") or []
        for oi, opt in enumerate(opts, 1):
            if isinstance(opt, dict):
                label = (opt.get("label") or "").strip()
                desc = (opt.get("description") or "").strip()
            else:
                label, desc = str(opt), ""
            lines.append(f"    {oi}. {label}" + (f" — {desc}" if desc else ""))
        lines.append("")

    has_options = any(isinstance(q, dict) and (q.get("options") or [])
                      for q in questions)
    lines += ["-" * 60, ""]
    if has_options:
        # Must match handle_reply: an unmatched reply is ignored, never forwarded.
        lines += [
            "TO ANSWER: reply to this email. Keep the subject line intact (it carries",
            f"the question id q:{qid[:8]}). The first line of your reply is the answer:",
            "",
            "  • a number   -> picks that option (e.g. \"2\")",
            "  • a label    -> picks that option by name",
            "  • anything else -> ignored; the question stays open",
        ]
    else:
        # Free-text questions are never auto-answered from mail (see handle_reply).
        lines += [
            "This question needs a free-text answer, which cannot be given by email.",
            "Open the session in Clayrune to answer it.",
        ]
    lines += [
        "",
        "The agent stays parked until it is answered. If it never is, nothing",
        "happens — it simply never resumes.",
        "",
        "-- Clayrune",
    ]
    return subject, "\n".join(lines)


# ─── Delivery ────────────────────────────────────────────────────────────────


def _send_email(subject: str, body: str, to: Optional[str], message_id: str) -> bool:
    """Reuse the existing mailer. Per AGENT_RULES: no new SMTP code, no new creds.

    `message_id` is generated by us (not the mailer) and passed through via
    `--message-id` so we know in advance exactly what Message-ID went out —
    that is the value `handle_reply` later requires an inbound In-Reply-To/
    References to name before treating anything as a genuine reply.
    """
    if not _MAILER.exists():
        _log(f"[question-channel] mailer not found at {_MAILER}", flush=True)
        return False
    cmd = [sys.executable, str(_MAILER), "--subject", subject, "--body", body,
           "--message-id", message_id]
    if to:
        cmd += ["--to", to]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            _log(f"[question-channel] send failed rc={r.returncode}: "
                 f"{(r.stderr or r.stdout or '').strip()[:200]}", flush=True)
            return False
        return True
    except Exception as e:
        _log(f"[question-channel] send failed: {e}", flush=True)
        return False


def on_question_raised(session: dict) -> None:
    """Called at the turn boundary when a question was parsed. Non-blocking.

    Arms a timer for the grace window rather than deciding now: the user may be
    two seconds from opening the tab, and an email they didn't need is how a
    notification channel earns itself an ignore rule.
    """
    pending = session.get("pending_questions") or []
    if not pending:
        return
    q = pending[-1]
    qid = q.get("question_id") or ""
    if not qid:
        return

    with _lock:
        if qid in _delivered:
            return          # exactly-once, even if the turn scan runs twice
        _delivered.add(qid)

    project_id = session.get("project_id") or ""
    try:
        # Imported lazily: project_routes imports plenty, and this module is
        # pulled in from inside a stream reader.
        from mc.blueprints.project_routes import load_project
        project = load_project(project_id) or {}
    except Exception:
        project = {}

    if channel_for(project) == "off":
        return

    grace = grace_seconds(project)
    _outbox[qid] = {
        "project_id": project_id,
        "project_name": project.get("name") or project_id,
        "session_id": session.get("session_id") or "",
        "questions": q.get("questions") or [],
        "to": _cfg(project, "question_channel_to", None),
    }

    t = threading.Timer(grace, _deliver_if_still_unattended, args=(qid, session, grace))
    t.daemon = True
    t.start()


def _deliver_if_still_unattended(qid: str, session: dict, grace: int) -> None:
    try:
        # Answered in the chat while we waited? Then there WAS someone there.
        if not session.get("waiting_for_question"):
            return
        if is_attended(session, grace=grace):
            return

        meta = _outbox.get(qid)
        if not meta:
            return

        # Resolve the expected reply sender now, at send time, the same way
        # send_mail.py itself will resolve `to` (configured recipient, else
        # mail-yourself). Used later only as an extra, non-authoritative
        # filter in handle_reply -- From: is never proof by itself.
        to_addr = meta.get("to")
        if not to_addr:
            creds = _creds()
            to_addr = creds[0] if creds else None

        message_id = make_msgid(domain="clayrune.local")
        subject, body = render(meta["project_name"], session, qid, meta["questions"])
        if _send_email(subject, body, meta.get("to"), message_id):
            meta["message_id"] = message_id
            meta["to"] = to_addr
            _log(f"[question-channel] delivered q:{qid[:8]} "
                 f"({meta['project_name']}) — nobody was watching", flush=True)
            session.setdefault("log_lines", []).append(
                f"[question sent to your offline channel — reply to the email "
                f"(q:{qid[:8]}) and I'll pick it up]")
    except Exception as e:
        _log(f"[question-channel] delivery failed for {qid[:8]}: {e}", flush=True)


# ─── Reply → answer ──────────────────────────────────────────────────────────


def match_answer(reply_line: str, questions: list) -> str:
    """Map the first line of a reply onto an option label.

    A number picks by position; otherwise an exact (then loose) label match
    wins. If the question carries options and NONE of them match, this
    returns "" — an unresolved option-question must never resume the agent
    with the raw reply text, or a value the agent never offered gets passed
    off as a choice it did.

    If the question carries no options at all (genuinely free-text), the raw
    text is returned so the caller (`handle_reply`) can apply its own,
    separately-documented policy for that case — this function only knows
    how to *match*, not whether free text is safe to forward.
    """
    text = (reply_line or "").strip()
    if not text:
        return ""
    opts = []
    for q in questions or []:
        if isinstance(q, dict):
            for o in (q.get("options") or []):
                opts.append((o.get("label") or "") if isinstance(o, dict) else str(o))

    if not opts:
        return text

    if text.isdigit():
        i = int(text)
        if 1 <= i <= len(opts):
            return opts[i - 1]
        return ""

    low = text.lower()
    for label in opts:
        if label and label.lower() == low:
            return label
    for label in opts:
        if label and (low in label.lower() or label.lower() in low):
            return label
    return ""


def _answer(qid: str, answer: str) -> bool:
    """Resume the agent through the SAME follow-up path the chat form uses."""
    meta = _outbox.get(qid)
    if not meta or not answer:
        return False
    # Same resolution order as server.py: env wins over config.
    port = int(os.environ.get("MC_PORT") or state.CONFIG.get("port") or 5199)
    url = f"http://localhost:{port}/api/project/{meta['project_id']}/agent/followup"
    payload = json.dumps({
        "message": answer,
        "session_id": meta["session_id"],
    }).encode()
    req = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            ok = 200 <= r.status < 300
        if ok:
            _log(f"[question-channel] q:{qid[:8]} answered offline: {answer[:60]!r}",
                 flush=True)
        return ok
    except Exception as e:
        _log(f"[question-channel] could not deliver answer for {qid[:8]}: {e}", flush=True)
        return False


# The literal opening line of `render()`'s body. If this shows up as the
# "first" line of a message we're examining, we are almost certainly looking
# at our OWN outgoing question mail (or a bounce/redelivery of it) rather
# than a reply — belt-and-suspenders alongside the Message-ID check below,
# which is the load-bearing one.
_OWN_BODY_MARKER = "An agent is waiting on your answer."


def _norm_mid(value: str) -> str:
    """`<abc@domain>` and `abc@domain` compare equal; MTAs are inconsistent
    about the angle brackets on In-Reply-To/References."""
    return (value or "").strip().strip("<>").strip()


def _is_genuine_reply(meta: dict, message_id: str, in_reply_to: str, references: str) -> bool:
    """Only a message whose In-Reply-To/References actually NAMES the
    Message-ID of the question mail we sent counts as a reply to it. A
    subject carrying a matching `q:<qid>` is necessary but — since we mail
    ourselves, so our own outgoing mail carries that same subject — never
    sufficient on its own; see the module docstring, 'Subject match is not
    enough'.
    """
    our_mid = _norm_mid(meta.get("message_id") or "")
    if not our_mid:
        return False   # we don't know what we sent; refuse rather than guess
    if _norm_mid(message_id) == our_mid:
        return False   # this IS the message we sent, not a reply to it
    haystack = f"{in_reply_to or ''} {references or ''}"
    tokens = haystack.replace("<", " <").split()
    return any(_norm_mid(t) == our_mid for t in tokens)


def handle_reply(subject: str, body: str, *, message_id: str = "",
                  in_reply_to: str = "", references: str = "",
                  from_header: str = "") -> bool:
    """Given a reply's headers + body, answer the question it refers to.

    Returns True if an agent was actually resumed. Idempotent: a qid is
    answered at most once, so a mail poller re-reading the same thread cannot
    double-send — but ONLY once the message has passed the reply-authenticity
    checks below. A rejected message never touches `_answered`, so it can
    never poison a later genuine reply.
    """
    m = _QID_RE.search(subject or "")
    if not m:
        return False
    short = m.group(1)

    qid = next((k for k in _outbox if k.startswith(short)), None)
    if not qid:
        return False   # unknown or expired — ignore, never guess

    meta = _outbox[qid]

    if not _is_genuine_reply(meta, message_id, in_reply_to, references):
        _log(f"[question-channel] ignored non-reply mail for q:{short} "
             f"(subject/qid matched but In-Reply-To did not name our "
             f"Message-ID — likely our own sent copy or a spoofed subject)",
             flush=True)
        return False

    expected_from = _norm_mid((meta.get("to") or "")).lower()
    if expected_from and expected_from not in (from_header or "").lower():
        _log(f"[question-channel] ignored mail for q:{short}: From did not "
             f"match the configured operator address (unauthenticated hint "
             f"only, but it did not even match)", flush=True)
        return False

    with _lock:
        if qid in _answered:
            return False
        _answered.add(qid)

    first = ""
    for raw in (body or "").splitlines():
        ln = raw.strip()
        # Skip quoted text and the usual reply cruft.
        if not ln or ln.startswith(">") or ln.startswith("On ") and ln.endswith("wrote:"):
            continue
        first = ln
        break

    if first.startswith(_OWN_BODY_MARKER):
        # Defense in depth: even though In-Reply-To already passed, never
        # treat our own outgoing question text as someone's answer.
        with _lock:
            _answered.discard(qid)
        _log(f"[question-channel] ignored q:{short}: body looked like our "
             f"own outgoing question text, not a reply", flush=True)
        return False

    questions = meta.get("questions") or []
    has_options = any(
        isinstance(q, dict) and (q.get("options") or []) for q in questions)

    answer = match_answer(first, questions)

    if has_options:
        if not answer:
            with _lock:
                _answered.discard(qid)   # no match — let a later reply work
            _log(f"[question-channel] q:{short} reply did not match any "
                 f"option — left open, not resumed", flush=True)
            return False
        return _answer(qid, answer)

    # Genuinely free-text question (no options anywhere in it). Decision:
    # never auto-resume a fully-tooled agent from a mail reply here — see
    # the module docstring. This is stricter than laundering the text through
    # mc.mail_launder first: that module's digest schema is shaped for
    # approved/declined/unclear decisions, not arbitrary free-text answers,
    # and force-fitting an open-ended answer into that enum would either lose
    # the answer or misrepresent it. Leaving the question open costs nothing
    # unsafe; the session still has it pending for the next attended look.
    with _lock:
        _answered.discard(qid)
    if answer:
        _log(f"[question-channel] q:{short} is a free-text question (no "
             f"options) — offline mail auto-answer is disabled for these, "
             f"leaving it open", flush=True)
    return False


# ─── Inbound poller ──────────────────────────────────────────────────────────
#
# A purpose-built IMAP read, NOT a reuse of tools/mail-mcp/server.py: that server
# is agent-facing and returns human-formatted strings, which is the wrong shape
# for programmatic matching. It does share the same credentials, so there is
# still only one secret.

_CREDS = Path.home() / ".clayrune" / "night-mail.json"
_IMAP_HOST = "imap.gmail.com"


def _creds() -> Optional[tuple[str, str]]:
    user = os.environ.get("NIGHT_MAIL_USER", "")
    pw = os.environ.get("NIGHT_MAIL_APP_PASSWORD", "")
    if not (user and pw):
        try:
            cfg = json.loads(_CREDS.read_text(encoding="utf-8"))
            user = user or cfg.get("user", "")
            pw = pw or cfg.get("app_password", "")
        except Exception:
            return None
    pw = (pw or "").replace(" ", "").strip()
    return (user, pw) if user and pw else None


def poll_replies() -> int:
    """Scan the inbox for replies to outstanding questions. Returns #answered.

    Only looks when we are actually waiting on something — an idle Clayrune must
    not sit there logging into a mailbox every minute for no reason.
    """
    with _lock:
        outstanding = [q for q in _outbox if q not in _answered]
    if not outstanding:
        return 0

    creds = _creds()
    if not creds:
        return 0

    import imaplib
    from email import message_from_bytes
    from email.header import decode_header, make_header

    answered = 0
    try:
        M = imaplib.IMAP4_SSL(_IMAP_HOST, 993, timeout=30)
        M.login(*creds)
        try:
            M.select("INBOX", readonly=True)
            typ, data = M.search(None, 'SUBJECT', '"Clayrune question"')
            if typ != "OK":
                return 0
            uids = (data[0].split() if data and data[0] else [])[-30:]
            for uid in uids:
                typ, msg_data = M.fetch(uid, "(RFC822)")
                if typ != "OK" or not msg_data or not isinstance(msg_data[0], tuple):
                    continue
                msg = message_from_bytes(msg_data[0][1])
                subject = str(make_header(decode_header(msg.get("Subject", ""))))
                msg_id = (msg.get("Message-ID") or "").strip()
                in_reply_to = (msg.get("In-Reply-To") or "").strip()
                refs = (msg.get("References") or "").strip()
                from_hdr = msg.get("From", "")
                from_header = str(make_header(decode_header(from_hdr))) if from_hdr else ""
                body = ""
                if msg.is_multipart():
                    for part in msg.walk():
                        if part.get_content_type() == "text/plain":
                            raw = part.get_payload(decode=True)
                            if isinstance(raw, bytes):
                                body = raw.decode(
                                    part.get_content_charset() or "utf-8", "replace")
                            break
                else:
                    raw = msg.get_payload(decode=True)
                    if isinstance(raw, bytes):
                        body = raw.decode(
                            msg.get_content_charset() or "utf-8", "replace")
                if handle_reply(subject, body, message_id=msg_id,
                                 in_reply_to=in_reply_to, references=refs,
                                 from_header=from_header):
                    answered += 1
        finally:
            try:
                M.logout()
            except Exception:
                pass
    except Exception as e:
        _log(f"[question-channel] inbox poll failed: {e}", flush=True)
    return answered


def start_poller(interval_s: int = 120) -> None:
    """Background loop. Best-effort; a mailbox outage must never touch an agent."""
    def _loop():
        while True:
            try:
                poll_replies()
            except Exception as e:
                _log(f"[question-channel] poller error: {e}", flush=True)
            time.sleep(max(30, interval_s))

    t = threading.Thread(target=_loop, name="question-channel-poller", daemon=True)
    t.start()
    _log("[question-channel] reply poller started", flush=True)
