"""Campaign-chat dispatch admission and durable campaign notification sink.

The research adapter is the only caller that issues a turn capability. Outside
results are appended as data; this module never invokes a planner for them.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
import os
import secrets
import threading
from typing import Callable, Iterator
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from flask import has_request_context, jsonify, request

from mc import state
from mc.core import now_iso

CONFIG_KEY = 'desk_dispatch_enabled'
MAX_DISPATCHES = 2  # One builder and one specialist without an unbounded fan-out.
TOOL = 'mcp__desk__dispatch_agent'
SINK_PREFIX = 'desk-campaign:'
_current: ContextVar[dict | None] = ContextVar('desk_campaign_turn', default=None)
_turns: dict[str, dict] = {}
_lock = threading.RLock()


@contextmanager
def campaign_turn(campaign_id: str, project: dict, agent: str, user_turn: dict | None = None) -> Iterator[dict]:
    """Only the typed campaign-chat call site enters this scope (never its data)."""
    turn = {'campaign_id': campaign_id, 'project_id': project['id'], 'agent': agent,
            'dispatches': [], 'attempts': 0, 'origin': 'campaign_chat', 'user_turn': user_turn,
            'lock': threading.RLock()}
    reset = _current.set(turn)
    try:
        yield turn
    finally:
        _current.reset(reset)


def admission(provider: str, project: dict | None, guard_supported: bool) -> tuple[dict | None, str]:
    """Single adapter guard. No request/model-provided origin or enable flag."""
    turn = _current.get()
    if not turn or turn.get('origin') != 'campaign_chat' or turn['project_id'] != (project or {}).get('id') or not _typed_chat_request():
        return None, 'Dispatch is available only while answering a message typed in campaign chat.'
    if state.CONFIG.get(CONFIG_KEY, True) is False:
        return None, 'Desk agent dispatch is disabled in Settings > Providers > Advanced.'
    if provider != 'claude' or not guard_supported:
        return None, f'{provider} has no certified Desk dispatch tool boundary.'
    return turn, ''


def _typed_chat_request() -> bool:
    """Internal/scheduled entry points and attributed agent POSTs cannot opt in."""
    if not has_request_context() or request.method != 'POST' or request.endpoint != 'desk_routes.post_campaign_chat':
        return False
    from mc import dispatch_lineage
    from mc.blueprints import agent_routes
    from mc.unattended import is_unattended_caller
    return not (dispatch_lineage.attributed_caller_session(
        request, agent_routes.agent_sessions, agent_routes.tracked_processes) or is_unattended_caller())


@contextmanager
def tool_session(turn: dict | None) -> Iterator[str]:
    if turn is None:
        yield ''
        return
    token = secrets.token_urlsafe(32)
    with _lock:
        _turns[token] = turn
    try:
        yield token
    finally:
        with _lock:
            _turns.pop(token, None)


def roster(project: dict) -> list[str]:
    return [r['character'] for r in project.get('roster', [])
            if isinstance(r, dict) and isinstance(r.get('character'), str) and not r.get('removed_at')]


def _append_dispatch(turn: dict, card: dict) -> None:
    from mc import desk_campaign_chat as chat
    from mc import desk
    turns = [turn['user_turn']] if turn.get('user_turn') and not turn.get('user_saved') else []
    turns.append({
        'id': 'dispatch-' + card['session_id'], 'role': 'dispatch', 'text': card['task'],
        'at': now_iso(), 'agent': turn['agent'], 'dispatch': card})
    with desk._store_lock:
        store = desk._read_store()
        camp = chat._campaign(store, turn['campaign_id'])
        camp.setdefault('dispatch_sessions', {})[card['session_id']] = dict(card)
        camp['chat'] = ([t for t in camp.get('chat', []) if isinstance(t, dict)] + turns)[-chat.MAX_TURNS_KEPT:]
        desk._write_store(store)
    turn['user_saved'] = True


def _post_dispatch(project_id: str, body: dict) -> dict:
    """One ordinary loopback POST, never replayed on an ambiguous outcome."""
    from urllib.parse import quote
    port = int(os.environ.get('MC_PORT') or state.CONFIG.get('port') or 5199)
    req = Request(f'http://127.0.0.1:{port}/api/project/{quote(project_id, safe="")}/agent/dispatch',
                  method='POST', data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'})
    try:
        with urlopen(req, timeout=90) as response:
            return json.loads(response.read())
    except HTTPError as exc:
        try:
            detail = json.loads(exc.read()).get('error') or f'HTTP {exc.code}'
        except (ValueError, AttributeError):
            detail = f'HTTP {exc.code}'
        raise RuntimeError(f'{detail}; check the Floor before retrying') from exc
    except Exception as exc:
        raise RuntimeError('Dispatch outcome unknown; check the Floor and do not retry automatically') from exc


def register(bp, load_project: Callable) -> None:
    @bp.route('/api/desk/dispatch-tool', methods=['POST'])
    def dispatch_tool():
        token = request.headers.get('X-Desk-Turn', '')
        with _lock:
            turn = _turns.get(token)
        if not turn:
            return jsonify({'error': 'This campaign-chat turn is closed or unavailable.'}), 403
        with turn['lock']:
            with _lock:
                active = _turns.get(token) is turn
            if not active:
                return jsonify({'error': 'This campaign-chat turn is closed or unavailable.'}), 403
            if state.CONFIG.get(CONFIG_KEY, True) is False:
                return jsonify({'error': 'Desk agent dispatch is disabled in Settings.'}), 403
            project = load_project(turn['project_id'])
            if not project:
                return jsonify({'error': 'Campaign project is unavailable.'}), 404
            from mc import desk_campaign_chat as chat
            from mc import desk
            with desk._store_lock:
                camp = chat._campaign(desk._read_store(), turn['campaign_id'])
                if camp.get('project_id') != project['id']:
                    return jsonify({'error': 'Campaign project changed; send a new message.'}), 409
            body = request.get_json(silent=True) or {}
            if not isinstance(body, dict):
                return jsonify({'error': 'Dispatch input must be an object.'}), 400
            target, task = body.get('agent'), body.get('task')
            if not isinstance(target, str) or target not in roster(project):
                return jsonify({'error': 'Target is unknown or not hired on this project Floor.'}), 400
            from mc import desk_story
            try:
                desk_story._agent(target, project)
            except chat.PieceError as exc:
                return jsonify({'error': str(exc)}), 400
            if not isinstance(task, str) or not task.strip() or len(task) > 12000:
                return jsonify({'error': 'Task must contain 1 to 12000 characters.'}), 400
            if turn['attempts'] >= MAX_DISPATCHES:
                return jsonify({'error': f'At most {MAX_DISPATCHES} dispatch attempts per chat turn.'}), 429
            # Reserve before launch. An ambiguous launch is never retried by this tool.
            turn['attempts'] += 1
            task = f"Origin: Desk campaign {turn['campaign_id']}, agent {turn['agent']}\n\n{task.strip()}"
            try:
                result = _post_dispatch(project['id'], {
                    'task': task, 'character': target, 'source': 'agent',
                    'notify_session': SINK_PREFIX + turn['campaign_id']})
            except RuntimeError as exc:
                return jsonify({'error': str(exc)}), 502
            if not result.get('session_id'):
                return jsonify({'error': result.get('error') or 'Dispatch outcome unknown; do not retry.'}), 502
            card = {'session_id': result['session_id'], 'project_id': project['id'],
                    'agent': target, 'task': task.split('\n\n', 1)[1], 'status': 'dispatched'}
            turn['dispatches'].append(card)
            _append_dispatch(turn, card)
            return jsonify(card)


def deliver(row: dict, payload: dict) -> bool:
    """Existing durable notify inbox calls this sink, idempotently, on completion."""
    sink = row.get('parent_session_id', '')
    if not sink.startswith(SINK_PREFIX):
        return False
    from mc import desk, desk_campaign_chat as chat
    from mc.delegation_delivery import DeliveryBlocked
    cid = sink[len(SINK_PREFIX):]
    event_id = row['event_id']
    tid = 'result-' + hashlib.sha256(event_id.encode()).hexdigest()[:24]
    with desk._store_lock:
        store = desk._read_store()
        camp = (store.get('campaigns') or {}).get(cid)
        if not isinstance(camp, dict) or camp.get('project_id') != row['project_id']:
            raise DeliveryBlocked('Desk campaign is missing or its project changed')
        thread = camp.setdefault('chat', [])
        receipts = camp.setdefault('dispatch_receipts', [])
        if event_id in receipts:
            return True
        child = payload.get('child_session_id') or event_id.rsplit(':turn:', 1)[0]
        if child not in camp.get('dispatch_sessions', {}):
            # The existing route commits its notify-bearing launch row before
            # Popen. Recover the card if we crashed after launch, before saving it.
            from mc.blueprints import agent_routes
            entry = agent_routes.agent_sessions.get(child)
            if not entry:
                entry = next((e for e in agent_routes._load_agent_log(row['project_id'])
                              if e.get('session_id') == child), None)
            origin = f'Origin: Desk campaign {cid}, agent '
            if not entry or (entry.get('_notify_session') or entry.get('spawned_by_session_id')) != sink or not entry.get('task', '').startswith(origin):
                from mc.delegation_delivery import DeliveryDeferred
                raise DeliveryDeferred('Desk dispatch launch record is not available yet')
            character = entry.get('character') or {}
            target = (f"{character.get('scope', 'global')}:{character.get('name', '')}"
                      if isinstance(character, dict) else str(character))
            card = {'session_id': child, 'project_id': row['project_id'], 'agent': target,
                    'task': entry['task'].partition('\n\n')[2], 'status': 'dispatched'}
            camp.setdefault('dispatch_sessions', {})[child] = card
            thread.append({'id': 'dispatch-' + child, 'role': 'dispatch', 'text': card['task'],
                           'agent': entry['task'].split('\n', 1)[0][len(origin):], 'at': now_iso(), 'dispatch': card})
        thread.append({'id': tid, 'role': 'dispatch_result', 'at': now_iso(),
                       'agent': payload.get('who') or child, 'text': str(payload.get('summary') or ''),
                       'dispatch': {'session_id': child, 'project_id': row['project_id'],
                                    'status': payload.get('status') or 'finished'}})
        receipts.append(event_id)
        camp['chat'] = thread[-chat.MAX_TURNS_KEPT:]
        desk._write_store(store)
    return True
