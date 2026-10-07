"""The Studio agent conversation (`mc/desk_story_chat.py`).

Pinned:

  * a question gets prose and NO change; the turns are saved and read back;
  * the thread lives inside the board: it survives a whole-list PUT, never moves
    the board's rev, and is bounded;
  * a change is validated by the store's own scene check before the page sees it:
    with a scene selected, that scene only; with none, sparse edit/add/remove built
    into the whole list, `from` set, untouched scenes word for word even when their
    text was shortened in what the agent was shown;
  * the summary the user reads is written from the validated change (scene numbers);
  * an unusable change keeps the reply and says why (`change_error`); an unreadable
    answer is a 502 and nothing is saved; no saved board, or an unknown agent, is a
    404 and the model is not called;
  * what the agent is sent is bounded and rides as stdin data, never in the prompt;
  * the page's report moves a `proposed` change to `applied` / `failed`, once.
"""
import json
import sys
from pathlib import Path

import pytest
from flask import Flask

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc.blueprints import desk_routes  # noqa: E402
from mc import desk_story, desk_story_chat  # noqa: E402

OWNER = {'kind': 'studio', 'id': 'draft1'}
SCENES = [{'label': 'Open', 'line': 'A desk.', 'duration_sec': 4},
          {'label': 'Turn', 'line': 'The door opens.', 'duration_sec': 5},
          {'label': 'Close', 'line': 'Fade out.', 'duration_sec': 3}]


@pytest.fixture
def env(tmp_path, monkeypatch):
    app = Flask(__name__)
    app.config['TESTING'] = True
    desk_routes.wire(
        load_projects_fn=lambda: [],
        load_project_fn=lambda pid: None,
        store_path=tmp_path / 'desk.json',
        signals_path=tmp_path / 'desk_signals.jsonl',
        uploads_root=tmp_path,
    )
    app.register_blueprint(desk_routes.bp)
    calls = []
    answer = {'text': ''}

    def fake(provider, **kw):
        calls.append(dict(kw, provider=provider))
        if isinstance(answer['text'], Exception):
            raise answer['text']
        return answer['text']

    monkeypatch.setattr(desk_story, '_call_model', fake)
    client = app.test_client()
    r = client.put('/api/desk/studio/draft1/storyboard', json={'rev': 0, 'scenes': [], 'story': 'A story.'})
    assert r.status_code == 200
    return client, calls, answer


def _say(client, message='hello', scenes=SCENES, scene=None, **kw):
    return client.post('/api/desk/storyboard/chat',
                       json=dict({'owner': OWNER, 'message': message, 'story': 'A story.', 'scenes': scenes,
                                  'scene': scene}, **kw))


def _reply(reply, change=None):
    return json.dumps({'reply': reply, 'change': change})


def _thread(client):
    return client.get('/api/desk/storyboard/chat?kind=studio&id=draft1').get_json()['thread']


# -- a question changes nothing --------------------------------------------------------

def test_a_question_gets_prose_and_no_change_and_both_turns_are_saved(env):
    client, calls, answer = env
    answer['text'] = _reply('Scene 2 works because the door gives it a reveal.')
    r = _say(client, 'why does scene 2 work?', scene=2)
    assert r.status_code == 200, r.get_json()
    out = r.get_json()
    assert 'change' not in out
    assert [t['role'] for t in out['turns']] == ['user', 'agent']
    assert out['turns'][1]['text'].startswith('Scene 2 works') and out['turns'][1]['change'] is None
    t = _thread(client)
    assert [x['role'] for x in t] == ['user', 'agent'] and t[0]['text'] == 'why does scene 2 work?' and t[0]['scene'] == 2
    assert client.get('/api/desk/studio/draft1/storyboard').get_json()['rev'] == 1   # a chat is not a scene write


def test_plain_prose_from_the_model_is_an_answer_with_no_change(env):
    client, _, answer = env
    answer['text'] = 'It is a strong opening.'
    r = _say(client, 'thoughts?')
    assert r.status_code == 200 and r.get_json()['turns'][1]['text'] == 'It is a strong opening.'


