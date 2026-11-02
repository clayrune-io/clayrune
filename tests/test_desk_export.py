"""Local MP4 command construction, queue lifecycle and path authorization."""
import contextlib
from pathlib import Path

import pytest
from flask import Flask

from mc import desk_export as export, desk_pieces as pieces, desk as desk
from mc import desk_file_explorer as explorer
from mc.blueprints import desk_export_routes as routes


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(pieces, 'UPLOADS_ROOT', tmp_path / 'uploads')
    monkeypatch.setattr(desk, 'STORE_PATH', tmp_path / 'desk.json')
    monkeypatch.setattr(export, '_jobs', {})
    monkeypatch.setattr(explorer, '_home', tmp_path)
    monkeypatch.setattr(explorer, '_load_projects', lambda: [])
    # pytest's OS temp root can itself be in AppData or a hidden folder. Test
    # the real containment policy below without granting those locations live.
    monkeypatch.setattr(explorer.guard, 'check_chain', lambda path, home: None)
    monkeypatch.setattr(export.stitch, 'ffmpeg_path', lambda: '/approved/ffmpeg')
    monkeypatch.setattr(export, 'title_font', lambda: None)
    monkeypatch.setattr(export.addons, 'hold', lambda *args: contextlib.nullcontext())
    (tmp_path / 'uploads').mkdir()
    app = Flask(__name__)
    app.register_blueprint(routes.bp)
    return app.test_client(), tmp_path


def scene(sid='one', **kwargs):
    return dict(id=sid, label='A scene', line='', duration_sec=1.25, picture=None, **kwargs)


def immediate(monkeypatch):
    class Thread:
        def __init__(self, *, target, args, **_kw):
            self.target, self.args = target, args
        def start(self):
            self.target(*self.args)
    monkeypatch.setattr(export.threading, 'Thread', Thread)


def encoding(monkeypatch, *, failure=None):
    calls = []
    def run(cmd):
        calls.append(cmd)
        if cmd[-1] == '-':
            return 1, 'Stream map 0:a:0 matches no streams.'
        if failure:
            return failure
        Path(cmd[-1]).write_bytes(b'mp4')
        return 0, ''
    monkeypatch.setattr(export.stitch, 'run_ffmpeg', run)
    return calls


@pytest.mark.parametrize('kind,source,audio', [('black', None, False), ('image', Path('a b.png'), False), ('video', Path('clip.mp4'), False), ('video', Path('audio.mp4'), True)])
def test_normalized_commands(kind, source, audio):
    cmd = export.segment_command('/approved/ffmpeg', source, kind, 1.25, Path('out.mp4'), audio=audio)
    assert cmd[0] == '/approved/ffmpeg'
    assert cmd[cmd.index('-t') + 1] == '1.25'
    assert 'libx264' in cmd and 'aac' in cmd and 'yuv420p' in cmd
    assert '48000' in cmd and '2' in cmd and '-nostdin' in cmd
    assert 'tpad=stop_mode=clone:stop_duration=1.25' in cmd[cmd.index('-vf') + 1]
    assert '1280:720' in cmd[cmd.index('-vf') + 1]
    assert ('anullsrc=r=48000:cl=stereo' in cmd) is (not audio)
    assert ('trim=end_frame=1' in cmd[cmd.index('-vf') + 1]) is (kind == 'image')
    if kind == 'black':
        assert 'color=c=black:s=1280x720:r=30' in cmd
        assert not any('drawtext' in x for x in cmd)


def test_done_snapshot_order_download_and_reopen(env, monkeypatch):
    client, root = env
    immediate(monkeypatch)
    calls = encoding(monkeypatch)
    rows = [scene('two'), dict(scene('example'), placeholder=True), dict(scene('one'), duration_sec=.5)]
    response = client.post('/api/desk/exports', json={'owner': {'kind': 'studio', 'id': 'draft'}, 'scenes': rows})
    assert response.status_code == 202
    job = response.get_json()
    assert job['status'] == 'done' and job['progress'] == 100
    assert job['duration_sec'] == 1.75 and job['scene_count'] == 2
    assert [c[c.index('-t') + 1] for c in calls[:-1]] == ['1.25', '0.5']
    assert 'plain black' in job['message']
    assert Path(job['path']).parent == root / 'uploads/desk/library/video/Generated'
    assert len(list(Path(job['path']).parent.iterdir())) == 1, 'temporary segments cleaned'
    assert client.get(job['download']).data == b'mp4'
    assert 'attachment' in client.get(job['download']).headers['Content-Disposition']
    assert 'inline' in client.get(job['download'] + '?open=1').headers['Content-Disposition']
    assert client.get('/api/desk/exports?kind=studio&id=draft').get_json()['job']['id'] == job['id']
    # Current roots are checked again at download time.
    monkeypatch.setattr(explorer, '_home', root / 'elsewhere')
    assert client.get(job['download']).status_code == 403


