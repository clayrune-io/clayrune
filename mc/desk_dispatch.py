"""Typed Desk chat dispatch admission and durable notification sink.

A turn belongs to one owner (campaign, storyboard or article; see
`mc/desk_dispatch_owner.py`). The research adapter is the only caller that issues
a turn capability. Outside results are appended as data; this module never
invokes a planner for them.
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

from mc import desk_dispatch_owner as owners, state
from mc.core import now_iso

CONFIG_KEY = 'desk_dispatch_enabled'
MAX_DISPATCHES = 2  # One builder and one specialist without an unbounded fan-out.
TOOL = 'mcp__desk__dispatch_agent'
SINK_PREFIX = owners.CAMPAIGN_SINK   # campaign sinks are unchanged; the other owners: desk_dispatch_owner
_current: ContextVar[dict | None] = ContextVar('desk_typed_chat_turn', default=None)
_turns: dict[str, dict] = {}
_lock = threading.RLock()


@contextmanager
def typed_turn(owner: owners.Owner, project: dict, agent: str, user_turn: dict | None = None) -> Iterator[dict]:
    """Only a typed chat route's own call site enters this scope (never its data)."""
    turn = {'owner': owner, 'project_id': project['id'], 'agent': agent,
            'dispatches': [], 'attempts': 0, 'origin': owner.chat_origin, 'user_turn': user_turn,
            'lock': threading.RLock()}
    reset = _current.set(turn)
    try:
        yield turn
    finally:
        _current.reset(reset)


def campaign_turn(campaign_id: str, project: dict, agent: str, user_turn: dict | None = None):
    return typed_turn(owners.Owner('campaign', campaign_id), project, agent, user_turn)


def admission(provider: str, project: dict | None, guard_supported: bool) -> tuple[dict | None, str]:
    """Single adapter guard. No request/model-provided origin or enable flag."""
    turn = _current.get()
    owner = (turn or {}).get('owner')
    if not turn or not owner or turn.get('origin') != owner.chat_origin or turn['project_id'] != (project or {}).get('id') or not _typed_chat_request():
        return None, 'Dispatch is available only while answering a message typed in campaign, storyboard or article chat.'
    if state.CONFIG.get(CONFIG_KEY, True) is False:
        return None, 'Desk agent dispatch is disabled in Settings > Providers > Advanced.'
    if provider != 'claude' or not guard_supported:
        return None, f'{provider} has no certified Desk dispatch tool boundary.'
    return turn, ''


