"""Tests for the read-only "preserved worktrees" survey and its route.

The survey (mc.preserved_worktrees) must agree with the gc's own keep verdict
(agent_worktree.has_unmerged_work) and must never change a worktree or branch.
Every git fixture here is a THROWAWAY repo in a temp dir.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from flask import Flask

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from mc import preserved_worktrees as pw  # noqa: E402
from mc import project_sync  # noqa: E402
import mc.agent_worktree as w  # noqa: E402

RUNTIME_DIR = 'rt_state'   # stand-in for the linked runtime path (data/projects)


def _git(cwd, *args):
    r = subprocess.run(['git', *args], cwd=str(cwd), capture_output=True, text=True,
                       stdin=subprocess.DEVNULL)
    if r.returncode != 0:
        raise RuntimeError(f'git {" ".join(args)}: {r.stderr.strip()}')
    return r.stdout.strip()


@pytest.fixture(scope='module', autouse=True)
def _register():
    project_sync.register(0, None, lambda p, m: None, lambda p: None,
                          lambda p, v: None, lambda: '',
                          Path(tempfile.gettempdir()))
    w.register(lambda p, m: None, lambda p: None, lambda m: None)


@pytest.fixture(autouse=True)
def _fresh_cache():
    pw.clear_cache()
    yield
    pw.clear_cache()


@pytest.fixture
def repo():
    d = Path(tempfile.mkdtemp(prefix='pw_test_'))
    _git(d, 'init', '-q', '-b', 'master')
    _git(d, 'config', 'user.email', 't@t.local')
    _git(d, 'config', 'user.name', 'T')
    (d / 'app.py').write_text('x = 1\n', encoding='utf-8')
    (d / '.gitignore').write_text('.clayrune/\n', encoding='utf-8')
    _git(d, 'add', '.')
    _git(d, 'commit', '-q', '-m', 'baseline')
    yield d
    # Worktrees hold .git files pointing into the repo; ignore errors on Windows.
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def project(repo):
    return {'id': 'pwproj', 'name': 'PW Proj', 'project_path': str(repo),
            'worktree_shared_runtime': [RUNTIME_DIR]}


def _add_wt(repo, sid):
    """A registered agent worktree on clayrune/agent/<sid>, cut from master."""
    wt = repo / '.clayrune' / 'agents' / sid
    _git(repo, 'worktree', 'add', '-q', '-b', w.branch_name(sid), str(wt), 'master')
    return wt


def _commit(wt, name='work.txt'):
    (wt / name).write_text('agent work\n', encoding='utf-8')
    _git(wt, 'add', name)
    _git(wt, 'commit', '-q', '-m', 'agent work')


def _by_sid(survey):
    return {i['session_id']: i for i in survey['items']}


# ── classification ──────────────────────────────────────────────────────────

def test_clean_merged_worktree_is_not_listed(repo, project):
    _add_wt(repo, 'aaaaaaaaaaaa')
    s = pw.survey_project(project)
    assert s['worktrees_total'] == 1
    assert s['items'] == [] and s['preserved'] == 0


def test_commits_ahead_is_kept(repo, project):
    wt = _add_wt(repo, 'bbbbbbbbbbbb')
    _commit(wt)
    it = _by_sid(pw.survey_project(project))['bbbbbbbbbbbb']
    assert it['held_for'] == ['commits_ahead']
    assert it['commits_ahead'] == 1
    assert it['dirty'] is False and it['dirty_raw'] is False
    assert it['branch'] == 'clayrune/agent/bbbbbbbbbbbb'
    assert it['registered'] is True
    assert it['last_commit_at']


def test_real_uncommitted_file_is_dirty(repo, project):
    wt = _add_wt(repo, 'cccccccccccc')
    (wt / 'scratch.txt').write_text('wip\n', encoding='utf-8')
    it = _by_sid(pw.survey_project(project))['cccccccccccc']
    assert it['held_for'] == ['uncommitted']
    assert it['dirty'] is True and it['dirty_raw'] is True
    assert it['commits_ahead'] == 0


def test_runtime_files_only_is_flagged_not_dirty(repo, project):
    """Untracked files under the linked runtime path keep the worktree (the gc
    sees raw `git status`) but are not the agent's own work."""
    wt = _add_wt(repo, 'dddddddddddd')
    (wt / RUNTIME_DIR).mkdir()
    (wt / RUNTIME_DIR / 'stats.jsonl').write_text('{}\n', encoding='utf-8')
    it = _by_sid(pw.survey_project(project))['dddddddddddd']
    assert it['held_for'] == ['runtime_files_only']
    assert it['dirty'] is False and it['dirty_raw'] is True


