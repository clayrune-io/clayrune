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


def test_the_bench_is_the_whole_roster_busy_or_not(floor):
    """It used to be "types with nothing running anywhere", so a type vanished
    from the board the moment it started working — taking with it the only two
    things you can do to a type: put it in a room, and edit it. Free ones sort
    first; a busy one says where it already is."""
    fr, c, sessions, projects, chars = floor
    projects.append({'id': 'a', 'name': 'Alpha'})
    chars += [{'name': 'fenn', 'agent_name': 'Fenn', 'scope': 'global',
               'engine': {'provider': 'claude', 'model': 'claude-sonnet-5'}},
              {'name': 'quill', 'agent_name': 'Quill', 'scope': 'global'}]
    sessions['1'] = _session('a', '1', character={'name': 'fenn', 'agent_name': 'Fenn'})
    d = _get(c)
    assert [b['display'] for b in d['bench']] == ['Quill', 'Fenn']
    assert d['counts']['bench'] == 2
    assert {b['display']: b['rooms'] for b in d['bench']} ==         {'Quill': [], 'Fenn': ['Alpha']}


def test_a_type_working_in_two_rooms_names_both_once(floor):
    """The reason this view exists is that one character runs in several places
    at once. Two sessions in the SAME room must not print that room twice."""
    fr, c, sessions, projects, chars = floor
    projects += [{'id': 'a', 'name': 'Alpha'}, {'id': 'b', 'name': 'Beta'}]
    chars.append({'name': 'dave', 'agent_name': 'Dave', 'scope': 'global'})
    ch = {'name': 'dave', 'agent_name': 'Dave'}
    sessions['1'] = _session('a', '1', character=ch)
    sessions['2'] = _session('a', '2', character=ch)
    sessions['3'] = _session('b', '3', character=ch)
    assert _get(c)['bench'][0]['rooms'] == ['Alpha', 'Beta']


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


def test_the_model_pill_matches_what_the_chat_header_would_show(floor):
    """A session dispatched before `agent_model` was captured carries nothing,
    and the header falls back to the project default. Reading the session dict
    flat renders a blank pill beside a header showing a model — for the SAME
    session, which is the two-surfaces-disagreeing failure again."""
    fr, c, sessions, projects, _ = floor
    projects.append({'id': 'a', 'name': 'Alpha', 'agent_model': 'claude-opus-5'})
    sessions['1'] = _session('a', '1')                       # no model of its own
    sessions['2'] = _session('a', '2', model='claude-haiku-4-5')
    figs = {f['session_id']: f for f in _get(c)['rooms'][0]['figures']}
    assert figs['1']['model'] == 'claude-opus-5'
    assert figs['2']['model'] == 'claude-haiku-4-5', 'a session model was overridden'


def test_a_non_claude_session_never_inherits_the_claude_default(floor):
    """A project's `agent_model` is always a claude id — the Agent-settings
    picker offers nothing else. Applying it to a codex run made the chat header
    read "codex - claude-opus-5" for a spawn that received no --model at all."""
    fr, c, sessions, projects, _ = floor
    projects.append({'id': 'a', 'name': 'Alpha', 'agent_model': 'claude-opus-5'})
    sessions['1'] = _session('a', '1', provider='codex')
    f = _get(c)['rooms'][0]['figures'][0]
    assert (f['provider'], f['model']) == ('codex', '')


# ── name vs type, and naming a figure ───────────────────────────────────────

def test_a_figure_always_has_a_name_even_with_no_persona(floor, monkeypatch):
    """The board used to print "no type" where the name goes, while that same
    session's prompt says "Your name is Vector" — two surfaces disagreeing
    about who someone is. The role is a separate fact and still shown."""
    fr, c, sessions, projects, _ = floor
    from mc import state
    monkeypatch.setitem(state.CONFIG, 'agent_name', 'Vector')
    projects.append({'id': 'a', 'name': 'Alpha'})
    sessions['1'] = _session('a', '1', character=None)
    f = _get(c)['rooms'][0]['figures'][0]
    assert (f['name'], f['name_from']) == ('Vector', 'default')
    assert f['character'] is None, 'the type is still reported as absent'