def test_the_thread_survives_a_put_and_is_bounded(env, monkeypatch):
    client, _, answer = env
    answer['text'] = _reply('ok')
    _say(client, 'one')
    assert client.put('/api/desk/studio/draft1/storyboard', json={'rev': 1, 'scenes': [], 'story': 'New.'}).status_code == 200
    assert len(_thread(client)) == 2
    monkeypatch.setattr(desk_story_chat, 'MAX_TURNS_KEPT', 4)
    for i in range(4):
        _say(client, f'm{i}')
    t = _thread(client)
    assert len(t) == 4 and t[-2]['text'] == 'm3'


# -- a change ----------------------------------------------------------------------------

def test_scene_scope_change_is_validated_and_summarised(env):
    client, calls, answer = env
    answer['text'] = _reply('Slower it is.', {'scene': {'line': 'The door opens slowly.', 'duration_sec': 8}})
    out = _say(client, 'make scene 2 slower', scene=2).get_json()
    assert out['change']['mode'] == 'scene' and out['change']['scene_number'] == 2
    assert out['change']['scene'] == {'label': 'Turn', 'line': 'The door opens slowly.', 'duration_sec': 8}
    assert out['change']['summary'] == 'Changed scene 2.'
    assert out['turns'][1]['change']['status'] == 'proposed'
    sent = json.loads(calls[0]['stdin_text'])
    assert sent['focus'] == 2 and sent['message'] == 'make scene 2 slower'
    assert 'make scene 2 slower' not in calls[0]['prompt']


def test_a_scene_change_that_changes_nothing_is_no_change(env):
    client, _, answer = env
    answer['text'] = _reply('Already like that.', {'scene': {'line': 'The door opens.'}})
    out = _say(client, 'x', scene=2).get_json()
    assert 'change' not in out and out['turns'][1]['change'] is None


def test_board_change_builds_the_whole_list_with_from_and_numbers(env):
    client, _, answer = env
    answer['text'] = _reply('Done.', {'edit': [{'scene': 3, 'line': 'Fade to black.'}],
                                      'add': [{'after': 1, 'label': 'Beat', 'line': 'A pause.', 'duration_sec': 2}],
                                      'remove': [2]})
    out = _say(client, 'tighten it').get_json()
    ch = out['change']
    assert ch['mode'] == 'board'
    assert [(s['from'], s['label'], s['line']) for s in ch['scenes']] == [
        (1, 'Open', 'A desk.'), (None, 'Beat', 'A pause.'), (3, 'Close', 'Fade to black.')]
    assert ch['numbers'] == {'changed': [3], 'added': [2], 'removed': [2]}
    assert ch['summary'] == 'Changed scene 3. Added scene 2. Removed scene 2 (numbered as before the change).'


def test_untouched_scenes_come_back_word_for_word_even_when_shortened_for_the_agent(env, monkeypatch):
    client, calls, answer = env
    monkeypatch.setattr(desk_story_chat, 'CONTEXT_SCENE_BUDGET', 135)
    long = 'W' * 400
    scenes = [{'label': 'A', 'line': long, 'duration_sec': 4}, {'label': 'B', 'line': 'short', 'duration_sec': 4}]
    answer['text'] = _reply('ok', {'edit': [{'scene': 2, 'line': 'shorter'}]})
    out = _say(client, 'edit b', scenes=scenes).get_json()
    sent = json.loads(calls[0]['stdin_text'])
    assert sent['scenes'][0]['line_cut'] is True and len(sent['scenes'][0]['line']) == 130
    assert out['change']['scenes'][0]['line'] == long                      # not the shortened copy
    # editing a scene the agent only saw cut is refused, not applied from the stub
    answer['text'] = _reply('ok', {'edit': [{'scene': 1, 'line': 'x'}]})
    out = _say(client, 'edit a', scenes=scenes).get_json()
    assert 'change' not in out and 'too long to rewrite' in out['turns'][1]['change_error']
    # ... but with scene 1 selected it is sent whole
    _say(client, 'edit a', scenes=scenes, scene=1)
    assert json.loads(calls[-1]['stdin_text'])['scenes'][0]['line'] == long