def test_commit_and_dirty_report_both_reasons(repo, project):
    wt = _add_wt(repo, 'eeeeeeeeeeee')
    _commit(wt)
    (wt / 'more.txt').write_text('wip\n', encoding='utf-8')
    it = _by_sid(pw.survey_project(project))['eeeeeeeeeeee']
    assert it['held_for'] == ['commits_ahead', 'uncommitted']


def test_base_unresolved_fails_closed(repo, project):
    """A clean, merged worktree is still kept when the base ref cannot be
    resolved: has_unmerged_work answers True ("can't prove it's merged")."""
    _add_wt(repo, 'ffffffffffff')
    project = dict(project, worktree_base_branch='no-such-branch')
    s = pw.survey_project(project)
    it = _by_sid(s)['ffffffffffff']
    assert it['held_for'] == ['base_unresolved']
    assert it['commits_ahead'] is None
    assert w.has_unmerged_work(project, 'ffffffffffff') is True


def test_unregistered_directory_is_kept(repo, project):
    """A directory under .clayrune/agents that git does not know as a worktree."""
    (repo / '.clayrune' / 'agents' / 'abababababab').mkdir(parents=True)
    it = _by_sid(pw.survey_project(project))['abababababab']
    assert 'unregistered' in it['held_for']
    assert it['registered'] is False


def test_live_session_counts_as_in_use_not_preserved(repo, project):
    wt = _add_wt(repo, 'cdcdcdcdcdcd')
    _commit(wt)
    s = pw.survey_project(project, is_live=lambda sid: sid == 'cdcdcdcdcdcd')
    assert s['in_use'] == 1 and s['preserved'] == 0
    assert _by_sid(s)['cdcdcdcdcdcd']['live_session'] is True


def test_agrees_with_has_unmerged_work(repo, project):
    """The survey lists exactly the worktrees the gc would refuse to remove."""
    _add_wt(repo, '111111111111')                                   # clean
    _commit(_add_wt(repo, '222222222222'))                          # ahead
    (_add_wt(repo, '333333333333') / 'f.txt').write_text('x', encoding='utf-8')  # dirty
    wt4 = _add_wt(repo, '444444444444')                             # runtime only
    (wt4 / RUNTIME_DIR).mkdir()
    (wt4 / RUNTIME_DIR / 'a.jsonl').write_text('{}', encoding='utf-8')
    listed = set(_by_sid(pw.survey_project(project)))
    kept = {sid for sid in w.list_worktrees(project) if w.has_unmerged_work(project, sid)}
    assert listed == kept == {'222222222222', '333333333333', '444444444444'}


def test_survey_does_not_modify_anything(repo, project):
    wt = _add_wt(repo, '555555555555')
    _commit(wt)
    (wt / 'wip.txt').write_text('wip\n', encoding='utf-8')
    before = (_git(repo, 'worktree', 'list', '--porcelain'),
              _git(repo, 'for-each-ref', '--format=%(refname) %(objectname)'),
              _git(wt, 'status', '--porcelain'))
    pw.survey_project(project, include_size=True)
    after = (_git(repo, 'worktree', 'list', '--porcelain'),
             _git(repo, 'for-each-ref', '--format=%(refname) %(objectname)'),
             _git(wt, 'status', '--porcelain'))
    assert before == after
    assert (wt / 'wip.txt').exists() and (wt / 'work.txt').exists()


def test_size_is_opt_in(repo, project):
    wt = _add_wt(repo, '666666666666')
    _commit(wt)
    assert _by_sid(pw.survey_project(project))['666666666666']['size_bytes'] is None
    s = pw.survey_project(project, include_size=True)
    assert _by_sid(s)['666666666666']['size_bytes'] > 0
    assert s['size_complete'] is True


def test_cache_reuses_and_refresh_bypasses(repo, project):
    _commit(_add_wt(repo, '777777777777'))
    first = pw.survey_cached(project)
    assert first['cached'] is False
    assert pw.survey_cached(project)['cached'] is True
    assert pw.survey_cached(project, refresh=True)['cached'] is False


# ── route ───────────────────────────────────────────────────────────────────

@pytest.fixture
def client(repo, project, monkeypatch):
    from mc.blueprints import preserved_worktrees_routes as routes
    monkeypatch.setattr(routes.project_routes, 'load_projects', lambda: [project])
    monkeypatch.setattr(routes.project_routes, 'load_project',
                        lambda pid: project if pid == project['id'] else None)
    app = Flask(__name__)
    app.register_blueprint(routes.bp)
    return app.test_client()


