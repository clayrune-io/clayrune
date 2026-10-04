"""Desk Studio: the STORY field and the model call that makes scenes from it
(`mc/desk_story.py`, `mc/desk_storyboard.py`).

Pinned:

  * `story` is a board field: stored on PUT, read back on GET, '' on a board that
    never had one, kept when a PUT omits it, refused past MAX_STORY or when not text;
  * `POST /api/desk/storyboard/generate` sends the WHOLE story to the model as stdin
    data (never joined into the instruction), and returns scenes that already pass
    the store's own scene check; the model call is mocked, there is no real one;
  * Ron's real storyboard (docs/LEARN_STORYBOARD_GEMINI.md) goes through end to end:
    every shot's full instruction lands in `line`, uncut, and survives save + reload;
  * a bad answer (no scenes, a line over MAX_LINE, not JSON, the call failing) is a
    502 that says nothing was changed, never a trimmed or partial board;
  * revising the board returns `from` only for a real, unused scene number;
  * a picked agent that cannot be found is a 404 and the model is NOT called (no
    silent fall back to the default); a found one's persona is the system prompt.
"""
import json
import re
import sys
from pathlib import Path

import pytest
from flask import Flask

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc.blueprints import desk_routes  # noqa: E402
from mc import desk_story, desk_storyboard  # noqa: E402

GEMINI_DOC = PROJECT_ROOT / 'docs' / 'LEARN_STORYBOARD_GEMINI.md'


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
    return app.test_client(), calls, answer


def _gen(client, **body):
    return client.post('/api/desk/storyboard/generate', json=body)


def _put(client, rev, scenes=(), **kw):
    return client.put('/api/desk/studio/draft1/storyboard', json=dict({'rev': rev, 'scenes': list(scenes)}, **kw))


def _reply(*scenes):
    return json.dumps({'scenes': [dict(s) for s in scenes]})


def _shot_paragraphs():
    """The doc's shot prompts: '**1A.** text ...' up to the next blank line + bold."""
    text = GEMINI_DOC.read_text(encoding='utf-8')
    out = []
    for m in re.finditer(r'^\*\*(\w+)\.\*\* (.*?)(?=\n\n\*\*|\n\n---)', text, re.S | re.M):
        out.append((m.group(1), ' '.join(m.group(2).split())))
    return text, out


# -- the story field -----------------------------------------------------------------

def test_story_is_stored_read_back_and_kept_when_a_put_omits_it(env):
    client, _, _ = env
    assert client.get('/api/desk/studio/draft1/storyboard').get_json()['story'] == ''
    r = _put(client, 0, story='Line one.\n\nLine two.')
    assert r.status_code == 200 and r.get_json()['story'] == 'Line one.\n\nLine two.'
    r = _put(client, 1, [])                                  # an older client: no `story` key
    assert r.status_code == 200 and r.get_json()['story'] == 'Line one.\n\nLine two.'
    r = _put(client, 2, story='')                            # clearing it is a real write
    assert r.get_json()['story'] == ''
    assert client.get('/api/desk/studio/draft1/storyboard').get_json()['story'] == ''


def test_story_is_bounded_and_must_be_text(env):
    client, _, _ = env
    assert _put(client, 0, story='x' * desk_storyboard.MAX_STORY).status_code == 200
    r = _put(client, 1, story='x' * (desk_storyboard.MAX_STORY + 1))
    assert r.status_code == 400 and 'story' in ' '.join(r.get_json()['problems'])
    assert _put(client, 1, story=5).status_code == 400
    assert client.get('/api/desk/studio/draft1/storyboard').get_json()['rev'] == 1   # refused writes changed nothing


# -- Ron's real storyboard, end to end ------------------------------------------------

def test_ron_storyboard_story_makes_scenes_with_full_lines_and_survives_reload(env):
    client, calls, answer = env
    story, shots = _shot_paragraphs()
    assert len(story) < desk_storyboard.MAX_STORY and len(shots) == 7, shots
    assert max(len(t) for _, t in shots) > 300            # these are real paragraphs, not stubs
    answer['text'] = _reply(*({'label': f'Shot {n}', 'line': t, 'duration_sec': 5} for n, t in shots))

    r = _gen(client, mode='board', story=story)
    assert r.status_code == 200, r.get_json()
    out = r.get_json()
    assert [s['line'] for s in out['scenes']] == [t for _, t in shots]          # none cut
    assert len({s['id'] for s in out['scenes']}) == 7
    sent = json.loads(calls[0]['stdin_text'])
    assert sent == {'story': story}                                             # the whole story, as data
    assert story not in calls[0]['prompt']                                      # never joined into the instruction

    rev = client.get('/api/desk/studio/draft1/storyboard').get_json()['rev']
    scenes = [{k: s[k] for k in ('id', 'label', 'line', 'duration_sec', 'picture', 'edited')} for s in out['scenes']]
    assert _put(client, rev, scenes, story=story).status_code == 200
    back = client.get('/api/desk/studio/draft1/storyboard').get_json()
    assert back['story'] == story
    assert [s['line'] for s in back['scenes']] == [t for _, t in shots]


def test_a_fenced_answer_is_accepted(env):
    client, _, answer = env
    answer['text'] = 'Here you go:\n```json\n' + _reply({'label': 'A', 'line': 'L', 'duration_sec': 2}) + '\n```'
    r = _gen(client, mode='board', story='s')
    assert r.status_code == 200 and r.get_json()['scenes'][0]['label'] == 'A'


