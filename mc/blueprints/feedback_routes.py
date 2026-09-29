"""In-app feedback entry point — send path (blueprint).

Backlog f638e8d9 (MC-904), FEEDBACK CHANNEL 1/3. Scope here is the entry
point's server-side send path only — items 2/3 and 3/3 are separate backlog
entries and are NOT built by this file.

Route: POST /api/feedback
  body: {"message": str, "reply_to": str (optional), "hp_topic": str (honeypot)}
  - Rate limited: 3 sends per hour per source IP — in-memory counter, same
    precedent as mc/blueprints/local_auth.py's login-attempt gate (resets on
    restart; acceptable for a low-stakes anti-spam limit).
  - Honeypot (hp_topic): a field real users never see or fill; if non-empty,
    the route reports success without sending, so a bot filling it in is
    never tipped off that it was caught.
  - The payload sent is exactly `message` + `reply_to` — nothing else is
    appended. The prefilled "Clayrune <version> on <OS>" line lives INSIDE
    `message` itself, generated client-side as visible, editable text
    (privacy constraint, backlog item (c)) — this route never adds it.

TRANSPORT GAP — reported, not hidden (see the build's final report for the
full writeup). Cloudflare Email Routing on clayrune.io (MC-906) is
RECEIVE-only: it forwards hello@clayrune.io to a human mailbox but cannot
itself send. There is today no credential that ships with — or can be
assumed present on — a stranger's install that would let THEIR machine send
mail to hello@clayrune.io. The only proven sender is Ron's own Gmail SMTP
(tools/night-review/send_mail.py + ~/.clayrune/night-mail.json), which is a
personal credential and must never ship or be silently reused here.

So by default, on every install including this one, sending is NOT
CONFIGURED, and this route says so honestly (`mailer_not_configured`)
rather than faking success or silently dropping the message. To actually
send, a human creates a DEDICATED vault secret named 'feedback-mailer'
(kind=password, username=<gmail address>, password=<Gmail App Password>) —
deliberately not auto-wired to night-mail.json, a different, personal
channel. Rate limit and honeypot still run either way, so the shape of a
send failure never depends on whether the sender was abusive.
"""
from __future__ import annotations

import platform
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from flask import Blueprint, jsonify, request

from mc.backup import _clayrune_version
from mc.core import _log
from mc import secrets_store

bp = Blueprint('feedback_routes', __name__)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SEND_MAIL_SCRIPT = REPO_ROOT / 'tools' / 'night-review' / 'send_mail.py'
# Public product contact address — already printed in install.sh/install.ps1
# (MC-905), not an operator-personal address.
FEEDBACK_TO = 'hello@clayrune.io'
FEEDBACK_SECRET_NAME = 'feedback-mailer'

_RATE_LIMIT_MAX = 3
_RATE_LIMIT_WINDOW_S = 3600
_SEND_LOG: dict[str, list[float]] = {}  # ip -> send timestamps within the window

_MAX_MESSAGE_LEN = 8000
_MAX_EMAIL_LEN = 320


def _rate_limited(ip: str) -> bool:
    now = time.monotonic()
    hits = [t for t in _SEND_LOG.get(ip, []) if now - t < _RATE_LIMIT_WINDOW_S]
    if len(hits) >= _RATE_LIMIT_MAX:
        _SEND_LOG[ip] = hits
        return True
    hits.append(now)
    _SEND_LOG[ip] = hits
    return False


def _send(message: str, reply_to: str) -> tuple[bool, str]:
    """Best-effort send via the dedicated feedback-mailer vault secret.

    Returns (ok, error_code). Never raises — every failure path is a clear,
    honest error code, never a fabricated success.
    """
    try:
        user = secrets_store.get_username(FEEDBACK_SECRET_NAME)
        app_password = secrets_store.get_secret_value(
            FEEDBACK_SECRET_NAME, consumer='feedback_routes', unattended=False)
    except secrets_store.VaultLocked:
        return False, 'vault_locked'
    except secrets_store.SecretNotFound:
        return False, 'mailer_not_configured'
    except secrets_store.SecretsError as e:
        _log(f"[feedback] mailer secret unavailable: {e}")
        return False, 'mailer_not_configured'

    # Newlines in reply_to could otherwise inject extra headers into the
    # subject line — strip them; the body (plain message text) is unaffected.
    safe_reply_to = reply_to.replace('\r', ' ').replace('\n', ' ').strip()
    subject = 'Clayrune feedback'
    if safe_reply_to:
        subject += f' (reply-to: {safe_reply_to})'

    import os
    env = dict(os.environ)
    env['NIGHT_MAIL_USER'] = user
    env['NIGHT_MAIL_APP_PASSWORD'] = app_password

    try:
        with tempfile.NamedTemporaryFile(
                mode='w', suffix='.txt', delete=False, encoding='utf-8') as fh:
            fh.write(message)
            body_path = fh.name
        try:
            proc = subprocess.run(
                [sys.executable, str(SEND_MAIL_SCRIPT),
                 '--subject', subject, '--to', FEEDBACK_TO,
                 '--body-file', body_path],
                env=env, capture_output=True, text=True, timeout=30,
                stdin=subprocess.DEVNULL,
            )
        finally:
            try:
                Path(body_path).unlink(missing_ok=True)
            except Exception:
                pass
    except Exception as e:
        _log(f"[feedback] send_mail subprocess failed: {e}")
        return False, 'send_failed'

    if proc.returncode != 0:
        _log(f"[feedback] send_mail exited {proc.returncode}: "
             f"{(proc.stderr or '').strip()[:300]}")
        return False, 'send_failed'
    return True, ''


def _os_label() -> str:
    return f"{platform.system()} {platform.release()}".strip() or 'unknown OS'


@bp.route('/api/feedback/context')
def feedback_context():
    """Version + OS for the modal's prefilled, editable first line — never
    appended server-side to the message itself (privacy constraint (c))."""
    return jsonify({'version': _clayrune_version(), 'os': _os_label()})


@bp.route('/api/feedback', methods=['POST'])
def send_feedback():
    body = request.get_json(silent=True) or {}
    message = (body.get('message') or '').strip()
    reply_to = (body.get('reply_to') or '').strip()
    # Never rendered for a real user (see the JS/CSS side) — anything filling
    # it in is a bot.
    honeypot = (body.get('hp_topic') or '').strip()

    if honeypot:
        _log(f"[feedback] honeypot tripped from {request.remote_addr}")
        return jsonify({'ok': True})

    if not message:
        return jsonify({'ok': False, 'error': 'empty_message'}), 400
    if len(message) > _MAX_MESSAGE_LEN:
        return jsonify({'ok': False, 'error': 'message_too_long',
                        'max': _MAX_MESSAGE_LEN}), 400
    if len(reply_to) > _MAX_EMAIL_LEN:
        return jsonify({'ok': False, 'error': 'email_too_long',
                        'max': _MAX_EMAIL_LEN}), 400

    ip = request.remote_addr or 'unknown'
    if _rate_limited(ip):
        return jsonify({'ok': False, 'error': 'too_many_attempts'}), 429

    ok, err = _send(message, reply_to)
    if not ok:
        return jsonify({'ok': False, 'error': err}), 502
    return jsonify({'ok': True})
