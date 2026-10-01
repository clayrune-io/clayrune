"""Pop-out chat window slot registry — /api/popout/*.

The chat header's "Pop Out" button opens a conversation in its own browser /
app window (static/js/modal-manager.js, `popOutChat`). Every popped window is a
full SPA instance on the same origin, and Chromium allows 6 HTTP/1.1
connections per origin: the main dashboard keeps its own SSE streams, each
popped window keeps one more. Unbounded pop-outs would starve the `/api/*`
fetches (docs: memory `arch_sse_slot_management`), so concurrent pop-outs are
capped at POPOUT_MAX.

The count lives on the SERVER, not in localStorage or window handles, because
neither survives the realities of the clients: a frozen Mac app's pywebview
windows do not share web storage, and a dashboard reload forgets every handle
it held. One in-memory registry is the only view all of them agree on.

A slot is keyed by session id (one window per conversation) and held by
heartbeat: the popped window re-claims every few seconds and releases on
`pagehide`; a window that dies without releasing (crash, killed app) ages out
after POPOUT_TTL_SEC. Nothing here is persisted — a server restart clears the
registry and the live windows re-claim on their next heartbeat.
"""

import threading
import time

from flask import Blueprint, jsonify, request

bp = Blueprint('popout', __name__)

POPOUT_MAX = 4
POPOUT_TTL_SEC = 90

_lock = threading.Lock()
_slots = {}  # session_id -> last claim (monotonic seconds)


def _purge(now):
    for sid in [s for s, t in _slots.items() if now - t > POPOUT_TTL_SEC]:
        del _slots[sid]


def _claim(session_id):
    """Take or refresh the slot for `session_id`. Returns (ok, existing, count)."""
    now = time.monotonic()
    with _lock:
        _purge(now)
        existing = session_id in _slots
        if not existing and len(_slots) >= POPOUT_MAX:
            return False, False, len(_slots)
        _slots[session_id] = now
        return True, existing, len(_slots)


def _release(session_id):
    with _lock:
        _slots.pop(session_id, None)
        return len(_slots)


def _reset_for_tests():
    with _lock:
        _slots.clear()


def _session_id_from_request():
    body = request.get_json(force=True, silent=True)
    if not isinstance(body, dict):
        return ''
    sid = body.get('session_id')
    return sid.strip() if isinstance(sid, str) else ''


@bp.route('/api/popout/claim', methods=['POST'])
def popout_claim():
    """Reserve (or refresh) a pop-out slot for a conversation.

    Body: {"session_id": "..."}. 200 {ok, existing, count, max}; `existing` is
    true when that conversation already holds a slot — its window is open, so
    the caller should focus it rather than open another. 409 when POPOUT_MAX
    other conversations are already popped out.
    """
    sid = _session_id_from_request()
    if not sid:
        return jsonify({'ok': False, 'error': 'session_id required'}), 400
    ok, existing, count = _claim(sid)
    if not ok:
        return jsonify({
            'ok': False, 'count': count, 'max': POPOUT_MAX,
            'message': (f'{POPOUT_MAX} conversations are already popped out. '
                        'Close one of those windows to pop out another.'),
        }), 409
    return jsonify({'ok': True, 'existing': existing, 'count': count,
                    'max': POPOUT_MAX})


@bp.route('/api/popout/release', methods=['POST'])
def popout_release():
    """Give a slot back (the popped window closed). Idempotent."""
    sid = _session_id_from_request()
    if not sid:
        return jsonify({'ok': False, 'error': 'session_id required'}), 400
    return jsonify({'ok': True, 'count': _release(sid), 'max': POPOUT_MAX})
