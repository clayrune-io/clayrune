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
this view exists. The bench is the whole roster, marked with where each type
is already working — never filtered by it, or a busy type would have nowhere
to be placed or edited from.

Read-only. It starts nothing, stops nothing, and touches no state.
"""
import json
import threading
from pathlib import Path
from typing import Any, Callable

from flask import Blueprint, jsonify, request

from mc import state
from mc.characters import MAX_EMOJI_LEN, clean_avatar
from mc.core import _atomic_write_text, _log, time_ago

bp = Blueprint('floor_routes', __name__)

# Wired by server.py — this module must not import agent_routes/project_routes
# (they import each other's families through wire() slots; see server.py's
# import order stanza).
agent_sessions: dict = {}
load_projects: Callable[[], list] = None  # type: ignore[assignment]
list_characters: Callable[..., Any] = None  # type: ignore[assignment]

# A card shows a couple of lines of task. The full string is in the chat.
_TASK_CHARS = 110
# What a type is FOR is the question the bench answers, so it gets room to say
# it. Still bounded — the card is a summary, and the full text is in the editor.
_DESC_CHARS = 130


def _clip(text, n):
    """Cut at a word boundary and mark it, or return the whole thing untouched.

    A hard slice lands mid-word and the card reads as broken rather than
    abbreviated, which is worse than showing less: the reader stops to work out
    whether something is missing. Falls back to a hard cut only when there is no
    space to break on (one very long token).
    """
    t = ' '.join(str(text or '').split())
    if len(t) <= n:
        return t
    cut = t[:n].rsplit(' ', 1)[0]
    return (cut if len(cut) >= n * 0.6 else t[:n]).rstrip(' ,.;:—-') + '…'
# A name is a name, not a sentence.
_NAME_CHARS = 32
# The avatar rule lives in ONE place — `characters.clean_avatar`. This module
# used to keep its own hand-copied `= 8` "mirroring characters.MAX_AVATAR_LEN";
# when that grew to hold `fig:<name>` the copy stayed behind and served
# `fig:guar` for `fig:guard`, a broken image on every card with the correct
# value sitting untouched in the character file. Two caps cannot mirror each
# other by comment, so there is only one.
_AVATAR_CHARS = MAX_EMOJI_LEN

# Per-session name overrides. OUTSIDE DATA_DIR on purpose: `load_projects()`
# treats every *.json under data/projects/ as a project, and a stray one becomes
# a malformed record that 500s both restart endpoints (the load-bearing rule in
# CLAUDE.md). Keyed by session id so a name survives the revival that rebuilds
# sessions from the agent log.
LABELS_PATH: Path = None  # type: ignore[assignment]
_labels_lock = threading.Lock()


def wire(*, agent_sessions_ref, load_projects_fn, list_characters_fn,
         labels_path=None):
    global agent_sessions, load_projects, list_characters, LABELS_PATH
    agent_sessions = agent_sessions_ref
    load_projects = load_projects_fn
    list_characters = list_characters_fn
    LABELS_PATH = labels_path


def _clean_name(v):
    return ' '.join(str(v or '').split())[:_NAME_CHARS]


def _clean_avatar(v):
    return clean_avatar(v)


def read_labels():
    """{session_id: {name, by}}. Never raises — an unreadable file reads empty."""
    try:
        if not LABELS_PATH or not Path(LABELS_PATH).is_file():
            return {}
        d = json.loads(Path(LABELS_PATH).read_text(encoding='utf-8'))
        return d if isinstance(d, dict) else {}
    except Exception as e:
        _log(f'[floor] labels unreadable: {e}')
        return {}


def set_label(session_id, name=None, by='user', avatar=None):
    """Name a figure and/or give it a face. `''` clears a field; None leaves it.

    `by` is kept because the two paths mean different things: a name the agent
    chose for itself is a statement, and one Ron typed is an instruction. The
    card says which, so a self-chosen name never reads as a decision he made.

    Absent-vs-empty matters here for the same reason it does on a character
    file: this record is rewritten whole, so a caller that only sets an avatar
    must not thereby delete the name.
    """
    with _labels_lock:
        labels = read_labels()
        cur = labels.get(session_id) or {}
        rec = {'name': cur.get('name', ''), 'avatar': cur.get('avatar', ''),
               'by': cur.get('by') or by}
        if name is not None:
            rec['name'] = _clean_name(name)
            rec['by'] = by
        if avatar is not None:
            rec['avatar'] = _clean_avatar(avatar)
            rec['by'] = by
        if rec['name'] or rec['avatar']:
            labels[session_id] = rec
        else:
            labels.pop(session_id, None)
        try:
            Path(LABELS_PATH).parent.mkdir(parents=True, exist_ok=True)
            _atomic_write_text(Path(LABELS_PATH),
                               json.dumps(labels, indent=2, sort_keys=True) + '\n')
        except Exception as e:
            _log(f'[floor] could not persist label for {session_id}: {e}')
    # The live session carries it too, so the next poll reflects it even if the
    # write failed — a name that vanishes on save looks like the click missed.
    sess = (agent_sessions or {}).get(session_id)
    if isinstance(sess, dict):
        saved = labels.get(session_id) or {}
        for key, val in (('floor_label', saved.get('name')),
                         ('floor_avatar', saved.get('avatar'))):
            if val:
                sess[key] = val
            else:
                sess.pop(key, None)
        if saved:
            sess['floor_label_by'] = saved.get('by') or by
        else:
            sess.pop('floor_label_by', None)
    return labels.get(session_id)


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
    """The TYPE this session was hired as, or None when it was hired as nothing.

    Deliberately not a name. "no type" stays visible on the card because it is
    what Frame 1 exists to show — but it is a statement about the ROLE, and it
    was previously standing in for the name as well, which put the board at odds
    with the prompt: the same session was "no type" here and "Your name is
    Vector" there. See `_figure_name`.
    """
    ch = s.get('character')
    if not isinstance(ch, dict):
        return None
    name = (ch.get('agent_name') or ch.get('display_name')
            or ch.get('name') or '').strip()
    return {'name': ch.get('name') or '', 'display': name,
            'scope': ch.get('scope') or ''} if name else None


def _figure_name(s, labels):
    """(name, source) — who this figure is, and where the name came from.

    Precedence is the same one the prompt uses, with an override on top:
    an explicit label beats the persona's self-chosen name, which beats the
    configured default. Anything else and the board would call a session
    something its own system prompt never told it.

    A DELEGATED session (source='agent') with no persona is the exception
    (MC-925): its own prompt is told "you appear as unnamed", not the
    project's default name — see `_build_agent_context`'s `_delegated_unnamed`
    — so falling through to the default here would make the card disagree
    with the very prompt this docstring says it has to match.
    """
    lab = labels.get(s.get('session_id')) or {}
    if isinstance(lab, dict) and lab.get('name'):
        return _clean_name(lab['name']), (lab.get('by') or 'user')
    if s.get('floor_label'):
        return _clean_name(s['floor_label']), (s.get('floor_label_by') or 'user')
    ch = s.get('character')
    if isinstance(ch, dict):
        own = (ch.get('agent_name') or '').strip()
        if own:
            return own, 'character'
    if (s.get('source') or '') == 'agent':
        return 'unnamed', 'unnamed'
    return _clean_name(state.CONFIG.get('agent_name', '')), 'default'


def _figure_avatar(s, labels):
    """The face, resolved in lockstep with `_figure_name`: explicit override,
    then the type's own, then the configured default agent's.

    The fall-through order MUST match the name's, or a card shows the default
    NAME beside a placeholder face — one field claiming this is Vector and the
    one next to it saying nobody is home. The default (`agent_avatar`) exists
    for the same reason `agent_name` does: a hired type carries its face in its
    own file and keeps it forever, while the default agent had no such slot, so
    the only way to give it one was a Floor label keyed on the session id —
    which dies with the session.

    Still no INVENTED default. A delegated session with no persona gets nothing
    (MC-925: its own prompt is told it appears unnamed), and an install that has
    set no face gets the neutral placeholder — absence stays a finding.
    """
    lab = labels.get(s.get('session_id')) or {}
    if isinstance(lab, dict) and lab.get('avatar'):
        return _clean_avatar(lab['avatar'])
    if s.get('floor_avatar'):
        return _clean_avatar(s['floor_avatar'])
    ch = s.get('character')
    if isinstance(ch, dict):
        own = _clean_avatar(ch.get('avatar'))
        if own:
            return own
    if (s.get('source') or '') == 'agent':
        return ''
    return _clean_avatar(state.CONFIG.get('agent_avatar', ''))


def _figure_model(s, proj_default):
    """The engine string, resolved the way the chat header resolves it.

    A session dispatched before `agent_model` was captured per-dispatch carries
    nothing, and the header falls back to the project default — so reading the
    session dict flat renders a blank pill next to a header that shows a model,
    for the same session. The fallback is claude-ONLY: a project's `agent_model`
    is always a claude id, and applying it to a codex/gemini run would make the
    card claim "codex · claude-opus-5" for a spawn that received no --model.
    Returns (model_id, source) where source is 'own' when the session was
    dispatched with this model and 'project' when it is only inheriting the
    project default. The Floor needs the difference: a figure showing Opus
    because its persona pins Opus and a figure showing Opus because nobody
    chose anything are two different facts, and the board rendered them
    identically (Ron, 2026-09-01).
    """
    own = s.get('model') or s.get('agent_model') or ''
    if own:
        return own, 'own'
    if (s.get('provider') or 'claude') == 'claude' and proj_default:
        return proj_default, 'project'
    return '', ''


def _figure(s, proj_default='', labels=None):
    _fig_model, _fig_model_from = _figure_model(s, proj_default)
    st, reason = _figure_state(s)
    ch = _character_of(s)
    name, name_from = _figure_name(s, labels or {})
    return {
        'session_id': s.get('session_id', ''),
        'claude_session_id': s.get('claude_session_id', ''),
        'state': st,
        'reason': reason,
        # 'thinking' | 'writing' | 'tool' | '' — only present while a turn is
        # actually streaming, and only when activity_states_enabled is on.
        'activity': s.get('activity_state', '') if st == 'working' else '',
        'task': _clip(s.get('task'), _TASK_CHARS),
        'character': ch,
        # Who this figure IS, always populated. `name_from` is 'user' | 'self' |
        # 'character' | 'default', so the card can show a chosen name
        # differently from an inherited one.
        'name': name,
        'name_from': name_from,
        'avatar': _figure_avatar(s, labels or {}),
        'provider': s.get('provider') or 'claude',
        # 'own' (dispatched with it) vs 'project' (merely inheriting the
        # default) — see _figure_model.
        'model': _fig_model,
        'model_from': _fig_model_from,
        'started_at': s.get('started_at', ''),
        # The corner age. A forgotten twenty-hour session is invisible today
        # unless you open its modal; this is the whole reason it is on the card.
        'age': time_ago(s.get('started_at')) if s.get('started_at') else '',
        'trigger_type': s.get('trigger_type', 'manual'),
        'hivemind_id': s.get('hivemind_id', ''),
    }


def _live_sessions(defaults=None, labels=None):
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
        rooms.setdefault(pid, []).append(
            _figure(s, (defaults or {}).get(pid, ''), labels))
    order = {'asking': 0, 'working': 1, 'idle': 2}
    for figs in rooms.values():
        figs.sort(key=lambda f: (order.get(f['state'], 3),
                                 f.get('started_at') or ''))
    return rooms


@bp.route('/api/floor/figure/<session_id>/name', methods=['POST'])
def name_figure(session_id):
    """Name a figure. `{"name": "..."}`; an empty name clears it.

    Both callers use this one route: Ron clicking a card, and an agent naming
    itself. `by` distinguishes them on the card, because a name the agent chose
    is a statement about itself and a name Ron typed is an instruction — reading
    the first as the second is how you end up trusting a label nobody set.

    Scoped to a LIVE session on purpose. A name for a figure that no longer
    exists is a leak in a file nothing ever prunes.
    """
    if session_id not in (agent_sessions or {}):
        return jsonify({'error': 'no such live session'}), 404
    d = request.get_json(silent=True) or {}
    by = 'self' if (d.get('by') or '').strip().lower() == 'self' else 'user'
    # Absent means "leave it", not "clear it" — a caller setting only an avatar
    # must not wipe the name, and vice versa.
    rec = set_label(session_id,
                    name=d.get('name') if 'name' in d else None,
                    avatar=d.get('avatar') if 'avatar' in d else None,
                    by=by)
    return jsonify({'ok': True, 'name': (rec or {}).get('name', ''),
                    'avatar': (rec or {}).get('avatar', ''),
                    'by': (rec or {}).get('by', '')})


@bp.route('/api/floor')
def floor():
    """Rooms with something live, the quiet ones by name only, and the bench."""
    projects = load_projects() or []
    # Global default under the project's own, mirroring the chat header's
    # resolution order (`agent_status`: project agent_model, then CONFIG).
    _global = state.CONFIG.get('agent_model') or ''
    defaults = {p.get('id'): (p.get('agent_model') or _global) for p in projects}
    rooms = _live_sessions(defaults, read_labels())
    live, quiet = [], []
    for p in projects:
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

    # The bench is the ROSTER — every hired type, whether or not it is already
    # working. It used to be "types with nothing running anywhere", which made
    # a busy type vanish from the board entirely, and with it the only two
    # things you can do to a type: put it in a room, and edit it. So while Dave
    # was working in one project you could neither open him in a second one nor
    # fix a typo in his instructions, and nothing said why he was gone.
    #
    # One character running in several rooms at once is the fact this whole view
    # exists to show (the module docstring) — the bench must not contradict it.
    # Where it is already working is now a line ON the card, not a filter.
    # Keyed on the character's file name, which is its identity — `agent_name`
    # is what it calls itself and two characters may pick the same one.
    # (scope, name) rather than name alone: a project may hire a type with the
    # same file name as a global one, and merging them would report a room the
    # other one is standing in.
    rooms_of: dict = {}
    for r in live:
        for f in r['figures']:
            ch = f['character'] or {}
            nm = ch.get('name')
            if not nm:
                continue
            lst = rooms_of.setdefault(((ch.get('scope') or 'global'), nm), [])
            if (r['id'], r['name']) not in lst:
                lst.append((r['id'], r['name']))

    def _bench_card(c, project_id='', project_name=''):
        eng = c.get('engine') or {}
        seen = rooms_of.get(((c.get('scope') or 'global'), c.get('name'))) or []
        if project_id:
            # A project type can only ever run in its own project, so a room
            # of the same name elsewhere is a different type entirely.
            seen = [x for x in seen if x[0] == project_id]
        return {'name': c.get('name'), 'scope': c.get('scope'),
                'avatar': c.get('avatar') or '',
                'skills': c.get('skills') or [],
                'display': (c.get('agent_name')
                            or c.get('display_name') or c.get('name')),
                'description': _clip(c.get('description'), _DESC_CHARS),
                'provider': eng.get('provider') or '',
                'model': eng.get('model') or '',
                'effort': eng.get('effort') or '',
                # Where this type LIVES. Empty for a global one, which is
                # dispatchable into any room; set for a project-scoped one,
                # which is only dispatchable into its own.
                'project_id': project_id, 'project_name': project_name,
                # Rooms this type is ALREADY working in. Empty = free.
                'rooms': [nm for _, nm in seen]}

    bench = []
    try:
        # The global pool AND every project's own. The bench used to be global
        # only, on the reasoning that a project-scoped type "is not dispatchable
        # anywhere" so offering it here would be a click that cannot be
        # honoured. That is half right and the wrong half: it is dispatchable
        # into exactly one room, its own. Leaving it off meant Ron wrote a
        # persona into a project and it simply was not on the board — no card,
        # no pencil, and nothing saying where it had gone.
        for c in (list_characters() or []):
            bench.append(_bench_card(c))
        for p in (load_projects() or []):
            pp = p.get('project_path')
            if not pp:
                continue
            try:
                own = list_characters(project_path=pp, project_id=p.get('id'))
            except Exception as e:
                _log(f"[floor] roster unreadable for {p.get('id')}: {e}")
                continue
            for c in (own or []):
                # `list_characters` returns the globals alongside a project's
                # own; those are already on the bench once.
                if (c.get('scope') or '') != 'project':
                    continue
                bench.append(_bench_card(c, p.get('id') or '',
                                         p.get('name') or p.get('id') or ''))
    except Exception as e:
        _log(f'[floor] bench unavailable: {e}')
        bench = []
    # Free types first — "who can I put on this?" is the question the bench is
    # read with, and a busy one is the weaker answer, not an invalid one.
    bench.sort(key=lambda c: (1 if c.get('rooms') else 0, c.get('scope') or '',
                              (c.get('display') or '').lower()))

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
