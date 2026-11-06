"""Routes for the daily update-check ping (mc/update_ping.py).

  * GET  /api/update-ping/status  what the dashboard needs: enabled / disclosed /
                                  env_disabled / latest_version / update_available
  * POST /api/update-ping/seen    the disclosure was displayed -> update_ping_disclosed
  * POST /api/update-ping/choice  {"enabled": bool}: the user's answer on the
                                  first-run step or the one-time notice; also
                                  counts as "seen"

Why these are NOT PUT /api/config: that route needs the retyped dashboard
passcode (MC-995), and a fresh install has none, so the first-run step would
raise a "set a passcode" form over a yes/no. Same reasoning and same gate as
POST /api/setup/complete: refuse an unattended agent session, otherwise
write the one flag. The Settings toggle (update_ping_enabled) still goes through
PUT /api/config like every other setting. The endpoint URL is not writable here
or from Settings, only from config.json.
"""
import json

from flask import Blueprint, jsonify, request

from mc import state, update_ping
from mc.atomic_json import write_json_atomic
from mc.core import _log
from mc.unattended import is_unattended_caller

bp = Blueprint('update_ping_routes', __name__)

CONFIG_PATH = None


def wire(*, config_path):
    global CONFIG_PATH
    CONFIG_PATH = config_path


def _persist(updates: dict) -> None:
    """Read-merge-write config.json (as setup_complete does), then update the live dict."""
    try:
        with open(CONFIG_PATH, encoding='utf-8') as f:
            on_disk = json.load(f)
    except Exception:
        on_disk = dict(state.CONFIG)
    on_disk.update(updates)
    write_json_atomic(CONFIG_PATH, on_disk, indent=2, ensure_ascii=False)
    state.CONFIG.update(updates)


def _refuse_unattended():
    if is_unattended_caller():
        return jsonify({'error': 'this action needs a human — an unattended '
                                 'agent session cannot change settings'}), 403
    return None


@bp.route('/api/update-ping/status')
def ping_status():
    return jsonify(update_ping.status())


@bp.route('/api/update-ping/seen', methods=['POST'])
def ping_seen():
    refusal = _refuse_unattended()
    if refusal is not None:
        return refusal
    try:
        _persist({'update_ping_disclosed': True})
    except Exception as e:
        _log(f"[update-ping] failed to persist disclosure: {e}", flush=True)
        return jsonify({'error': f'failed to save config: {e}'}), 500
    return jsonify({'ok': True, **update_ping.status()})


@bp.route('/api/update-ping/choice', methods=['POST'])
def ping_choice():
    refusal = _refuse_unattended()
    if refusal is not None:
        return refusal
    body = request.get_json(silent=True) or {}
    enabled = body.get('enabled')
    if not isinstance(enabled, bool):
        return jsonify({'error': 'enabled must be true or false'}), 400
    try:
        _persist({'update_ping_enabled': enabled, 'update_ping_disclosed': True})
    except Exception as e:
        _log(f"[update-ping] failed to persist choice: {e}", flush=True)
        return jsonify({'error': f'failed to save config: {e}'}), 500
    return jsonify({'ok': True, **update_ping.status()})
