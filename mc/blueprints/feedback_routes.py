"""In-app feedback entry point — context endpoint (blueprint).

Backlog f638e8d9 (MC-904), REWORK 2026-09-29 (Ron's call: mailto now, a
Cloudflare Worker send-in-app path later — see position
`position_howastrangerinstalldeliversinappsendfeedbacktohe`). This module
used to also hold a POST /api/feedback send path (SMTP via a dedicated vault
secret, rate limit, honeypot) — removed. There is no credential that ships
with — or can be assumed present on — a stranger's install that would let
their machine send mail, so the client now builds a mailto: link itself
(static/js/feedback-modal.js) and makes no server call to send. Cloudflare
Email Routing on clayrune.io (MC-906) is receive-only regardless.

The one route left is read-only metadata for the modal's prefilled, visible,
editable "Clayrune <version> on <OS>" line (privacy constraint, backlog item
(c)) — this route never sees or appends to the feedback message itself.
"""
from __future__ import annotations

import platform

from flask import Blueprint, jsonify

from mc.backup import _clayrune_version

bp = Blueprint('feedback_routes', __name__)


def _os_label() -> str:
    return f"{platform.system()} {platform.release()}".strip() or 'unknown OS'


@bp.route('/api/feedback/context')
def feedback_context():
    """Version + OS for the modal's prefilled, editable first line — never
    appended server-side to the message itself (privacy constraint (c))."""
    return jsonify({'version': _clayrune_version(), 'os': _os_label()})
