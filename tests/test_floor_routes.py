"""The Floor endpoint (mc/blueprints/floor_routes.py) — MC-897 phase 1.

`docs/AGENT_FLOOR_DESIGN.md`. Rooms are projects, a figure is a SESSION. The
distinction is the whole design: one character can be running in three projects
at once, and the chat header pill — one persona, one chat — structurally cannot
say so.

These tests pin the properties that make the board trustworthy. A board you do
not trust is worse than no board, because you keep opening the twenty modals
anyway and now you also maintain a view.
"""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _session(pid, sid, **kw):
    s = {'project_id': pid, 'session_id': sid, 'status': 'running',
         'task': f'task for {sid}', 'started_at': '2026-08-24T10:00:00Z'}
    s.update(kw)
    return s


@pytest.fixture()
def floor():
    from mc.blueprints import floor_routes as fr
    from flask import Flask
    sessions, projects, chars = {}, [], []
    fr.wire(agent_sessions_ref=sessions,
            load_projects_fn=lambda: projects,
            list_characters_fn=lambda: chars)
    app = Flask(__name__)
    app.register_blueprint(fr.bp)
    return fr, app.test_client(), sessions, projects, chars


def _get(client):
    r = client.get('/api/floor')
    assert r.status_code == 200
    return r.get_json()


# ── a figure is a session ───────────────────────────────────────────────────

def test_one_character_appears_in_every_room_it_is_running_in(floor):
    """THE reason this view exists. Marlow on two projects is two figures — the
    header pill can only ever show one, which is the gap the board fills."""
    fr, c, sessions, projects, _ = floor
    projects += [{'id': 'a', 'name': 'Alpha'}, {'id': 'b', 'name': 'Beta'}]
    ch = {'name': 'marlow', 'agent_name': 'Marlow'}
    sessions['1'] = _session('a', '1', character=ch)
    sessions['2'] = _session('b', '2', character=ch)

    d = _get(c)
    assert d['counts']['rooms'] == 2 and d['counts']['figures'] == 2
    for room in d['rooms']:
        assert [f['character']['display'] for f in room['figures']] == ['Marlow']


def test_two_agents_in_one_project_are_two_figures_in_one_room(floor):
    """Frame 2. This is the shape a per-project row cannot render."""
    fr, c, sessions, projects, _ = floor
    projects.append({'id': 'a', 'name': 'Alpha'})
    sessions['1'] = _session('a', '1', character={'name': 'fenn', 'agent_name': 'Fenn'})
    sessions['2'] = _session('a', '2', character={'name': 'quill', 'agent_name': 'Quill'})
    d = _get(c)
    assert len(d['rooms']) == 1
    assert sorted(f['character']['display'] for f in d['rooms'][0]['figures']) == \
        ['Fenn', 'Quill']


# ── what the board must put first ───────────────────────────────────────────

def test_a_room_that_needs_you_sorts_above_one_that_does_not(floor):
    """The board's job is to surface the thing waiting on a human, not to
    render an alphabet."""
    fr, c, sessions, projects, _ = floor
    projects += [{'id': 'a', 'name': 'Alpha'}, {'id': 'z', 'name': 'Zulu'}]
    sessions['1'] = _session('a', '1')                          # working
    sessions['2'] = _session('z', '2', status='idle', waiting_for_question=True)
    d = _get(c)
    assert [r['id'] for r in d['rooms']] == ['z', 'a']
    assert d['rooms'][0]['figures'][0]['state'] == 'asking'
    assert d['rooms'][0]['figures'][0]['reason'] == 'question'


def test_plan_approval_and_a_question_are_both_asking_but_distinguishable(floor):
    fr, c, sessions, projects, _ = floor
    projects.append({'id': 'a', 'name': 'Alpha'})
    sessions['1'] = _session('a', '1', waiting_for_plan_approval=True)
    d = _get(c)
    f = d['rooms'][0]['figures'][0]
    assert (f['state'], f['reason']) == ('asking', 'plan')


def test_state_priority_matches_the_project_tiles(floor):
    """Two surfaces disagreeing about whether a project needs you is worse than
    either being wrong — you stop trusting both. Same order as
    `_project_live_agent`: asking > working > idle."""
    fr, c, sessions, projects, _ = floor
    projects.append({'id': 'a', 'name': 'Alpha'})
    sessions['1'] = _session('a', '1', status='idle')
    sessions['2'] = _session('a', '2')
    sessions['3'] = _session('a', '3', waiting_for_question=True)
    d = _get(c)
    assert [f['state'] for f in d['rooms'][0]['figures']] == \
        ['asking', 'working', 'idle']


# ── what must never appear ──────────────────────────────────────────────────

