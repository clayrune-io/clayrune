"""P55 (MC-1179 pass 07): a single-field writer must not undo another writer.

The lost-update guard in `save_project` restores rows another writer ADDED. A
row it CHANGED or DELETED is invisible to it, so a stale writer's copy won: a
closed backlog item reopened, a deleted one came back.

Each test interleaves two writers deterministically. Writer B is the code under
test; when B reaches `save_project` the test starts writer A on another thread
and gives it one second to finish first. If B holds no lock across its read and
write, A finishes inside that window and B then saves a stale copy. If B holds
the project's write lock, A waits and applies on the fresh record afterwards.
"""
import json
import sys
import threading
import types
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc.project_mutate import mutate_project  # noqa: E402


def _seed():
    return {
        'id': 'proj1', 'name': 'Proj 1', 'status': 'active',
        'backlog': [
            {'id': 'one', 'text': 'first', 'status': 'open'},
            {'id': 'two', 'text': 'second', 'status': 'open'},
        ],
        'activity_log': [], 'pinned_conversations': [],
    }


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import server  # noqa: F401
    from mc.blueprints import local_auth as la
    from mc.blueprints import project_routes as pr

    monkeypatch.setattr(la, 'LOCAL_AUTH_PATH', tmp_path / 'local_auth.json')
    data_dir = tmp_path / 'projects'
    data_dir.mkdir()
    (tmp_path / 'uploads').mkdir()
    monkeypatch.setattr(pr, 'DATA_DIR', data_dir)
    monkeypatch.setattr(pr, '_DATA_ROOT', tmp_path)
    monkeypatch.setattr(pr, 'UPLOADS_DIR', tmp_path / 'uploads')
    monkeypatch.setattr(pr, 'PROJECTS_BASE', tmp_path)
    monkeypatch.setattr(pr, 'get_manager',
                        lambda pid: types.SimpleNamespace(lock=threading.Lock()))
    (data_dir / 'proj1.json').write_text(json.dumps(_seed()), encoding='utf-8')
    server.app.config['TESTING'] = True
    return types.SimpleNamespace(pr=pr, client=server.app.test_client(),
                                 data_dir=data_dir, monkeypatch=monkeypatch)


def _disk(env, pid='proj1'):
    return json.loads((env.data_dir / f'{pid}.json').read_text(encoding='utf-8'))


def _race(env, writer_a):
    """When the calling thread first saves, run `writer_a` on another thread
    and wait up to 1 s for it. Returns a handle: `.errors` is what writer A
    raised and `.join()` waits for it, so call that before reading the disk
    (under the lock fix, A finishes only after B has saved)."""
    pr = env.pr
    real_save = pr.save_project
    main = threading.get_ident()
    state = {'done': False}
    errors = []
    threads = []

    def a_body():
        try:
            writer_a()
        except Exception as e:  # pragma: no cover - surfaced by the assert
            errors.append(e)

    def save(project_id, data):
        if threading.get_ident() == main and not state['done']:
            state['done'] = True
            t = threading.Thread(target=a_body)
            threads.append(t)
            t.start()
            t.join(timeout=1.0)
        return real_save(project_id, data)

    def join():
        for t in threads:
            t.join(timeout=5.0)
            assert not t.is_alive(), 'writer A never finished'
        return errors

    env.monkeypatch.setattr(pr, 'save_project', save)
    return types.SimpleNamespace(errors=errors, join=join)


def _writer_a(env):
    """Closes item one and deletes item two, on the fresh record."""
    def fn(p):
        for it in p['backlog']:
            if it['id'] == 'one':
                it['status'] = 'done'
        p['backlog'] = [it for it in p['backlog'] if it['id'] != 'two']
        return True
    return lambda: mutate_project('proj1', fn)


def _a_changes_survived(env):
    d = _disk(env)
    by_id = {it['id']: it for it in d['backlog']}
    assert by_id['one']['status'] == 'done', 'a closed item reopened'
    assert 'two' not in by_id, 'a deleted item came back'


# ── mutate_project itself ───────────────────────────────────────────────────

