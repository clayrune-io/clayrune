"""The Floor — one call that answers "who is doing what, everywhere".

Design: `docs/AGENT_FLOOR_DESIGN.md`, phase 1. Today the only way to learn what
is running across twenty projects is to open twenty modals, and a session that
has been idle for twenty hours is invisible until you happen to look.

WHY THIS IS ONE ENDPOINT AND NOT N. `/api/project/<id>/agent/status` is
per-project by design — the chat modal wants one project's sessions with their
full log tails. A cross-project view built on it is twenty HTTP calls carrying
twenty log buffers to render twenty one-line cards, on a 30s poll. This walks
the same in-memory `agent_sessions` map once and returns only what a card shows.

WHAT A FIGURE IS. A *session*, never a type — the Frame 2 caption in the design.
One character can appear in several rooms at once because it is running in
several places; that is the fact the header pill cannot express and the reason
this view exists. The bench is the inverse: types with nothing running anywhere.

Read-only. It starts nothing, stops nothing, and touches no state.
"""
from typing import Any, Callable

from flask import Blueprint, jsonify

from mc import state
from mc.core import _log, time_ago

bp = Blueprint('floor_routes', __name__)

# Wired by server.py — this module must not import agent_routes/project_routes
# (they import each other's families through wire() slots; see server.py's
# import order stanza).
agent_sessions: dict = {}
load_projects: Callable[[], list] = None  # type: ignore[assignment]
list_characters: Callable[..., Any] = None  # type: ignore[assignment]

# A card shows one line of task. The full string is in the chat.
_TASK_CHARS = 90


def wire(*, agent_sessions_ref, load_projects_fn, list_characters_fn):
    global agent_sessions, load_projects, list_characters
    agent_sessions = agent_sessions_ref
    load_projects = load_projects_fn
    list_characters = list_characters_fn


def _figure_state(s):
    """asking > working > idle — the same priority the project tiles use.

    Kept identical to `_project_live_agent` on purpose: two surfaces disagreeing
    about whether a project needs you is worse than either being wrong, because
    you stop trusting both.
    """
    if s.get('waiting_for_plan_approval'):
        return 'asking', 'plan'
    if s.get('waiting_for_question'):
        return 'asking', 'question'
    if s.get('status') == 'running':
        return 'working', None
    return 'idle', None


def _character_of(s):
    """{name, display} for the figure, or None when nobody was hired.

    An unnamed session is NOT given the configured default name here. On the
    Floor "no type" is information — it is what the design's Frame 1 is showing
    when it says nobody has a name yet — and quietly labelling every anonymous
    session "Vector" would hide exactly the gap the view exists to make visible.
    """
    ch = s.get('character')
    if not isinstance(ch, dict):
        return None
    name = (ch.get('agent_name') or ch.get('display_name')
            or ch.get('name') or '').strip()
    return {'name': ch.get('name') or '', 'display': name} if name else None


def _figure(s):
    st, reason = _figure_state(s)
    ch = _character_of(s)
    return {
        'session_id': s.get('session_id', ''),
        'claude_session_id': s.get('claude_session_id', ''),
        'state': st,
        'reason': reason,
        # 'thinking' | 'writing' | 'tool' | '' — only present while a turn is
        # actually streaming, and only when activity_states_enabled is on.
        'activity': s.get('activity_state', '') if st == 'working' else '',
        'task': (s.get('task') or '').strip()[:_TASK_CHARS],
        'character': ch,
        'provider': s.get('provider') or 'claude',
        'model': s.get('model') or s.get('agent_model') or '',
        'started_at': s.get('started_at', ''),
        # The corner age. A forgotten twenty-hour session is invisible today
        # unless you open its modal; this is the whole reason it is on the card.
        'age': time_ago(s.get('started_at')) if s.get('started_at') else '',
        'trigger_type': s.get('trigger_type', 'manual'),
        'hivemind_id': s.get('hivemind_id', ''),
    }


def _live_sessions():
    """Sessions worth drawing, grouped by project.

    Housekeeping and incognito are excluded for the same reason
    `_project_live_agent` excludes them: incognito's whole promise is that it
    does not show up on the public indicators, and a cross-project board is the
    most public indicator there is.
    """
    rooms: dict = {}
    for s in agent_sessions.values():
        if s.get('housekeeping') or s.get('incognito'):
            continue
        if s.get('status') not in ('running', 'idle'):
            continue
        pid = s.get('project_id')
        if not pid:
            continue
        rooms.setdefault(pid, []).append(_figure(s))
    order = {'asking': 0, 'working': 1, 'idle': 2}
    for figs in rooms.values():
        figs.sort(key=lambda f: (order.get(f['state'], 3),
                                 f.get('started_at') or ''))
    return rooms


@bp.route('/api/floor')
def floor():
    """Rooms with something live, the quiet ones by name only, and the bench."""
    rooms = _live_sessions()
    live, quiet = [], []
    for p in (load_projects() or []):
        pid = p.get('id')
        card = {'id': pid, 'name': p.get('name') or pid,
                'emoji': p.get('emoji') or '', 'color': p.get('modal_color') or ''}
        figs = rooms.get(pid)
        if figs:
            live.append({**card, 'figures': figs})
        else:
            quiet.append(card)

    # A room with someone waiting on you comes first: the board's job is to put
    # the thing that needs a human at the top, not to render an alphabet.
    def room_rank(r):
        states = {f['state'] for f in r['figures']}
        return (0 if 'asking' in states else 1 if 'working' in states else 2,
                (r['name'] or '').lower())
    live.sort(key=room_rank)
    quiet.sort(key=lambda r: (r['name'] or '').lower())

    # The bench: hired types with nothing running anywhere. Keyed on the
    # character's file name, which is its identity — `agent_name` is what it
    # calls itself and two characters may pick the same one.
    running = {(f['character'] or {}).get('name')
               for r in live for f in r['figures']}
    bench = []
    try:
        # Global pool only. A project-scoped character is not dispatchable
        # anywhere, so putting it on a cross-project bench would offer a click
        # that cannot be honoured; it belongs in that project's own roster.
        for c in (list_characters() or []):
            if c.get('name') in running:
                continue
            eng = c.get('engine') or {}
            bench.append({'name': c.get('name'), 'scope': c.get('scope'),
                          'display': (c.get('agent_name')
                                      or c.get('display_name') or c.get('name')),
                          'description': (c.get('description') or '')[:80],
                          'provider': eng.get('provider') or '',
                          'model': eng.get('model') or '',
                          'effort': eng.get('effort') or ''})
    except Exception as e:
        _log(f'[floor] bench unavailable: {e}')
        bench = []
    bench.sort(key=lambda c: (c.get('scope') or '', (c.get('display') or '').lower()))

    n_fig = sum(len(r['figures']) for r in live)
    return jsonify({
        'rooms': live, 'quiet': quiet, 'bench': bench,
        'counts': {'rooms': len(live), 'figures': n_fig, 'quiet': len(quiet),
                   'bench': len(bench)},
        # The card renders 'thinking'/'writing' only when the server is actually
        # streaming partial messages. Told once here rather than guessed per
        # card, so the UI can say "activity states are off" instead of showing
        # every figure as blank.
        'activity_states': bool(state.CONFIG.get('activity_states_enabled', False)),
        'poll_seconds': 30,
    })