def test_a_change_that_does_not_validate_keeps_the_reply_and_says_why(env):
    client, _, answer = env
    answer['text'] = _reply('Here you go.', {'edit': [{'scene': 9, 'line': 'x'}]})
    out = _say(client, 'x').get_json()
    assert 'change' not in out
    turn = out['turns'][1]
    assert turn['text'] == 'Here you go.' and turn['change'] is None and 'not there' in turn['change_error']
    answer['text'] = _reply('Long.', {'scene': {'line': 'x' * 5000}})
    out = _say(client, 'x', scene=1).get_json()
    assert 'change' not in out and out['turns'][1]['change_error']


# -- refusals ----------------------------------------------------------------------------

def test_unreadable_answer_or_failed_call_is_a_502_and_saves_nothing(env):
    client, _, answer = env
    answer['text'] = '{"reply": "oops'
    assert _say(client).status_code == 502
    answer['text'] = RuntimeError('boom')
    r = _say(client)
    assert r.status_code == 502 and 'nothing was changed' in r.get_json()['error']
    assert _thread(client) == []


def test_unsaved_board_or_bad_input_is_refused_before_the_model_is_called(env):
    client, calls, answer = env
    answer['text'] = _reply('x')
    r = client.post('/api/desk/storyboard/chat', json={'owner': {'kind': 'studio', 'id': 'never-saved'},
                                                       'message': 'hi', 'scenes': []})
    assert r.status_code == 404
    assert _say(client, '   ').status_code == 400
    assert _say(client, 'x' * (desk_story_chat.MAX_MESSAGE + 1)).status_code == 400
    assert _say(client, 'x', scene=4).status_code == 400
    assert client.post('/api/desk/storyboard/chat', json={'owner': {'kind': 'nope', 'id': 'a'}, 'message': 'x'}).status_code == 400
    assert calls == []


def test_unknown_agent_is_a_404_and_the_model_is_not_called(env, monkeypatch):
    client, calls, _ = env
    monkeypatch.setattr(desk_story._chars, 'read_character', lambda *a, **k: None)
    r = _say(client, agent='global:ghost')
    assert r.status_code == 404 and calls == []


def test_a_picked_agents_persona_is_the_system_prompt_and_names_the_turn(env, monkeypatch):
    client, calls, answer = env
    rec = {'name': 'marlow', 'agent_name': 'Marlow', 'body': 'You think in wide shots.', 'engine': {}}
    monkeypatch.setattr(desk_story._chars, 'read_character', lambda scope, name, **k: rec)
    answer['text'] = _reply('Wide, then.')
    out = _say(client, agent='global:marlow').get_json()
    assert out['agent'] == {'ref': 'global:marlow', 'name': 'Marlow'} and out['turns'][1]['agent'] == 'Marlow'
    assert 'You think in wide shots.' in calls[0]['system_prompt']


# -- context bound -----------------------------------------------------------------------

@pytest.mark.parametrize('focus', [None, 4])
def test_seven_long_scene_lines_fit_whole_and_allow_board_edits(env, focus):
    client, calls, answer = env
    scenes = [{'label': f'Scene {i}', 'line': str(i) * 1900, 'duration_sec': 4} for i in range(1, 8)]
    change = {'edit': [{'scene': 2, 'line': 'Revised.'}]} if focus is None else None
    answer['text'] = _reply('ok', change)
    r = _say(client, 'edit scene 2' if focus is None else 'thoughts?', scenes=scenes, scene=focus)
    assert r.status_code == 200, r.get_json()
    sent = json.loads(calls[-1]['stdin_text'])
    assert [s['line'] for s in sent['scenes']] == [s['line'] for s in scenes]
    assert all('line_cut' not in s for s in sent['scenes'])
    if focus is None:
        assert r.get_json()['change']['scenes'][1]['line'] == 'Revised.'


@pytest.mark.parametrize('focus', [None, 2])
def test_over_budget_board_cuts_lines_but_keeps_focus_whole(env, focus):
    client, calls, answer = env
    scenes = [{'label': f'Scene {i}', 'line': 'W' * 1900, 'duration_sec': 4} for i in range(40)]
    answer['text'] = _reply('ok')
    r = _say(client, scenes=scenes, scene=focus)
    assert r.status_code == 200, r.get_json()
    sent = json.loads(calls[-1]['stdin_text'])['scenes']
    assert sum(len(s['line']) for s in sent) <= desk_story_chat.CONTEXT_SCENE_BUDGET
    assert any(s.get('line_cut') for s in sent)
    for i, (original, row) in enumerate(zip(scenes, sent), 1):
        if i == focus:
            assert row['line'] == original['line'] and 'line_cut' not in row
        else:
            assert row['line_cut'] is True
            assert desk_story_chat.CONTEXT_LINE_MIN <= len(row['line']) < len(original['line'])
            assert original['line'].startswith(row['line'])