def test_a_personas_own_name_beats_the_configured_default(floor, monkeypatch):
    fr, c, sessions, projects, _ = floor
    from mc import state
    monkeypatch.setitem(state.CONFIG, 'agent_name', 'Vector')
    projects.append({'id': 'a', 'name': 'Alpha'})
    sessions['1'] = _session('a', '1', character={'name': 'fenn', 'agent_name': 'Fenn'})
    f = _get(c)['rooms'][0]['figures'][0]
    assert (f['name'], f['name_from']) == ('Fenn', 'character')


def test_naming_a_figure_outranks_both(floor, tmp_path):
    fr, c, sessions, projects, _ = floor
    fr.LABELS_PATH = tmp_path / 'agent_labels.json'
    projects.append({'id': 'a', 'name': 'Alpha'})
    sessions['1'] = _session('a', '1', character={'name': 'fenn', 'agent_name': 'Fenn'})

    r = c.post('/api/floor/figure/1/name', json={'name': 'Scout'})
    assert r.status_code == 200 and r.get_json()['name'] == 'Scout'
    f = _get(c)['rooms'][0]['figures'][0]
    assert (f['name'], f['name_from']) == ('Scout', 'user')
    assert f['character']['display'] == 'Fenn', 'renaming changed the TYPE'


def test_who_named_it_is_recorded(floor, tmp_path):
    """A name the agent chose is a statement about itself; one Ron typed is an
    instruction. Reading the first as the second is how you end up trusting a
    label nobody set."""
    fr, c, sessions, projects, _ = floor
    fr.LABELS_PATH = tmp_path / 'agent_labels.json'
    projects.append({'id': 'a', 'name': 'Alpha'})
    sessions['1'] = _session('a', '1')
    c.post('/api/floor/figure/1/name', json={'name': 'Scribe', 'by': 'self'})
    assert _get(c)['rooms'][0]['figures'][0]['name_from'] == 'self'


def test_an_empty_name_clears_it(floor, tmp_path, monkeypatch):
    fr, c, sessions, projects, _ = floor
    from mc import state
    monkeypatch.setitem(state.CONFIG, 'agent_name', 'Vector')
    fr.LABELS_PATH = tmp_path / 'agent_labels.json'
    projects.append({'id': 'a', 'name': 'Alpha'})
    sessions['1'] = _session('a', '1')
    c.post('/api/floor/figure/1/name', json={'name': 'Scout'})
    c.post('/api/floor/figure/1/name', json={'name': '  '})
    f = _get(c)['rooms'][0]['figures'][0]
    assert (f['name'], f['name_from']) == ('Vector', 'default')


def test_a_name_survives_the_session_dict_being_rebuilt(floor, tmp_path):
    """Sessions are revived from the agent log after a restart. A name that
    only lived on the in-memory dict would vanish there."""
    fr, c, sessions, projects, _ = floor
    fr.LABELS_PATH = tmp_path / 'agent_labels.json'
    projects.append({'id': 'a', 'name': 'Alpha'})
    sessions['1'] = _session('a', '1')
    c.post('/api/floor/figure/1/name', json={'name': 'Scout'})
    sessions['1'] = _session('a', '1')          # revived: a fresh dict
    assert _get(c)['rooms'][0]['figures'][0]['name'] == 'Scout'


def test_naming_a_dead_session_is_refused(floor, tmp_path):
    """A name for a figure that no longer exists is a leak in a file nothing
    ever prunes."""
    fr, c, sessions, projects, _ = floor
    fr.LABELS_PATH = tmp_path / 'agent_labels.json'
    assert c.post('/api/floor/figure/ghost/name', json={'name': 'X'}).status_code == 404


