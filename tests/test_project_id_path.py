"""P54 (MC-1179 pass 07): a project id must never walk out of the data dir.

`DATA_DIR / f'{project_id}.json'` took the id straight from a URL segment or,
in /api/projects/order, from the request body. An encoded backslash (Windows)
or an absolute path (any OS) read and rewrote any `.json` file on the machine.

Everything here runs against a temp directory. The "victim" is a neutral JSON
object beside the temp data dir; nothing outside tmp_path is named or touched.
"""
import json
import os
import sys
import threading
import types
from pathlib import Path
from urllib.parse import quote

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc import project_record_path as prp  # noqa: E402

VICTIM_BODY = json.dumps({'hooks': {'a': 1}, 'pw_salt': 'owner-salt'}, indent=2)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import server  # noqa: F401
    from mc.blueprints import local_auth as la
    from mc.blueprints import project_routes as pr

    monkeypatch.setattr(la, 'LOCAL_AUTH_PATH', tmp_path / 'local_auth.json')
    data_dir = tmp_path / 'projects'
    data_dir.mkdir()
    uploads = tmp_path / 'uploads'
    uploads.mkdir()
    monkeypatch.setattr(pr, 'DATA_DIR', data_dir)
    monkeypatch.setattr(pr, '_DATA_ROOT', tmp_path)
    monkeypatch.setattr(pr, 'UPLOADS_DIR', uploads)
    monkeypatch.setattr(pr, 'PROJECTS_BASE', tmp_path)
    monkeypatch.setattr(pr, 'get_manager',
                        lambda pid: types.SimpleNamespace(lock=threading.Lock()))
    server.app.config['TESTING'] = True
    c = server.app.test_client()
    victim = tmp_path / 'victim.json'
    victim.write_text(VICTIM_BODY, encoding='utf-8')
    return types.SimpleNamespace(client=c, pr=pr, data_dir=data_dir,
                                 tmp=tmp_path, victim=victim,
                                 before=victim.read_bytes())


def _intact(env):
    return env.victim.read_bytes() == env.before


needs_backslash = pytest.mark.skipif(
    os.name != 'nt', reason='a backslash is a path separator only on Windows')


# ── the helper itself ───────────────────────────────────────────────────────

REAL_IDS = [
    'mission_control', 'apex_trader', 'fl3_v1', 'day_trading_engulfing_scanner',
    'eden_s_math_learning_project', '_ideas', '_incognito', 'engulfing-analyst',
    'a', 'x' * 64,
]
BAD_IDS = [
    '', '.', '..', '.hidden', '..\\victim', '../victim', 'a/b', 'a\\b',
    'C:\\x\\victim', 'C:victim', '/abs/victim', 'a:b', 'a\0b', 'a..b',
    'x' * 65, ' leading', 'a b', None, 7, ['a'], {'a': 1},
]


@pytest.mark.parametrize('pid', REAL_IDS)
def test_real_id_shapes_get_a_path_in_the_data_dir(tmp_path, pid):
    p = prp.project_record_path(tmp_path, pid)
    assert p == tmp_path / f'{pid}.json'


@pytest.mark.parametrize('pid', BAD_IDS)
def test_bad_ids_are_refused(tmp_path, pid):
    assert prp.project_record_path(tmp_path, pid) is None


def test_sidecar_suffix_is_kept_inside_the_data_dir(tmp_path):
    assert (prp.project_record_path(tmp_path, 'proj', '_agent_log')
            == tmp_path / 'proj_agent_log.json')
    assert prp.project_record_path(tmp_path, '..\\proj', '_agent_log') is None


def test_second_check_rejects_a_parent_that_is_not_the_data_dir(tmp_path, monkeypatch):
    # Defence in depth: even if the id pattern ever admitted something odd, a
    # path whose resolved parent is not DATA_DIR is refused.
    monkeypatch.setattr(prp, '_PLAIN_ID', __import__('re').compile(r'.+', __import__('re').DOTALL))
    assert prp.project_record_path(tmp_path, '../victim') is None
    assert prp.project_record_path(tmp_path, '/abs/victim') is None