def test_route_full_listing_shape(repo, client):
    _commit(_add_wt(repo, '888888888888'))
    r = client.get('/api/worktrees/preserved?refresh=1')
    assert r.status_code == 200
    d = r.get_json()
    assert d['totals'] == {'preserved': 1, 'in_use': 0, 'worktrees_total': 1,
                           'preserved_bytes': None}
    [p] = d['projects']
    assert p['project_id'] == 'pwproj' and p['name'] == 'PW Proj'
    [it] = p['items']
    for k in ('session_id', 'branch', 'last_commit_at', 'commits_ahead', 'dirty',
              'held_for', 'live_session', 'path'):
        assert k in it


def test_route_summary_has_counts_and_no_items(repo, client):
    _commit(_add_wt(repo, '999999999999'))
    d = client.get('/api/worktrees/preserved?summary=1&refresh=1').get_json()
    assert d['totals']['preserved'] == 1
    [p] = d['projects']
    assert p['preserved'] == 1 and 'items' not in p


def test_route_size_requires_project(client):
    r = client.get('/api/worktrees/preserved?size=1')
    assert r.status_code == 400
    assert 'project' in r.get_json()['error']


def test_route_size_with_project_ok(repo, client):
    _commit(_add_wt(repo, 'a1a1a1a1a1a1'))
    r = client.get('/api/worktrees/preserved?project=pwproj&size=1&refresh=1')
    assert r.status_code == 200
    assert r.get_json()['projects'][0]['items'][0]['size_bytes'] > 0


def test_route_unknown_project_404(client):
    assert client.get('/api/worktrees/preserved?project=nope').status_code == 404


@pytest.mark.parametrize('method', ['post', 'put', 'patch', 'delete'])
def test_route_has_no_write_methods(client, method):
    r = getattr(client, method)('/api/worktrees/preserved')
    assert r.status_code == 405


def test_route_registered_get_only():
    from mc.blueprints import preserved_worktrees_routes as routes
    app = Flask(__name__)
    app.register_blueprint(routes.bp)
    rules = [r for r in app.url_map.iter_rules() if r.rule.startswith('/api/worktrees')]
    assert len(rules) == 1
    assert rules[0].methods - {'HEAD', 'OPTIONS'} == {'GET'}


# ── disk use and unregistered folders ───────────────────────────────────────

def _wait_measured(project, timeout=30.0):
    import time
    t = time.monotonic() + timeout
    while time.monotonic() < t:
        st = pw.disk_status(project)
        if st['state'] == 'measured':
            return st
        time.sleep(0.05)
    raise AssertionError('disk measurement did not finish')


def test_measure_counts_every_folder_and_skips_loose_files(repo, project):
    wt = _add_wt(repo, 'd1d1d1d1d1d1')
    (wt / 'big.bin').write_bytes(b'x' * 5000)
    stray = repo / '.clayrune' / 'agents' / 'e2e2e2e2e2e2'
    (stray / 'sub').mkdir(parents=True)
    (stray / 'sub' / 'f.txt').write_bytes(b'y' * 300)
    (repo / '.clayrune' / 'agents' / 'gc_cache.json').write_text('{}', encoding='utf-8')
    m = pw.measure_agents_dir(project)
    assert m['complete'] is True and m['folders'] == 2
    assert set(m['per_folder']) == {'d1d1d1d1d1d1', 'e2e2e2e2e2e2'}
    assert m['per_folder']['e2e2e2e2e2e2'] == {'bytes': 300, 'files': 1, 'link': False}
    assert m['per_folder']['d1d1d1d1d1d1']['bytes'] >= 5000
    assert m['bytes'] == sum(v['bytes'] for v in m['per_folder'].values())


def test_measure_without_agents_dir_is_none(repo, project):
    assert pw.measure_agents_dir(project) is None
    assert pw.unregistered_folders(project) is None


def test_measure_budget_marks_incomplete(repo, project):
    (repo / '.clayrune' / 'agents' / 'f3f3f3f3f3f3').mkdir(parents=True)
    m = pw.measure_agents_dir(project, budget=-1)
    assert m['complete'] is False