def test_over_budget_trims_longest_lines_without_wasting_short_lines_budget(monkeypatch):
    monkeypatch.setattr(desk_story_chat, 'CONTEXT_SCENE_BUDGET', 4000)
    scenes = [{'label': 'Shot', 'line': 'W' * length, 'duration_sec': 4}
              for length in (100, 200, 1500, 1600, 1900)]
    sent, cut = desk_story_chat._context('', scenes, 5, [], '')
    assert [len(s['line']) for s in sent['scenes']] == [100, 200, 900, 900, 1900]
    assert cut == {3, 4}
    assert [s['number'] for s in sent['scenes'] if s.get('line_cut')] == [3, 4]
    assert [len(s['line']) for s in scenes] == [100, 200, 1500, 1600, 1900]


def test_board_exactly_at_budget_is_whole():
    scenes = [{'label': 'Shot', 'line': 'W' * desk_story_chat.MAX_LINE, 'duration_sec': 4}
              for _ in range(30)]
    sent, cut = desk_story_chat._context('', scenes, None, [], '')
    assert sum(len(s['line']) for s in sent['scenes']) == desk_story_chat.CONTEXT_SCENE_BUDGET
    assert cut == set() and all('line_cut' not in s for s in sent['scenes'])


def test_focus_and_line_floor_take_priority_when_budget_cannot_fit_them(monkeypatch):
    monkeypatch.setattr(desk_story_chat, 'CONTEXT_SCENE_BUDGET', 2000)
    scenes = [{'label': 'Shot', 'line': 'W' * length, 'duration_sec': 4}
              for length in (100, 200, 1500, 1900)]
    sent, cut = desk_story_chat._context('', scenes, 4, [], '')
    assert [len(s['line']) for s in sent['scenes']] == [100, 120, 120, 1900]
    assert cut == {2, 3} and 'line_cut' not in sent['scenes'][3]


def test_only_the_recent_turns_are_sent_each_cut(env, monkeypatch):
    client, calls, answer = env
    answer['text'] = _reply('r' * 3000)
    for i in range(10):
        _say(client, f'm{i}')
    _say(client, 'last')
    convo = json.loads(calls[-1]['stdin_text'])['conversation']
    assert len(convo) == desk_story_chat.CONTEXT_TURNS
    assert all(len(c['text']) <= desk_story_chat.CONTEXT_TURN_CHARS for c in convo)
    assert {c['from'] for c in convo} == {'user', 'you'}


# -- the page's report -------------------------------------------------------------------

def test_status_moves_a_proposed_change_once(env):
    client, calls, answer = env
    answer['text'] = _reply('ok', {'scene': {'line': 'New.'}})
    tid = _say(client, 'x', scene=1).get_json()['turns'][1]['id']
    r = client.post('/api/desk/storyboard/chat/status', json={'owner': OWNER, 'turn_id': tid, 'status': 'applied'})
    assert r.status_code == 200 and r.get_json()['change']['status'] == 'applied'
    r = client.post('/api/desk/storyboard/chat/status', json={'owner': OWNER, 'turn_id': tid, 'status': 'failed', 'note': 'n'})
    assert r.get_json()['change']['status'] == 'applied'          # settled: it does not move again
    assert _thread(client)[-1]['change']['status'] == 'applied'
    assert client.post('/api/desk/storyboard/chat/status', json={'owner': OWNER, 'turn_id': 'nope', 'status': 'applied'}).status_code == 404
    assert client.post('/api/desk/storyboard/chat/status', json={'owner': OWNER, 'turn_id': tid, 'status': 'x'}).status_code == 400
    # an applied change is told to the agent on the next turn
    answer['text'] = _reply('ok')
    _say(client, 'next')
    assert '[applied: Changed scene 1.]' in calls[-1]['stdin_text']