def test_a_name_is_a_name_not_a_paragraph(floor, tmp_path):
    fr, c, sessions, projects, _ = floor
    fr.LABELS_PATH = tmp_path / 'agent_labels.json'
    projects.append({'id': 'a', 'name': 'Alpha'})
    sessions['1'] = _session('a', '1')
    c.post('/api/floor/figure/1/name', json={'name': 'x' * 200})
    assert len(_get(c)['rooms'][0]['figures'][0]['name']) == fr._NAME_CHARS


# ── the face ────────────────────────────────────────────────────────────────

def test_a_type_brings_its_own_face(floor):
    fr, c, sessions, projects, _ = floor
    projects.append({'id': 'a', 'name': 'Alpha'})
    sessions['1'] = _session('a', '1', character={'name': 'quill',
                                                  'agent_name': 'Quill',
                                                  'avatar': '\U0001F50D'})
    assert _get(c)['rooms'][0]['figures'][0]['avatar'] == '\U0001F50D'


def test_a_figure_with_no_face_gets_no_face_not_a_random_one(floor):
    """Absence is a finding on this board. Server-side defaulting would paper
    over the same gap "no type" exists to show."""
    fr, c, sessions, projects, _ = floor
    projects.append({'id': 'a', 'name': 'Alpha'})
    sessions['1'] = _session('a', '1')
    assert _get(c)['rooms'][0]['figures'][0]['avatar'] == ''


def test_setting_a_face_does_not_wipe_the_name(floor, tmp_path):
    """The label record is rewritten whole, so absent has to mean "leave it".
    A caller that only sets a face must not clear the name, or the two UI
    controls silently undo each other."""
    fr, c, sessions, projects, _ = floor
    fr.LABELS_PATH = tmp_path / 'agent_labels.json'
    projects.append({'id': 'a', 'name': 'Alpha'})
    sessions['1'] = _session('a', '1')
    c.post('/api/floor/figure/1/name', json={'name': 'Scout'})
    c.post('/api/floor/figure/1/name', json={'avatar': '\U0001F989'})
    f = _get(c)['rooms'][0]['figures'][0]
    assert (f['name'], f['avatar']) == ('Scout', '\U0001F989')

    c.post('/api/floor/figure/1/name', json={'name': 'Scribe'})
    f = _get(c)['rooms'][0]['figures'][0]
    assert (f['name'], f['avatar']) == ('Scribe', '\U0001F989'), 'renaming ate the face'


def test_an_explicit_face_beats_the_types_own(floor, tmp_path):
    fr, c, sessions, projects, _ = floor
    fr.LABELS_PATH = tmp_path / 'agent_labels.json'
    projects.append({'id': 'a', 'name': 'Alpha'})
    sessions['1'] = _session('a', '1', character={'name': 'quill',
                                                  'agent_name': 'Quill',
                                                  'avatar': '\U0001F50D'})
    c.post('/api/floor/figure/1/name', json={'avatar': '\U0001F989'})
    assert _get(c)['rooms'][0]['figures'][0]['avatar'] == '\U0001F989'


def test_a_multi_codepoint_emoji_is_not_truncated(floor, tmp_path):
    """A "single emoji" is often several codepoints — a ZWJ sequence or a
    skin-tone modifier. A 1-char cap silently turns a woman technologist into
    a woman."""
    fr, c, sessions, projects, _ = floor
    fr.LABELS_PATH = tmp_path / 'agent_labels.json'
    projects.append({'id': 'a', 'name': 'Alpha'})
    sessions['1'] = _session('a', '1')
    zwj = '\U0001F469‍\U0001F4BB'          # 3 codepoints
    c.post('/api/floor/figure/1/name', json={'avatar': zwj})
    assert _get(c)['rooms'][0]['figures'][0]['avatar'] == zwj


def test_an_avatar_cannot_become_a_second_name_field(floor, tmp_path):
    fr, c, sessions, projects, _ = floor
    fr.LABELS_PATH = tmp_path / 'agent_labels.json'
    projects.append({'id': 'a', 'name': 'Alpha'})
    sessions['1'] = _session('a', '1')
    c.post('/api/floor/figure/1/name', json={'avatar': 'the code reviewer'})
    assert len(_get(c)['rooms'][0]['figures'][0]['avatar']) == fr._AVATAR_CHARS


