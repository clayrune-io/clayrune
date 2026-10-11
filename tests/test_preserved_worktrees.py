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
    assert d['totals'] == {'preserved': 1, 'in_use': 0, 'worktrees_total': 1}
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