def test_measure_does_not_follow_a_link(repo, project, tmp_path):
    import os
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'huge.bin').write_bytes(b'z' * 9000)
    agents = repo / '.clayrune' / 'agents'
    (agents / 'a4a4a4a4a4a4').mkdir(parents=True)
    try:
        os.symlink(str(outside), str(agents / 'a4a4a4a4a4a4' / 'lnk'), target_is_directory=True)
    except (OSError, NotImplementedError):
        if os.name != 'nt':
            pytest.skip('cannot create a directory link here')
        r = subprocess.run(['cmd', '/c', 'mklink', '/J',
                            str(agents / 'a4a4a4a4a4a4' / 'lnk'), str(outside)],
                           capture_output=True, stdin=subprocess.DEVNULL)
        if r.returncode != 0:
            pytest.skip('cannot create a junction here')
    m = pw.measure_agents_dir(project)
    assert m['per_folder']['a4a4a4a4a4a4']['bytes'] == 0


def test_start_measure_is_background_and_single_flight(repo, project):
    (repo / '.clayrune' / 'agents' / 'b5b5b5b5b5b5').mkdir(parents=True)
    assert pw.disk_status(project)['state'] == 'unmeasured'
    assert pw.start_measure(project) is True
    st = _wait_measured(project)
    assert st['folders'] == 1 and st['age_s'] is not None
    # a fresh result is kept under max_age; force (max_age=None) re-measures
    assert pw.start_measure(project, max_age=600) is False
    assert pw.start_measure(project, max_age=None) is True
    _wait_measured(project)


def test_unregistered_folders_lists_only_unknown_dirs(repo, project):
    _add_wt(repo, 'c6c6c6c6c6c6')
    (repo / '.clayrune' / 'agents' / 'zzzzzzzzzzzz').mkdir(parents=True)
    (repo / '.clayrune' / 'agents' / 'loose.txt').write_text('x', encoding='utf-8')
    u = pw.unregistered_folders(project)
    assert [r['name'] for r in u] == ['zzzzzzzzzzzz']


def test_unregistered_none_when_git_list_unreadable(repo, project, monkeypatch):
    (repo / '.clayrune' / 'agents' / 'yyyyyyyyyyyy').mkdir(parents=True)
    monkeypatch.setattr(pw, '_porcelain_worktrees', lambda base: {})
    assert pw.unregistered_folders(project) is None


def test_survey_does_not_measure_disk(repo, project):
    _commit(_add_wt(repo, 'd7d7d7d7d7d7'))
    pw.survey_project(project)
    assert pw.disk_status(project)['state'] == 'unmeasured'


def test_items_with_disk_sums_only_kept_folders(repo, project):
    _commit(_add_wt(repo, 'e8e8e8e8e8e8'))
    _commit(_add_wt(repo, 'f9f9f9f9f9f9'))
    items = pw.survey_project(project)['items']
    _, before = pw.items_with_disk(project, items)
    assert before['bytes'] is None and before['complete'] is False
    pw.start_measure(project)
    _wait_measured(project)
    live = [dict(i, live_session=(i['session_id'] == 'f9f9f9f9f9f9')) for i in items]
    out, pd = pw.items_with_disk(project, live)
    per = pw._disk[project['id']]['last']['per_folder']
    assert pd['folders'] == 1 and pd['complete'] is True
    assert pd['bytes'] == per['e8e8e8e8e8e8']['bytes'] > 0
    assert all(i['size_bytes'] is not None for i in out)


def test_route_measure_starts_walk_and_reports_disk(repo, client, project):
    _commit(_add_wt(repo, 'aaaa11112222'))
    (repo / '.clayrune' / 'agents' / 'unreg0000000').mkdir(parents=True)
    d = client.get('/api/worktrees/preserved?refresh=1').get_json()
    [p] = d['projects']
    assert p['disk']['state'] == 'unmeasured'
    assert [u['name'] for u in p['unregistered']] == ['unreg0000000']
    assert d['totals']['preserved_bytes'] is None
    client.get('/api/worktrees/preserved?measure=1')
    _wait_measured(project)
    d = client.get('/api/worktrees/preserved').get_json()
    [p] = d['projects']
    assert p['disk']['state'] == 'measured'
    assert d['totals']['preserved_bytes'] == p['preserved_disk']['bytes'] > 0
    assert p['unregistered'][0]['size_bytes'] == 0
    assert p['unregistered_disk'] == {'folders': 1, 'measured': 1, 'bytes': 0}


def test_route_summary_drops_unregistered_rows_keeps_count(repo, client):
    _commit(_add_wt(repo, 'bbbb11112222'))
    (repo / '.clayrune' / 'agents' / 'unreg1111111').mkdir(parents=True)
    d = client.get('/api/worktrees/preserved?summary=1&refresh=1').get_json()
    [p] = d['projects']
    assert 'unregistered' not in p and p['unregistered_count'] == 1