def test_clearing_both_forgets_the_figure_entirely(floor, tmp_path):
    """Otherwise the label file grows a row per session, forever."""
    fr, c, sessions, projects, _ = floor
    fr.LABELS_PATH = tmp_path / 'agent_labels.json'
    projects.append({'id': 'a', 'name': 'Alpha'})
    sessions['1'] = _session('a', '1')
    c.post('/api/floor/figure/1/name', json={'name': 'Scout', 'avatar': '\U0001F989'})
    c.post('/api/floor/figure/1/name', json={'name': '', 'avatar': ''})
    assert fr.read_labels() == {}


def test_the_bench_shows_a_types_face(floor):
    fr, c, sessions, projects, chars = floor
    chars.append({'name': 'quill', 'agent_name': 'Quill', 'scope': 'global',
                  'avatar': '\U0001F50D'})
    assert _get(c)['bench'][0]['avatar'] == '\U0001F50D'


# ── the roster block in an agent's prompt ───────────────────────────────────

def test_the_roster_maps_what_ron_says_to_what_the_agent_calls(tmp_path, monkeypatch):
    """The harness already lists agent TYPES — characters are Claude Code
    subagent files, so `code-reviewer` is in every prompt already. What is
    missing is that Ron says "Fenn", and nothing maps the two."""
    import server  # noqa: F401
    from mc.blueprints import agent_routes as ar
    from mc import characters as chars
    monkeypatch.setattr(chars, 'list_characters', lambda **kw: [
        {'name': 'code-reviewer', 'agent_name': 'Fenn', 'scope': 'global',
         'avatar': '\U0001F989', 'description': 'reviews a diff'},
    ])
    out = ar._roster_block({'id': 'p1', 'project_path': str(tmp_path)}, 5199)
    assert 'Fenn' in out and '`code-reviewer`' in out
    assert '\U0001F989' in out, 'the roster dropped the face'


def test_the_roster_says_which_route_is_visible(tmp_path, monkeypatch):
    """Task-tool subagents run in-process and never become sessions, so they
    never reach the Floor. Which route to take is a real choice with a real
    consequence, and nothing was saying so."""
    import server  # noqa: F401
    from mc.blueprints import agent_routes as ar
    from mc import characters as chars
    monkeypatch.setattr(chars, 'list_characters', lambda **kw: [
        {'name': 'code-reviewer', 'agent_name': 'Fenn', 'scope': 'global',
         'description': 'reviews a diff'}])
    out = ar._roster_block({'id': 'p1', 'project_path': str(tmp_path)}, 5199)
    assert 'Task tool' in out and 'Floor' in out
    assert '/agent/dispatch' in out


def test_an_empty_roster_prints_nothing(tmp_path, monkeypatch):
    """A heading over an empty list is worse than silence — it spends prompt
    space to tell an agent it has nobody."""
    import server  # noqa: F401
    from mc.blueprints import agent_routes as ar
    from mc import characters as chars
    monkeypatch.setattr(chars, 'list_characters', lambda **kw: [])
    assert ar._roster_block({'id': 'p1', 'project_path': str(tmp_path)}, 5199) == ''
    # A type with no self-chosen name adds nothing the harness has not said.
    monkeypatch.setattr(chars, 'list_characters', lambda **kw: [
        {'name': 'code-reviewer', 'scope': 'global', 'description': 'x'}])
    assert ar._roster_block({'id': 'p1', 'project_path': str(tmp_path)}, 5199) == ''


def test_an_unreadable_roster_never_costs_the_prompt(tmp_path, monkeypatch):
    import server  # noqa: F401
    from mc.blueprints import agent_routes as ar
    from mc import characters as chars

    def boom(**kw):
        raise RuntimeError('agents dir on fire')
    monkeypatch.setattr(chars, 'list_characters', boom)
    assert ar._roster_block({'id': 'p1', 'project_path': str(tmp_path)}, 5199) == ''


# ── text that has to fit on a card ──────────────────────────────────────────