def test_incognito_and_housekeeping_never_reach_the_board(floor):
    """Incognito's whole promise is that it stays off the public indicators,
    and a cross-project board is the most public indicator there is."""
    fr, c, sessions, projects, _ = floor
    projects.append({'id': 'a', 'name': 'Alpha'})
    sessions['1'] = _session('a', '1', incognito=True)
    sessions['2'] = _session('a', '2', housekeeping=True)
    d = _get(c)
    assert d['rooms'] == []
    assert [q['id'] for q in d['quiet']] == ['a']


def test_a_finished_session_is_not_a_figure(floor):
    fr, c, sessions, projects, _ = floor
    projects.append({'id': 'a', 'name': 'Alpha'})
    sessions['1'] = _session('a', '1', status='completed')
    sessions['2'] = _session('a', '2', status='error')
    assert _get(c)['rooms'] == []


def test_an_unnamed_session_is_shown_as_untyped_not_renamed(floor):
    """"no type" is a finding — it is what Frame 1 is showing when it says
    nobody has a name yet. Quietly labelling it with the configured default
    would hide the exact gap the board exists to make visible."""
    fr, c, sessions, projects, _ = floor
    projects.append({'id': 'a', 'name': 'Alpha'})
    sessions['1'] = _session('a', '1', character=None)
    assert _get(c)['rooms'][0]['figures'][0]['character'] is None


# ── the quiet half, and the bench ───────────────────────────────────────────

def test_projects_with_nothing_running_are_quiet_not_empty_rooms(floor):
    """Twenty mostly-empty rooms is the dead-room failure in §3 of the design
    wearing a different hat."""
    fr, c, sessions, projects, _ = floor
    projects += [{'id': f'p{i}', 'name': f'P{i}'} for i in range(5)]
    sessions['1'] = _session('p2', '1')
    d = _get(c)
    assert [r['id'] for r in d['rooms']] == ['p2']
    assert len(d['quiet']) == 4 and 'figures' not in d['quiet'][0]


def test_the_bench_is_types_with_nothing_running_anywhere(floor):
    fr, c, sessions, projects, chars = floor
    projects.append({'id': 'a', 'name': 'Alpha'})
    chars += [{'name': 'fenn', 'agent_name': 'Fenn', 'scope': 'global',
               'engine': {'provider': 'claude', 'model': 'claude-sonnet-5'}},
              {'name': 'quill', 'agent_name': 'Quill', 'scope': 'global'}]
    sessions['1'] = _session('a', '1', character={'name': 'fenn', 'agent_name': 'Fenn'})
    d = _get(c)
    assert [b['display'] for b in d['bench']] == ['Quill']
    assert d['counts']['bench'] == 1


def test_the_bench_reads_the_engine_off_its_own_key(floor):
    """`provider`/`model`/`effort` live under `engine`, not at the top level of
    a character record — reading them flat yields three silent blanks."""
    fr, c, sessions, projects, chars = floor
    chars.append({'name': 'fenn', 'agent_name': 'Fenn', 'scope': 'global',
                  'engine': {'provider': 'claude', 'model': 'claude-sonnet-5',
                             'effort': 'high'}})
    b = _get(c)['bench'][0]
    assert (b['provider'], b['model'], b['effort']) == \
        ('claude', 'claude-sonnet-5', 'high')


def test_a_broken_character_pool_costs_the_bench_not_the_board(floor):
    """The board is the load-bearing half. A roster that cannot be read must
    not take the running sessions down with it."""
    fr, c, sessions, projects, _ = floor
    projects.append({'id': 'a', 'name': 'Alpha'})
    sessions['1'] = _session('a', '1')

    def boom():
        raise RuntimeError('roster unreadable')
    fr.list_characters = boom
    d = _get(c)
    assert d['bench'] == []
    assert d['counts']['figures'] == 1


# ── the payload the card actually needs ─────────────────────────────────────

def test_a_figure_carries_what_it_takes_to_open_the_chat(floor):
    """Clicking a figure must reach that session, not just that project —
    hierarchy is for delegation, not for inspection (DAVE_DESIGN §8)."""
    fr, c, sessions, projects, _ = floor
    projects.append({'id': 'a', 'name': 'Alpha'})
    sessions['1'] = _session('a', '1', claude_session_id='csid-1')
    f = _get(c)['rooms'][0]['figures'][0]
    assert f['session_id'] == '1' and f['claude_session_id'] == 'csid-1'
    assert f['age'], 'no age — a forgotten 20-hour session stays invisible'


def test_the_activity_string_is_only_for_a_running_turn(floor):
    """A stale 'thinking…' on an idle figure is a lie about a live system."""
    fr, c, sessions, projects, _ = floor
    projects.append({'id': 'a', 'name': 'Alpha'})
    sessions['1'] = _session('a', '1', activity_state='thinking')
    sessions['2'] = _session('a', '2', status='idle', activity_state='thinking')
    figs = {f['session_id']: f for f in _get(c)['rooms'][0]['figures']}
    assert figs['1']['activity'] == 'thinking'
    assert figs['2']['activity'] == ''
