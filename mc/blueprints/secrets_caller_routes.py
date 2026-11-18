"""`GET /api/secrets/caller-unattended`: the server's answer to "am I an attended
chat?" for the process making the request (MC-1179 P10).

`tools/with-secret.py` reads the vault in its own process when the vault is not
passphrase-locked, so it cannot know from inside whether the CLI above it is an
attended chat. It asks here; the server attributes the connecting process
(`mc.secrets_caller_attended`) and answers. The answer is about the caller
only, so it reveals nothing about any other session and needs no token.
Loopback only, and never through the tunnel.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from mc import secrets_caller_attended
from mc.core import _is_loopback_request
from mc.state import agent_sessions, tracked_processes

bp = Blueprint('secrets_caller', __name__)


def _tunneled() -> bool:
    return any(name.lower().startswith('cf-') for name in request.headers.keys())


@bp.route('/api/secrets/caller-unattended')
def caller_unattended():
    if not _is_loopback_request():
        return jsonify({'error': 'loopback_required'}), 403
    if _tunneled():
        return jsonify({'error': 'tunnel_refused'}), 403
    unattended, reason = secrets_caller_attended.caller_unattended(
        request, agent_sessions, tracked_processes)
    return jsonify({'unattended': unattended, 'reason': reason})