def test_a_clipped_line_never_ends_mid_word(floor):
    """Both screenshots showed it: "real design probl", "a rough idea turned
    i". A hard slice lands wherever it lands, and a card ending mid-word reads
    as broken rather than abbreviated — the reader stops to work out whether
    something is missing."""
    fr, c, sessions, projects, chars = floor
    projects.append({'id': 'a', 'name': 'Alpha'})
    long_desc = ('Use to review a diff, branch, or file for correctness bugs '
                 'and real design problems before it lands, and to say plainly '
                 'which of them actually matter.')
    chars.append({'name': 'fenn', 'agent_name': 'Fenn', 'scope': 'global',
                  'description': long_desc})
    got = _get(c)['bench'][0]['description']
    assert got.endswith('…')
    assert long_desc.startswith(got[:-1].rstrip('…').rstrip())
    # the surviving text is whole words
    assert got[:-1].rstrip() in long_desc


def test_a_short_line_is_left_completely_alone(floor):
    fr, c, sessions, projects, chars = floor
    chars.append({'name': 'fenn', 'agent_name': 'Fenn', 'scope': 'global',
                  'description': 'reviews a diff'})
    assert _get(c)['bench'][0]['description'] == 'reviews a diff'


def test_one_enormous_token_still_gets_cut(floor):
    """No space to break on — a hard cut is the only option, and returning the
    whole 4KB string would blow out the card instead."""
    fr, c, sessions, projects, chars = floor
    chars.append({'name': 'x', 'agent_name': 'X', 'scope': 'global',
                  'description': 'q' * 400})
    assert len(_get(c)['bench'][0]['description']) <= fr._DESC_CHARS + 1


def test_the_task_line_is_clipped_the_same_way(floor):
    fr, c, sessions, projects, _ = floor
    projects.append({'id': 'a', 'name': 'Alpha'})
    sessions['1'] = _session('a', '1', task='so resuming the memory work for '
                                            'Dave, as of now, we are still I '
                                            'believe at the very top tier of '
                                            'the thing we set out to build')
    got = _get(c)['rooms'][0]['figures'][0]['task']
    assert got.endswith('…') and not got.endswith('ti…')


# ── a declared toolkit ──────────────────────────────────────────────────────

def test_a_type_carries_its_declared_skills_to_the_bench(floor):
    """A description says what a type is FOR; the toolkit says what it can
    reach for. That is the "abilities are not observable enough" gap."""
    fr, c, sessions, projects, chars = floor
    chars.append({'name': 'fenn', 'agent_name': 'Fenn', 'scope': 'global',
                  'skills': ['audit-doc', 'code-review']})
    assert _get(c)['bench'][0]['skills'] == ['audit-doc', 'code-review']


def test_a_type_with_no_toolkit_reports_an_empty_one(floor):
    fr, c, sessions, projects, chars = floor
    chars.append({'name': 'fenn', 'agent_name': 'Fenn', 'scope': 'global'})
    assert _get(c)['bench'][0]['skills'] == []


def test_a_figure_avatar_survives_the_length_cap(floor, tmp_path):
    """`fig:guard` is 9 chars. The cap here was a hand-copied `= 8` "mirroring"
    characters.MAX_AVATAR_LEN; when that went 8 -> 40 this copy stayed behind
    and served `fig:guar` — a broken <img> on every card, with the correct
    value sitting untouched in the character file. It imports the constant now.
    """
    fr, c, sessions, projects, chars = floor
    fr.LABELS_PATH = tmp_path / 'agent_labels.json'
    projects.append({'id': 'a', 'name': 'Alpha'})
    sessions['1'] = _session('a', '1', character={
        'name': 'dave', 'agent_name': 'Dave', 'scope': 'global',
        'avatar': 'fig:guard'})
    assert _get(c)['rooms'][0]['figures'][0]['avatar'] == 'fig:guard'
    c.post('/api/floor/figure/1/name', json={'avatar': 'fig:gardener'})
    assert _get(c)['rooms'][0]['figures'][0]['avatar'] == 'fig:gardener'