def test_clip_with_audio_and_picture_sources(env, monkeypatch):
    client, root = env
    immediate(monkeypatch)
    calls = encoding(monkeypatch)
    monkeypatch.setattr(export, 'has_audio', lambda ffmpeg, source: True)
    rows = []
    for kind, ext in [('video', '.mp4'), ('image', '.png')]:
        path = root / f'uploads/desk/library/{kind}/Uploads/file{ext}'
        path.parent.mkdir(parents=True)
        path.write_bytes(b'source')
        rows.append(dict(scene(kind), picture={'path': path.relative_to(root / 'uploads').as_posix(), 'kind': kind}))
    job = client.post('/api/desk/exports', json={'owner': {'kind': 'studio', 'id': 'draft'}, 'scenes': rows}).get_json()
    assert job['status'] == 'done'
    assert '0:a:0' in calls[0] and 'anullsrc=r=48000:cl=stereo' not in calls[0]
    assert 'trim=end_frame=1' in calls[1][calls[1].index('-vf') + 1] and '1:a:0' in calls[1]


def test_audio_probe_absence_is_distinct_from_corruption(monkeypatch):
    monkeypatch.setattr(export.stitch, 'run_ffmpeg', lambda cmd: (0, ''))
    assert export.has_audio('/approved/ffmpeg', Path('clip.mp4'))
    monkeypatch.setattr(export.stitch, 'run_ffmpeg', lambda cmd: (1, 'matches no streams'))
    assert not export.has_audio('/approved/ffmpeg', Path('clip.mp4'))
    monkeypatch.setattr(export.stitch, 'run_ffmpeg', lambda cmd: (1, 'Invalid data found'))
    with pytest.raises(RuntimeError, match='Invalid data'):
        export.has_audio('/approved/ffmpeg', Path('clip.mp4'))


def test_explicit_font_path_and_literal_title_file():
    cmd = export.segment_command('/approved/ffmpeg', None, 'black', 1.0, Path('out.mp4'),
                                 font=Path('C:/Windows/Fonts/segoeui.ttf'), title_file=Path('C:/Media/title.txt'))
    filters = cmd[cmd.index('-vf') + 1]
    assert 'drawtext=' in filters and 'expansion=none' in filters
    assert "fontfile='C\\:/Windows/Fonts/segoeui.ttf'" in filters
    assert "textfile='C\\:/Media/title.txt'" in filters
    assert export._filter_path(Path("C:/Unusual'name/title.txt")) is None


def test_failed_drawtext_retries_black_and_discloses(env, monkeypatch):
    client, root = env
    immediate(monkeypatch)
    monkeypatch.setattr(export, 'title_font', lambda: root / 'font.ttf')
    calls = []
    def run(cmd):
        calls.append(cmd)
        if '-vf' in cmd and 'drawtext=' in cmd[cmd.index('-vf') + 1]:
            return 1, 'No such filter: drawtext'
        Path(cmd[-1]).write_bytes(b'mp4')
        return 0, ''
    monkeypatch.setattr(export.stitch, 'run_ffmpeg', run)
    job = export.start('studio', 'draft', [scene()])
    assert job['status'] == 'done' and 'plain black' in job['message']
    # pytest temp directories can have unusual characters: only expect a retry
    # when the font/text paths passed the conservative filter-path vocabulary.
    assert len(calls) == (3 if export._filter_path(root / 'font.ttf') else 2)