def test_a_refusal_logs_once_per_id(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(prp, '_log', lambda msg, **kw: seen.append(msg))
    prp._LOGGED_REFUSALS.clear()
    for _ in range(5):
        prp.project_record_path(tmp_path, '..\\victim')
    assert len(seen) == 1


# ── the live ids on this install still load ─────────────────────────────────

def test_ids_that_exist_in_data_projects_today_are_accepted():
    """Reads names only (never contents) from the real data dir."""
    real = PROJECT_ROOT / 'data' / 'projects'
    if not real.is_dir():
        pytest.skip('no data/projects here')
    suffixes = ('_agent_log.json', '_scribe_stats.json', '_router_stats.json',
                '_skill_stats.json', '_skill_stats_summary.json',
                '_topics.json', '_topic_state.json')
    stems = [f.stem for f in real.glob('*.json') if not f.name.endswith(suffixes)]
    assert stems, 'expected at least one record'
    refused = [s for s in stems if prp.project_record_path(real, s) is None]
    assert refused == []


@pytest.mark.parametrize('pid', ['_ideas', '_incognito', 'proj1'])
def test_underscore_ids_load_through_the_routes(env, pid):
    (env.data_dir / f'{pid}.json').write_text(
        json.dumps({'id': pid, 'name': pid, 'backlog': []}), encoding='utf-8')
    assert env.pr.load_project(pid)['id'] == pid
    r = env.client.get(f'/api/project/{pid}/backlog')
    assert r.status_code == 200


# ── the five measured rows ──────────────────────────────────────────────────

@needs_backslash
def test_row1_get_backlog_with_encoded_backslash_leaves_outside_file_alone(env):
    r = env.client.get('/api/project/' + quote('..\\victim', safe='') + '/backlog')
    assert r.status_code == 404
    assert _intact(env)


@needs_backslash
def test_row2_conversation_pin_with_encoded_backslash(env):
    r = env.client.post('/api/project/' + quote('..\\victim', safe='') + '/conversation-pin',
                        json={'conversation_id': 'c1', 'pinned': True})
    assert r.status_code == 404
    assert _intact(env)


@needs_backslash
def test_row3_post_backlog_with_absolute_path_id(env):
    absolute = str(env.victim)[:-len('.json')]
    r = env.client.post('/api/project/' + quote(absolute, safe='') + '/backlog',
                        json={'text': 'planted'})
    assert r.status_code == 404
    assert _intact(env)


def test_row4_order_body_with_absolute_path_touches_nothing(env):
    absolute = str(env.victim)[:-len('.json')]
    layout = env.tmp / 'grid_layout.json'
    r = env.client.post('/api/projects/order', json={'order': [absolute]})
    assert r.status_code == 400
    assert _intact(env)
    assert not layout.exists()


def test_row4b_order_body_with_relative_and_nonstring_ids(env):
    for bad in ('../victim', '..\\victim', 7, ['a'], {'a': 1}):
        r = env.client.post('/api/projects/order', json={'order': [bad]})
        assert r.status_code == 400, bad
        assert _intact(env)


def test_order_still_accepts_real_ids_and_spacers(env):
    for pid in ('_ideas', 'proj1'):
        (env.data_dir / f'{pid}.json').write_text(
            json.dumps({'id': pid, 'name': pid}), encoding='utf-8')
    r = env.client.post('/api/projects/order', json={'order': ['proj1', None, '_ideas']})
    assert r.status_code == 200
    assert json.loads((env.data_dir / 'proj1.json').read_text())['display_order'] == 0
    assert json.loads((env.data_dir / '_ideas.json').read_text())['display_order'] == 2


@needs_backslash
def test_row5_update_project_cannot_replace_a_foreign_record(env):
    r = env.client.post('/api/project/' + quote('..\\victim', safe='') + '/',
                        json={'pw_salt': 'caller-salt'})
    assert r.status_code in (400, 404, 405)
    r = env.client.post('/api/project/' + quote('..\\victim', safe=''),
                        json={'pw_salt': 'caller-salt', 'pw_hash': 'caller-hash'})
    assert r.status_code in (400, 404)
    assert _intact(env)


# ── every other route in the module that takes an id ────────────────────────

# DELETE /api/project/<id> is human-only (mc/human_only_gate.py): a request with no
# browser Origin is refused 403 before the id is looked at. These tests are about the
# id check, so they call as the dashboard does.
_BROWSER = {'Origin': 'http://localhost:5199'}


@needs_backslash
@pytest.mark.parametrize('method,suffix,body', [
    ('delete', '', None),
    ('post', '/generate_summary', {}),
    ('post', '/backlog', {'text': 'x'}),
    ('get', '/backlog', None),
    ('post', '/conversation-pin', {'conversation_id': 'c', 'pinned': True}),
])
def test_encoded_backslash_id_is_not_found_on_every_route(env, method, suffix, body):
    url = '/api/project/' + quote('..\\victim', safe='') + suffix
    kw = {'json': body} if body is not None else {}
    r = getattr(env.client, method)(url, headers=_BROWSER, **kw)
    assert r.status_code == 404
    assert _intact(env)


@needs_backslash
def test_absolute_path_id_is_not_found_on_every_route(env):
    absolute = quote(str(env.victim)[:-len('.json')], safe='')
    for method, suffix, body in [('delete', '', None),
                                 ('post', '/generate_summary', {}),
                                 ('post', '/backlog', {'text': 'x'}),
                                 ('get', '/backlog', None),
                                 ('post', '/conversation-pin', {'conversation_id': 'c'})]:
        kw = {'json': body} if body is not None else {}
        r = getattr(env.client, method)('/api/project/' + absolute + suffix, headers=_BROWSER, **kw)
        assert r.status_code == 404, (method, suffix, r.status_code)
    assert _intact(env)


def test_load_save_read_refuse_directly(env):
    for bad in ('../victim', '..\\victim', str(env.victim)[:-5]):
        assert env.pr.load_project(bad) is None
        assert env.pr._read_record_raw(bad) is None
        env.pr.save_project(bad, {'x': 1})      # must not raise, must not write
        assert env.pr._record_fingerprint(bad) is None
    assert _intact(env)
    assert [p.name for p in env.tmp.glob('*.json')] == ['victim.json']