def _typed_chat_request() -> bool:
    """Internal/scheduled entry points and attributed agent POSTs cannot opt in.
    Only the three typed chat POSTs qualify, and only for the owner the turn was
    opened for (the request's own path/body names it, never the model)."""
    if not has_request_context() or request.method != 'POST' or request.endpoint not in owners.ENDPOINTS:
        return False
    turn = _current.get()
    owner = (turn or {}).get('owner')
    if not owner or owners.ENDPOINTS[request.endpoint] != owner.kind or not owners.request_names(owner, request):
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
    from mc import desk
    owner = turn['owner']
    turns = [turn['user_turn']] if turn.get('user_turn') and not turn.get('user_saved') else []
    turns.append({
        'id': 'dispatch-' + card['session_id'], 'role': 'dispatch', 'text': card['task'],
        'at': now_iso(), 'agent': turn['agent'], 'dispatch': card})
    with desk._store_lock:
        store = desk._read_store()
        try:
            rec = owners.record(store, owner)
        except owners.OwnerMissing as exc:
            from mc.desk_pieces import PieceError
            raise PieceError(str(exc), 404) from exc
        rec.setdefault('dispatch_sessions', {})[card['session_id']] = dict(card)
        owners.append_turns(rec, owner, turns)
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
            return jsonify({'error': 'This chat turn is closed or unavailable.'}), 403
        owner = turn['owner']
        with turn['lock']:
            with _lock:
                active = _turns.get(token) is turn
            if not active:
                return jsonify({'error': 'This chat turn is closed or unavailable.'}), 403
            if state.CONFIG.get(CONFIG_KEY, True) is False:
                return jsonify({'error': 'Desk agent dispatch is disabled in Settings.'}), 403
            project = load_project(turn['project_id'])
            if not project:
                return jsonify({'error': 'Project is unavailable.'}), 404
            from mc import desk
            with desk._store_lock:
                try:
                    rec = owners.record(desk._read_store(), owner)
                except owners.OwnerMissing:
                    return jsonify({'error': f'The Desk {owner.kind} is unavailable; send a new message.'}), 404
                if owner.project_bound and rec.get('project_id') != project['id']:
                    return jsonify({'error': f'The {owner.kind} project changed; send a new message.'}), 409
            body = request.get_json(silent=True) or {}
            if not isinstance(body, dict):
                return jsonify({'error': 'Dispatch input must be an object.'}), 400
            target, task = body.get('agent'), body.get('task')
            if not isinstance(target, str) or target not in roster(project):
                return jsonify({'error': 'Target is unknown or not hired on this project Floor.'}), 400
            from mc import desk_story
            from mc.desk_pieces import PieceError
            try:
                desk_story._agent(target, project)
            except PieceError as exc:
                return jsonify({'error': str(exc)}), 400
            if not isinstance(task, str) or not task.strip() or len(task) > 12000:
                return jsonify({'error': 'Task must contain 1 to 12000 characters.'}), 400
            if turn['attempts'] >= MAX_DISPATCHES:
                return jsonify({'error': f'At most {MAX_DISPATCHES} dispatch attempts per chat turn.'}), 429
            # Reserve before launch. An ambiguous launch is never retried by this tool.
            turn['attempts'] += 1
            task = f"Origin: {owner.origin_label}, agent {turn['agent']}\n\n{task.strip()}"
            try:
                result = _post_dispatch(project['id'], {
                    'task': task, 'character': target, 'source': 'agent',
                    'notify_session': owner.sink})
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
    if not owners.is_desk_sink(sink):
        return False
    from mc import desk
    from mc.delegation_delivery import DeliveryBlocked
    owner = owners.from_sink(sink)
    if owner is None:
        raise DeliveryBlocked('Desk notification sink is malformed')
    event_id = row['event_id']
    tid = 'result-' + hashlib.sha256(event_id.encode()).hexdigest()[:24]
    with desk._store_lock:
        store = desk._read_store()
        try:
            rec = owners.record(store, owner)
        except owners.OwnerMissing as exc:
            raise DeliveryBlocked(f'{exc}') from exc
        sessions = rec.get('dispatch_sessions') or {}
        stored = sessions.get(payload.get('child_session_id') or event_id.rsplit(':turn:', 1)[0])
        # A storyboard record names no project, so its saved card is the project it launched under.
        known = rec.get('project_id') if owner.project_bound else (stored or {}).get('project_id', row['project_id'])
        if known != row['project_id']:
            raise DeliveryBlocked(f'Desk {owner.kind} is missing or its project changed')
        receipts = rec.setdefault('dispatch_receipts', [])
        if event_id in receipts:
            return True
        new_turns = []
        child = payload.get('child_session_id') or event_id.rsplit(':turn:', 1)[0]
        if child not in sessions:
            # The existing route commits its notify-bearing launch row before
            # Popen. Recover the card if we crashed after launch, before saving it.
            from mc.blueprints import agent_routes
            entry = agent_routes.agent_sessions.get(child)
            if not entry:
                entry = next((e for e in agent_routes._load_agent_log(row['project_id'])
                              if e.get('session_id') == child), None)
            origin = f'Origin: {owner.origin_label}, agent '
            if not entry or (entry.get('_notify_session') or entry.get('spawned_by_session_id')) != sink or not entry.get('task', '').startswith(origin):
                from mc.delegation_delivery import DeliveryDeferred
                raise DeliveryDeferred('Desk dispatch launch record is not available yet')
            character = entry.get('character') or {}
            target = (f"{character.get('scope', 'global')}:{character.get('name', '')}"
                      if isinstance(character, dict) else str(character))
            card = {'session_id': child, 'project_id': row['project_id'], 'agent': target,
                    'task': entry['task'].partition('\n\n')[2], 'status': 'dispatched'}
            rec.setdefault('dispatch_sessions', {})[child] = card
            new_turns.append({'id': 'dispatch-' + child, 'role': 'dispatch', 'text': card['task'],
                              'agent': entry['task'].split('\n', 1)[0][len(origin):], 'at': now_iso(), 'dispatch': card})
        new_turns.append({'id': tid, 'role': 'dispatch_result', 'at': now_iso(),
                          'agent': payload.get('who') or child, 'text': str(payload.get('summary') or ''),
                          'dispatch': {'session_id': child, 'project_id': row['project_id'],
                                       'status': payload.get('status') or 'finished'}})
        receipts.append(event_id)
        owners.append_turns(rec, owner, new_turns)
        desk._write_store(store)
    return True