def test_mutate_returns_fn_result_and_saves(env):
    out = mutate_project('proj1', lambda p: p.update(status='paused') or 'ok')
    assert out == 'ok'
    assert _disk(env)['status'] == 'paused'


def test_mutate_on_missing_or_refused_id_does_not_call_fn(env):
    called = []
    assert mutate_project('nope', lambda p: called.append(1)) is None
    assert mutate_project('..\\proj1', lambda p: called.append(1)) is None
    assert called == []


def test_mutate_saves_nothing_when_fn_raises(env):
    def boom(p):
        p['status'] = 'broken'
        raise RuntimeError('x')
    with pytest.raises(RuntimeError):
        mutate_project('proj1', boom)
    assert _disk(env)['status'] == 'active'


def test_two_mutations_in_sequence_both_survive(env):
    mutate_project('proj1', lambda p: p['backlog'].pop())
    mutate_project('proj1', lambda p: p.update(status='paused'))
    d = _disk(env)
    assert d['status'] == 'paused' and [i['id'] for i in d['backlog']] == ['one']


# ── the single-field writers ────────────────────────────────────────────────

def test_activity_log_writer_does_not_undo_a_concurrent_edit(env):
    race = _race(env, _writer_a(env))
    env.pr._log_agent_activity('proj1', 'agent did a thing')
    assert race.join() == []
    _a_changes_survived(env)
    d = _disk(env)
    assert [e['msg'] for e in d['activity_log']] == ['agent did a thing']


def test_conversation_pin_does_not_undo_a_concurrent_edit(env):
    race = _race(env, _writer_a(env))
    r = env.client.post('/api/project/proj1/conversation-pin',
                        json={'conversation_id': 'conv-1', 'pinned': True})
    assert r.status_code == 200 and race.join() == []
    _a_changes_survived(env)
    assert _disk(env)['pinned_conversations'] == ['conv-1']


def test_order_route_does_not_undo_a_concurrent_edit(env):
    race = _race(env, _writer_a(env))
    r = env.client.post('/api/projects/order', json={'order': [None, 'proj1']})
    assert r.status_code == 200 and race.join() == []
    _a_changes_survived(env)
    assert _disk(env)['display_order'] == 1


def test_github_disconnect_does_not_undo_a_concurrent_edit(env):
    d = _disk(env)
    d.update(github_repo='o/r', github_sync_enabled=True)
    (env.data_dir / 'proj1.json').write_text(json.dumps(d), encoding='utf-8')
    race = _race(env, _writer_a(env))
    r = env.client.post('/api/project/proj1/github/disconnect')
    assert r.status_code == 200 and race.join() == []
    _a_changes_survived(env)
    assert _disk(env)['github_repo'] == ''


def test_github_setup_does_not_undo_a_concurrent_edit(env):
    env.monkeypatch.setattr(env.pr._gh_sync, 'validate_repo', lambda repo: (True, None))
    env.monkeypatch.setattr(env.pr._gh_sync, 'sync_project', lambda pid: (True, 'ok'))
    race = _race(env, _writer_a(env))
    r = env.client.post('/api/project/proj1/github/setup', json={'repo': 'o/r'})
    assert r.status_code == 200 and race.join() == []
    _a_changes_survived(env)
    assert _disk(env)['github_repo'] == 'o/r'


def test_summary_writer_holds_no_lock_across_the_model_call_and_keeps_edits(env):
    pr = env.pr
    seen = {}

    def fake_model(provider, **kw):
        # Another writer must be able to finish while the "model" runs: if the
        # route held the project lock here, this thread could not complete.
        t = threading.Thread(target=_writer_a(env))
        t.start()
        t.join(timeout=2.0)
        seen['writer_finished_during_model_call'] = not t.is_alive()
        return json.dumps({'emoji': 'X', 'summary': 'A short summary of the project here.'})

    env.monkeypatch.setattr(pr._agent_runtime, 'run_text_transform', fake_model)
    r = env.client.post('/api/project/proj1/generate_summary', json={})
    assert r.status_code == 200
    assert seen['writer_finished_during_model_call'] is True
    _a_changes_survived(env)
    d = _disk(env)
    assert d['summary'] == 'A short summary of the project here.' and d['emoji'] == 'X'
