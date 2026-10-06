"""The human gate on the MCP panel's write routes (backlog 34aac480, MC-1053).

An MCP server entry is a command the next agent dispatch runs, so registering one is the same
kind of decision as a vault write: CLAUDE.md "agents use credentials; only humans create them".
Desk Connect already holds its own Save to that rule (`desk_connect_routes.commit_connection`:
refuse an unattended caller, then `_require_human_passcode`). The panel routes in `mcp_routes`
did neither, so an agent could `curl` any command into `~/.claude.json`, and the Desk approval
card was no boundary against agents. This module is the panel's copy of the SAME two checks,
reused rather than reimplemented:

    refuse_unattended(action)      403 for a live non-manual agent session (`mc.unattended`).
                                   First check of every write route, before anything is read.
    require_passcode(data)         the dashboard passcode retyped in the request body
                                   (`secrets_routes._require_human_passcode`; a browser Origin
                                   header alone is forgeable by an agent's curl). For routes that
                                   add or change what runs: POST, PUT, url/install. DELETE and the
                                   loadout PUT only remove or re-select, so they stop at the first.

Both return None to proceed, or a Flask response tuple to return at once. Callers keep Desk's
fixed order: unattended first, the cheap shape checks (a bad request must not cost a passcode
guess), then the passcode, then the write.
"""
from __future__ import annotations

from flask import jsonify

from mc.blueprints.secrets_routes import _require_human_passcode
from mc.unattended import is_unattended_caller


def refuse_unattended(action: str):
    """403 when an unattended agent session could be the caller. `action` completes the
    sentence "an unattended agent session cannot ..."."""
    if is_unattended_caller():
        return jsonify({'error': 'this action needs a human: an unattended agent session '
                                 f'cannot {action}'}), 403
    return None


def require_passcode(data):
    """None when `data['passcode']` is the dashboard passcode; else the 403/429 response."""
    return _require_human_passcode(data)
