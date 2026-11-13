"""The storyboard chat agent can SEE the pictures and clips attached to scenes
(MC-1175 / backlog 863ee224).

Pinned:

  * the guard lets `Read` open exactly the absolute paths on the turn's allowlist,
    gitignored or not, and nothing near them: a sibling, a `..` spelling, a link to
    an allowed name, a file that became a link since, Glob and Grep all stay denied;
  * the allowlist reaches the hook as `--readable <file>` and a missing file fails
    closed; `Read` is offered with no project folder only when files are allowed;
  * the chat payload carries a picture row per scene from the SAVED board, never a
    path from the request body; the selected scene is shown first; there is a cap;
  * a clip becomes evenly spaced frames in a temp folder that is gone after the
    turn; no ffmpeg / a failed cut / a blind engine is said, not skipped;
  * an /api/serve-image URL in the message names a library file, and only a library
    file.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from flask import Flask

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc.blueprints import desk_routes  # noqa: E402
from mc import desk_read_guard as guard  # noqa: E402
from mc import desk_research, desk_story, desk_story_media as media  # noqa: E402

OWNER = {'kind': 'studio', 'id': 'draft1'}
IMG = 'desk/library/image/Storyboards/{}.png'
CLIP = 'desk/library/video/Storyboards/{}.mp4'


# -- the guard -------------------------------------------------------------------------

def _verdict(root, tool, readable=(), **inp):
    out = guard.decide(root, {'tool_name': tool, 'tool_input': inp}, readable=readable)
    return out['hookSpecificOutput']['permissionDecision']


@pytest.fixture
def lib(tmp_path):
    """A project folder, and a library folder OUTSIDE it holding an allowed picture and a sibling."""
    project = tmp_path / 'project'
    project.mkdir()
    folder = tmp_path / 'uploads' / 'library'
    folder.mkdir(parents=True)
    allowed = folder / 'allowed.png'
    allowed.write_bytes(b'png')
    sibling = folder / 'sibling.png'
    sibling.write_bytes(b'png')
    return project, folder, str(allowed.resolve()), str(sibling.resolve())


def test_an_allowed_file_is_readable_and_its_sibling_is_not(lib):
    project, folder, allowed, sibling = lib
    assert _verdict(project, 'Read', [allowed], file_path=allowed) == 'allow'
    assert _verdict(project, 'Read', [allowed], file_path=sibling) == 'deny'
    assert _verdict(project, 'Read', [], file_path=allowed) == 'deny'            # no allowlist, today's rules


def test_an_allowed_file_is_readable_with_no_project_folder_and_nothing_else_is(lib):
    _, _, allowed, sibling = lib
    assert _verdict(None, 'Read', [allowed], file_path=allowed) == 'allow'
    assert _verdict(None, 'Read', [allowed], file_path=sibling) == 'deny'
    assert _verdict(None, 'Glob', [allowed], path=os.path.dirname(allowed), pattern='*') == 'deny'


def test_glob_and_grep_do_not_gain_the_allowed_folder(lib):
    project, folder, allowed, _ = lib
    for tool, extra in (('Glob', {'pattern': '*'}), ('Grep', {'pattern': 'x'})):
        assert _verdict(project, tool, [allowed], path=str(folder), **extra) == 'deny'
        assert _verdict(project, tool, [allowed], path=allowed, **extra) == 'deny'


def test_a_dotdot_spelling_of_an_allowed_name_is_not_the_allowed_file(lib):
    project, folder, allowed, _ = lib
    spelled = str(folder / '..' / 'library' / 'allowed.png')
    assert spelled != allowed
    assert _verdict(project, 'Read', [allowed], file_path=spelled) == 'deny'
    assert _verdict(project, 'Read', [allowed], file_path='allowed.png') == 'deny'      # relative


def test_a_link_to_an_allowed_name_is_denied(lib):
    project, folder, allowed, _ = lib
    link = folder / 'link.png'
    try:
        link.symlink_to(allowed)
    except OSError:
        pytest.skip('symlinks need privilege on this host')
    assert _verdict(project, 'Read', [allowed], file_path=str(link)) == 'deny'
    # and an allowed path that BECAME a link after the server chose it is refused too
    swapped = folder / 'swapped.png'
    swapped.write_bytes(b'x')
    chosen = str(swapped.resolve())
    swapped.unlink()
    swapped.symlink_to(allowed)
    assert _verdict(project, 'Read', [chosen], file_path=chosen) == 'deny'


def test_an_allowed_path_that_is_gone_or_a_folder_is_denied(lib):
    project, folder, allowed, _ = lib
    assert _verdict(project, 'Read', [str(folder)], file_path=str(folder)) == 'deny'
    gone = str(folder / 'gone.png')
    assert _verdict(project, 'Read', [gone], file_path=gone) == 'deny'


def test_a_gitignored_file_inside_the_project_is_readable_only_when_allowed(tmp_path):
    root = tmp_path / 'repo'
    (root / 'data').mkdir(parents=True)
    (root / '.gitignore').write_text('data/\n')
    secret = root / 'data' / 'a.png'
    secret.write_bytes(b'x')
    other = root / 'data' / 'b.png'
    other.write_bytes(b'x')
    assert _verdict(root, 'Read', [], file_path=str(secret)) == 'deny'
    assert _verdict(root, 'Read', [str(secret.resolve())], file_path=str(secret.resolve())) == 'allow'
    assert _verdict(root, 'Read', [str(secret.resolve())], file_path=str(other.resolve())) == 'deny'


def test_the_hook_process_reads_the_allowlist_file_and_fails_closed_without_it(lib, tmp_path):
    project, _, allowed, sibling = lib
    listing = tmp_path / 'readable.json'
    listing.write_text(json.dumps([allowed]))

    def run(file_path, flag_file):
        event = json.dumps({'tool_name': 'Read', 'tool_input': {'file_path': file_path}})
        p = subprocess.run([sys.executable, str(Path(guard.__file__)), str(project), '--readable', str(flag_file)],
                           input=event, capture_output=True, text=True)
        return p

    ok = run(allowed, listing)
    assert ok.returncode == 0 and json.loads(ok.stdout)['hookSpecificOutput']['permissionDecision'] == 'allow'
    no = run(sibling, listing)
    assert json.loads(no.stdout)['hookSpecificOutput']['permissionDecision'] == 'deny'
    missing = run(allowed, tmp_path / 'nope.json')
    assert missing.returncode == 2 and not missing.stdout.strip()


# -- how the allowlist reaches the CLI -------------------------------------------------------

def test_settings_carry_the_allowlist_flag_only_when_there_is_one(tmp_path):
    plain = desk_research._settings(None)['hooks']['PreToolUse'][0]['hooks'][0]['command']
    assert '--readable' not in plain
    with_list = desk_research._settings(None, False, tmp_path / 'readable.json')['hooks']['PreToolUse'][0]['hooks'][0]['command']
    assert '--readable' in with_list and 'readable.json' in with_list


def test_read_is_offered_without_a_project_only_for_allowed_files(tmp_path):
    assert desk_research._tools(None, True) == ('WebSearch', 'WebFetch')
    assert desk_research._tools(None, True, ['/x/a.png']) == ('WebSearch', 'WebFetch', 'Read')   # not Glob, not Grep
    assert desk_research._tools(tmp_path, True) == desk_research.TOOLS
    assert desk_research._tools(None, False, ['/x/a.png']) == ()
    argv = desk_research._argv('claude', tmp_path / 's.json', None, 'm', '', readable=['/x/lib/a.png', '/x/lib/b.png', '/y/c.jpg'])
    assert argv[argv.index('--tools') + 1] == 'WebSearch,WebFetch,Read'
    folders = [argv[i + 1] for i, a in enumerate(argv) if a == '--add-dir']
    assert folders == [str(Path('/x/lib')), str(Path('/y'))]


# -- what the chat sends ---------------------------------------------------------------------

@pytest.fixture
def env(tmp_path, monkeypatch):
    app = Flask(__name__)
    app.config['TESTING'] = True
    desk_routes.wire(load_projects_fn=lambda: [], load_project_fn=lambda pid: None,
                     store_path=tmp_path / 'desk.json', signals_path=tmp_path / 'sig.jsonl', uploads_root=tmp_path)
    app.register_blueprint(desk_routes.bp)
    calls: list[dict] = []
    monkeypatch.setattr(desk_story, '_call_model',
                        lambda provider, **kw: calls.append(dict(kw, provider=provider)) or json.dumps({'reply': 'ok', 'change': None}))
    monkeypatch.setattr(media, 'blind_reason', lambda provider, project: '')
    client = app.test_client()
    for rel in (IMG.format('a'), IMG.format('b'), IMG.format('c'), CLIP.format('clip')):
        f = tmp_path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b'bytes')
    return client, calls, tmp_path


def _save(client, pictures):
    """Save a board of one scene per entry; an entry is a picture ref or None."""
    scenes = [{'id': f'sc{i + 1}', 'label': f'S{i + 1}', 'line': f'line {i + 1}', 'duration_sec': 4, 'picture': p}
              for i, p in enumerate(pictures)]
    r = client.put('/api/desk/studio/draft1/storyboard', json={'rev': 0, 'scenes': scenes, 'story': 'A story.'})
    assert r.status_code == 200, r.get_json()
    return scenes


def _ref(rel, kind='image'):
    return {'path': rel, 'kind': kind, 'title': Path(rel).stem}


def _chat(client, scenes, message='what do you see?', scene=None, send_ids=True):
    sent = [({'id': s['id']} if send_ids else {}) | {'label': s['label'], 'line': s['line'], 'duration_sec': s['duration_sec']}
            for s in scenes]
    return client.post('/api/desk/storyboard/chat', json={'owner': OWNER, 'message': message, 'story': 'A story.',
                                                           'scenes': sent, 'scene': scene})


def _sent(calls, i=-1):
    return json.loads(calls[i]['stdin_text'])


def test_a_scene_picture_reaches_the_payload_and_the_allowlist(env):
    client, calls, root = env
    scenes = _save(client, [_ref(IMG.format('a')), None])
    assert _chat(client, scenes).status_code == 200
    sent = _sent(calls)
    real = str((root / IMG.format('a')).resolve())
    assert sent['scenes'][0]['picture'] == {'title': 'a', 'kind': 'image', 'file': real}
    assert 'picture' not in sent['scenes'][1]
    assert calls[0]['readable'] == [real]
    assert 'Read tool' in calls[0]['prompt'] and 'save it into the material library' in calls[0]['prompt']


def test_a_board_with_no_pictures_changes_nothing_but_the_one_sentence_about_online_video(env):
    client, calls, _ = env
    scenes = _save(client, [None, None])
    _chat(client, scenes)
    assert 'readable' not in calls[0]
    assert 'picture' not in json.dumps(_sent(calls)['scenes']) and 'media_notes' not in _sent(calls)
    assert 'Read tool' not in calls[0]['prompt'] and 'material library' in calls[0]['prompt']


def test_a_picture_path_in_the_request_is_never_trusted(env):
    client, calls, root = env
    scenes = _save(client, [None, None])
    sent = [{'id': s['id'], 'label': s['label'], 'line': s['line'], 'duration_sec': 4,
             'picture': {'path': str(root / IMG.format('a')), 'kind': 'image', 'file': 'C:/Windows/win.ini'}} for s in scenes]
    r = client.post('/api/desk/storyboard/chat', json={'owner': OWNER, 'message': 'hi', 'scenes': sent,
                                                       'picture': {'path': '/etc/passwd'}})
    assert r.status_code == 200
    assert 'readable' not in calls[0] and 'picture' not in json.dumps(_sent(calls)['scenes'])


def test_a_scene_id_the_board_does_not_have_gets_no_picture(env):
    client, calls, _ = env
    scenes = _save(client, [_ref(IMG.format('a'))])
    other = [dict(scenes[0], id='not-a-scene')]
    _chat(client, other)
    assert 'readable' not in calls[0]


def test_an_old_page_without_ids_is_matched_by_position_only_when_labels_agree(env):
    client, calls, _ = env
    scenes = _save(client, [_ref(IMG.format('a')), _ref(IMG.format('b'))])
    _chat(client, scenes, send_ids=False)
    assert len(calls[-1]['readable']) == 2
    renamed = [dict(scenes[0], label='Edited since the last save'), scenes[1]]
    _chat(client, renamed, send_ids=False)
    assert len(calls[-1]['readable']) == 1 and 'picture' not in _sent(calls)['scenes'][0]
    _chat(client, scenes[:1], send_ids=False)         # a different number of scenes: no guessing
    assert 'readable' not in calls[-1]


def test_the_selected_scene_is_shown_first_and_the_cap_is_said(env, monkeypatch):
    client, calls, root = env
    monkeypatch.setattr(media, 'MAX_PICTURES', 2)
    scenes = _save(client, [_ref(IMG.format('a')), _ref(IMG.format('b')), _ref(IMG.format('c'))])
    _chat(client, scenes, scene=3)
    sent = _sent(calls)
    shown = [str((root / IMG.format(n)).resolve()) for n in ('c', 'a')]
    assert calls[0]['readable'] == shown
    assert sent['scenes'][1]['picture']['viewable'] is False and 'only 2 pictures' in sent['scenes'][1]['picture']['why']
    assert 'file' not in sent['scenes'][1]['picture']


def test_a_picture_deleted_from_the_library_is_said_not_skipped(env):
    client, calls, root = env
    scenes = _save(client, [_ref(IMG.format('a'))])
    (root / IMG.format('a')).unlink()
    assert _chat(client, scenes).status_code == 200
    assert 'readable' not in calls[0]
    assert any('no longer in the library' in n for n in _sent(calls)['media_notes'])


def test_an_engine_that_cannot_read_files_is_told_so_and_gets_no_paths(env, monkeypatch):
    client, calls, _ = env
    monkeypatch.setattr(media, 'blind_reason', lambda provider, project: 'codex has no certified Desk read-only tool boundary')
    scenes = _save(client, [_ref(IMG.format('a'))])
    _chat(client, scenes)
    sent = _sent(calls)
    assert sent['scenes'][0]['picture'] == {'title': 'a', 'kind': 'image', 'viewable': False}
    assert 'readable' not in calls[0] and 'NOT viewable' in calls[0]['prompt']
    assert any('cannot be viewed on this engine' in n for n in sent['media_notes'])


# -- clips -------------------------------------------------------------------------------------

def _fake_ffmpeg(monkeypatch, duration='00:00:12.00', fail_frames=()):
    seen: list[list[str]] = []

    def run(cmd):
        seen.append(cmd)
        if '-frames:v' not in cmd:
            return 1, f'  Duration: {duration}, start: 0.000000, bitrate: 900 kb/s\nAt least one output file must be specified'
        dest = Path(cmd[-1])
        if int(dest.stem.rsplit('-', 1)[1]) in fail_frames:
            return 1, 'boom'
        dest.write_bytes(b'jpg')
        return 0, ''
    monkeypatch.setattr(media, '_ffmpeg', lambda: 'ffmpeg')
    monkeypatch.setattr(media, '_run', run)
    return seen


def test_a_clip_becomes_six_timestamped_frames_in_a_folder_that_is_gone_after_the_turn(env, monkeypatch):
    client, calls, root = env
    seen = _fake_ffmpeg(monkeypatch)
    held: list[bool] = []
    monkeypatch.setattr(desk_story, '_call_model',
                        lambda provider, **kw: held.append(all(Path(p).is_file() for p in kw['readable']))
                        or calls.append(kw) or json.dumps({'reply': 'ok', 'change': None}))
    scenes = _save(client, [_ref(CLIP.format('clip'), 'video')])
    assert _chat(client, scenes).status_code == 200
    row = _sent(calls)['scenes'][0]['picture']
    assert row['kind'] == 'video' and row['clip_sec'] == 12.0
    assert [f['at_sec'] for f in row['frames']] == [1.0, 3.0, 5.0, 7.0, 9.0, 11.0]
    assert calls[0]['readable'] == [f['file'] for f in row['frames']]
    assert held == [True]                                            # the files existed during the call...
    assert not any(Path(f['file']).exists() for f in row['frames'])  # ...and are gone after it
    assert len(seen) == 7 and str((root / CLIP.format('clip')).resolve()) in seen[1]


def test_no_ffmpeg_is_said_to_the_agent(env, monkeypatch):
    client, calls, _ = env
    monkeypatch.setattr(media, '_ffmpeg', lambda: None)
    scenes = _save(client, [_ref(CLIP.format('clip'), 'video')])
    _chat(client, scenes)
    sent = _sent(calls)
    assert sent['scenes'][0]['picture']['viewable'] is False and 'ffmpeg is not installed' in sent['scenes'][0]['picture']['why']
    assert any('ffmpeg is not available' in n for n in sent['media_notes'])
    assert 'readable' not in calls[0]


def test_a_failed_cut_is_logged_and_partial_frames_are_kept(env, monkeypatch, capsys):
    client, calls, _ = env
    _fake_ffmpeg(monkeypatch, fail_frames=(2, 4))
    scenes = _save(client, [_ref(CLIP.format('clip'), 'video')])
    _chat(client, scenes)
    assert [f['at_sec'] for f in _sent(calls)['scenes'][0]['picture']['frames']] == [1.0, 5.0, 9.0, 11.0]
    assert 'frame 2 of clip.mp4 failed' in capsys.readouterr().out


def test_a_clip_with_no_readable_duration_is_said(env, monkeypatch):
    client, calls, _ = env
    _fake_ffmpeg(monkeypatch, duration='N/A')
    scenes = _save(client, [_ref(CLIP.format('clip'), 'video')])
    _chat(client, scenes)
    sent = _sent(calls)
    assert sent['scenes'][0]['picture']['viewable'] is False and any('could not read frames' in n for n in sent['media_notes'])


# -- a serve-image URL in the message ----------------------------------------------------------

def test_a_serve_image_url_naming_a_library_file_is_a_reference_to_it(env):
    client, calls, root = env
    scenes = _save(client, [None])
    real = (root / IMG.format('b')).resolve()
    import urllib.parse
    url = 'http://192.168.0.2:5199/api/serve-image?path=' + urllib.parse.quote(str(real), safe='')
    _chat(client, scenes, message=f'can you see {url} ?')
    assert _sent(calls)['referenced'] == [{'title': 'b', 'kind': 'image', 'file': str(real)}] or \
        _sent(calls)['referenced'][0]['file'] == str(real)
    assert calls[0]['readable'] == [str(real)]


def test_a_serve_image_url_outside_the_library_is_ignored(env, tmp_path):
    client, calls, root = env
    scenes = _save(client, [None])
    outside = tmp_path.parent / 'outside-library.png'
    outside.write_bytes(b'x')
    inside_uploads_not_library = root / 'other' / 'x.png'
    inside_uploads_not_library.parent.mkdir()
    inside_uploads_not_library.write_bytes(b'x')
    video_dir = root / 'desk' / 'library' / 'image'
    dotted = f'{video_dir}/Storyboards/../../../../../{outside.name}'
    for target in (outside, inside_uploads_not_library, dotted, 'C:/Windows/win.ini', '../../etc/passwd'):
        _chat(client, scenes, message=f'see http://x/api/serve-image?path={target}')
        assert 'readable' not in calls[-1], target
        assert 'referenced' not in _sent(calls)