# -- refusals: nothing half-made -------------------------------------------------------

@pytest.mark.parametrize('answer_text', [
    'I cannot do that.',
    json.dumps({'scenes': []}),
    json.dumps({'scenes': [{'label': '', 'line': 'x', 'duration_sec': 3}]}),
    json.dumps({'scenes': [{'label': 'A', 'line': 'x' * (desk_storyboard.MAX_LINE + 1), 'duration_sec': 3}]}),
    json.dumps({'scenes': [{'label': 'A', 'line': 'x', 'duration_sec': 0}]}),
    RuntimeError('boom'),
])
def test_a_bad_answer_is_a_502_that_says_nothing_changed(env, answer_text):
    client, _, answer = env
    answer['text'] = answer_text
    r = _gen(client, mode='board', story='s')
    assert r.status_code == 502 and 'nothing was changed' in r.get_json()['error']


def test_request_errors(env):
    client, calls, _ = env
    assert _gen(client, mode='nope', story='s').status_code == 400
    assert _gen(client, mode='board', story='   ').status_code == 400
    assert _gen(client, mode='board', story='x' * (desk_storyboard.MAX_STORY + 1)).status_code == 400
    assert _gen(client, mode='scene', story='s', scene={'label': 'A', 'line': 'x', 'duration_sec': 3}).status_code == 400
    assert _gen(client, mode='board', story='s', instruction='shorter').status_code == 400   # nothing to revise
    assert calls == []                                                                    # none reached the model


# -- scene + revise modes ---------------------------------------------------------------

def test_scene_mode_returns_one_scene(env):
    client, calls, answer = env
    answer['text'] = json.dumps({'scene': {'label': 'Opening', 'line': 'Wide shot, slower.', 'duration_sec': 6}})
    r = _gen(client, mode='scene', story='the story', instruction='make it slower',
             scene={'id': 'sc-1', 'label': 'Open', 'line': 'Wide shot.', 'duration_sec': 4, 'picture': {'path': 'x'}})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['scene'] == {'label': 'Opening', 'line': 'Wide shot, slower.', 'duration_sec': 6}
    sent = json.loads(calls[0]['stdin_text'])
    assert sent['instruction'] == 'make it slower' and sent['story'] == 'the story'
    assert sent['scene'] == {'label': 'Open', 'line': 'Wide shot.', 'duration_sec': 4}   # the picture is not sent


def test_revise_mode_keeps_only_real_unused_from_numbers(env):
    client, _, answer = env
    answer['text'] = _reply(
        {'from': 2, 'label': 'B', 'line': 'b', 'duration_sec': 3},
        {'from': 2, 'label': 'B again', 'line': 'b', 'duration_sec': 3},      # used twice
        {'from': 9, 'label': 'C', 'line': 'c', 'duration_sec': 3},            # out of range
        {'from': None, 'label': 'New', 'line': 'n', 'duration_sec': 3},
        {'from': True, 'label': 'Bool', 'line': 'n', 'duration_sec': 3})
    cur = [{'label': 'A', 'line': 'a', 'duration_sec': 3}, {'label': 'B', 'line': 'b', 'duration_sec': 3}]
    r = _gen(client, mode='board', story='s', instruction='tidy', scenes=cur)
    assert r.status_code == 200, r.get_json()
    assert [s['from'] for s in r.get_json()['scenes']] == [2, None, None, None, None]


# -- the agent ---------------------------------------------------------------------------

def test_unknown_agent_is_a_404_and_the_model_is_not_called(env, monkeypatch):
    client, calls, _ = env
    monkeypatch.setattr(desk_story._chars, 'read_character', lambda *a, **k: None)
    r = _gen(client, mode='board', story='s', agent='global:ghost')
    assert r.status_code == 404 and 'ghost' in r.get_json()['error']
    assert _gen(client, mode='board', story='s', agent='project:x').status_code == 400
    assert calls == []


def test_a_picked_agents_persona_and_engine_are_used(env, monkeypatch):
    client, calls, answer = env
    rec = {'name': 'marlow', 'agent_name': 'Marlow', 'body': 'You think in wide shots.',
           'engine': {'provider': 'codex', 'model': 'gpt-x', 'effort': 'low'}}
    monkeypatch.setattr(desk_story._chars, 'read_character', lambda scope, name, **k: rec)
    seen = {}

    class Eng:
        provider, model = 'codex', 'gpt-x'

    def resolve(project, character):
        seen['character'] = character
        return Eng

    monkeypatch.setattr(desk_story, '_resolve_engine', resolve)
    answer['text'] = _reply({'label': 'A', 'line': 'L', 'duration_sec': 2})
    r = _gen(client, mode='board', story='s', agent='global:marlow')
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['agent'] == {'ref': 'global:marlow', 'name': 'Marlow'}
    assert (r.get_json()['provider'], r.get_json()['model']) == ('codex', 'gpt-x')
    call = calls[0]
    assert call['provider'] == 'codex' and call['model'] == 'gpt-x' and call['effort'] == 'low'
    assert 'You think in wide shots.' in call['system_prompt'] and 'no tools' in call['system_prompt']
    assert seen['character'] is rec


def test_no_agent_uses_the_default_engine_and_no_persona(env):
    client, calls, answer = env
    answer['text'] = _reply({'label': 'A', 'line': 'L', 'duration_sec': 2})
    r = _gen(client, mode='board', story='s')
    assert r.status_code == 200 and r.get_json()['agent'] is None
    assert calls[0]['system_prompt'] == '' and calls[0]['provider']