def test_failure_visible_and_partial_removed(env, monkeypatch):
    client, root = env
    immediate(monkeypatch)
    encoding(monkeypatch, failure=(13, 'encoder failed'))
    job = client.post('/api/desk/exports', json={'owner': {'kind': 'studio', 'id': 'draft'}, 'scenes': [scene()]}).get_json()
    assert job['status'] == 'failed' and 'encoder failed' in job['message']
    assert client.get(f"/api/desk/exports/{job['id']}/file").status_code == 409
    assert list((root / 'uploads/desk/library/video/Generated').iterdir()) == []


def test_queue_returns_without_encoding_duplicate_and_busy(env, monkeypatch):
    client, _ = env
    waiting = []
    class Thread:
        def __init__(self, *, target, args, **_kw):
            waiting.append((target, args))
        def start(self):
            pass
    monkeypatch.setattr(export.threading, 'Thread', Thread)
    calls = encoding(monkeypatch)
    payload = {'owner': {'kind': 'studio', 'id': 'draft'}, 'scenes': [scene()]}
    job = client.post('/api/desk/exports', json=payload).get_json()
    assert job['status'] == 'queued' and not calls
    assert client.get(f"/api/desk/exports/{job['id']}").get_json()['status'] == 'queued'
    assert client.post('/api/desk/exports', json=payload).get_json()['id'] == job['id']
    payload['owner']['id'] = 'other'
    assert client.post('/api/desk/exports', json=payload).status_code == 409
    assert len(waiting) == 1
    # Caller edits after queuing cannot mutate the captured scene list.
    waiting[0][0](*waiting[0][1])
    assert client.get(f"/api/desk/exports/{job['id']}").get_json()['status'] == 'done'


def test_approval_requests_card_and_clear_retry(env, monkeypatch):
    client, _ = env
    monkeypatch.setattr(export.stitch, 'ffmpeg_path', lambda: None)
    reasons = []
    monkeypatch.setattr(export.stitch, 'request_ffmpeg', lambda reason: reasons.append(reason) or '')
    calls = encoding(monkeypatch)
    payload = {'owner': {'kind': 'studio', 'id': 'draft'}, 'scenes': [scene()]}
    job = client.post('/api/desk/exports', json=payload).get_json()
    assert job['status'] == 'held' and 'click Export again' in job['message']
    assert len(reasons) == 1 and not calls and not export._jobs
    immediate(monkeypatch)
    monkeypatch.setattr(export.stitch, 'ffmpeg_path', lambda: '/approved/ffmpeg')
    assert client.post('/api/desk/exports', json=payload).get_json()['status'] == 'done'


@pytest.mark.parametrize('body', [None, [], {}, {'owner': []}, {'owner': {'kind': 'bogus', 'id': 'bad'}, 'scenes': []},
    {'owner': {'kind': 'studio', 'id': '../escape'}, 'scenes': []},
    {'owner': {'kind': 'studio', 'id': 'draft'}, 'scenes': []},
    {'owner': {'kind': 'studio', 'id': 'draft'}, 'scenes': [dict(scene(), duration_sec=-1)]},
    {'owner': {'kind': 'studio', 'id': 'draft'}, 'scenes': [dict(scene(), picture={'path': '../secret.png'})]},
])
def test_refuse_malformed_owners_scenes_and_paths(env, monkeypatch, body):
    client, _ = env
    calls = encoding(monkeypatch)
    response = client.post('/api/desk/exports', json=body)
    assert response.status_code == 400 and response.get_json()['error']
    assert not calls


def test_absent_job_and_thread_spawn_failure(env, monkeypatch):
    client, _ = env
    assert client.get('/api/desk/exports/unknown').status_code == 404
    assert 'restarted' in client.get('/api/desk/exports/unknown').get_json()['error']
    class Broken:
        def __init__(self, **kw):
            pass
        def start(self):
            raise RuntimeError('thread unavailable')
    monkeypatch.setattr(export.threading, 'Thread', Broken)
    job = export.start('studio', 'draft', [scene()])
    assert job['status'] == 'failed' and 'thread unavailable' in job['message']


def test_worker_rechecks_approval(env, monkeypatch):
    _, root = env
    export._jobs['job'] = {'status': 'queued'}
    monkeypatch.setattr(export.stitch, 'ffmpeg_path', lambda: None)
    export._run('job', [scene()], root / 'not-created.mp4')
    assert export.get_job('job')['status'] == 'failed'
    assert 'approval' in export.get_job('job')['message']
