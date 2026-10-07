"""Merge review gate (MC-1075): no agent branch lands on the base branch unless
an adversarial review of its CURRENT tip SHA is on record with verdict pass.

Every git test runs against a THROWAWAY repo in a temp dir, same as
test_agent_worktree.py. Covers: merge_back's `awaiting_review` status (no
record / stale SHA / changes_requested / pass / gate off), the record rules
(self-review, non-tip SHA), the record route's caller attribution, gc/teardown
never deleting a held branch, and the hand-merge check + opt-in hook.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from flask import Flask

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from mc import caller_attribution as ca, project_sync  # noqa: E402
import mc.agent_worktree as w  # noqa: E402
import mc.merge_review_gate as gate  # noqa: E402
from mc.blueprints import merge_review_routes as rr, project_routes  # noqa: E402
from mc.state import agent_sessions  # noqa: E402

CHECK = REPO_ROOT / 'tools' / 'merge-review-check.py'


def _git(cwd, *args, check=True):
    r = subprocess.run(['git', *args], cwd=str(cwd), capture_output=True, text=True,
                       stdin=subprocess.DEVNULL)
    if check and r.returncode != 0:
        raise RuntimeError(f'git {" ".join(args)}: {r.stderr.strip()}')
    return r.stdout.strip()


@pytest.fixture(scope='module', autouse=True)
def _register():
    project_sync.register(0, None, lambda p, m: None, lambda p: None,
                          lambda p, v: None, lambda: '',
                          Path(tempfile.gettempdir()))
    w.register(lambda p, m: None, lambda p: None, lambda m: None)


@pytest.fixture(autouse=True)
def _store(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, 'STORE_DIR', tmp_path / 'reviews')


@pytest.fixture
def repo():
    d = Path(tempfile.mkdtemp(prefix='mrg_test_'))
    _git(d, 'init', '-q', '-b', 'master')
    _git(d, 'config', 'user.email', 't@t.local')
    _git(d, 'config', 'user.name', 'T')
    (d / 'app.py').write_text('def one():\n    return "orig one"\n', encoding='utf-8')
    (d / 'other.py').write_text('X = 1\n', encoding='utf-8')
    (d / '.gitignore').write_text('.clayrune/\n', encoding='utf-8')
    _git(d, 'add', '.')
    _git(d, 'commit', '-q', '-m', 'baseline')
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def project(repo):
    return {'id': 'testproj', 'project_path': str(repo), 'merge_requires_review': True}


def _commit(tree, body, name='app.py'):
    (Path(tree) / name).write_text(body, encoding='utf-8')
    _git(tree, 'add', '.')
    _git(tree, 'commit', '-q', '-m', f'edit {name}')
    return _git(tree, 'rev-parse', 'HEAD')


def _agent(project, sid, body='def one():\n    return "AGENT"\n'):
    _, path = w.create(project, sid)
    return path, _commit(path, body)


def _review(project, sid, sha, verdict='pass', reviewer='reviewer-session'):
    return gate.record(project, w.branch_name(sid), sha=sha, verdict=verdict,
                       reviewer_session=reviewer)


def _master_has(repo, text):
    return text in (repo / 'app.py').read_text(encoding='utf-8')


# ── merge_back ──────────────────────────────────────────────────────────────

def test_gate_off_by_default_merges_as_before(project, repo):
    project.pop('merge_requires_review')
    _agent(project, 'off1')
    status, detail = w.merge_back(project, 'off1')
    assert status == 'clean', detail
    assert _master_has(repo, 'AGENT')


def test_gate_explicitly_false_merges_as_before(project, repo):
    project['merge_requires_review'] = False
    _agent(project, 'off2')
    assert w.merge_back(project, 'off2')[0] == 'clean'


def test_gate_setting_is_strictly_boolean(project, repo):
    """A hand-edited "true" string is not the gate being on (nor off by luck)."""
    project['merge_requires_review'] = 'true'
    _agent(project, 'str1')
    assert w.merge_back(project, 'str1')[0] == 'clean'


def test_no_record_holds_the_merge_and_keeps_everything(project, repo):
    path, tip = _agent(project, 'nr1')
    before = _git(repo, 'rev-parse', 'master')
    status, detail = w.merge_back(project, 'nr1')
    assert status == 'awaiting_review'
    assert 'no review on record' in detail and tip[:8] in detail
    assert _git(repo, 'rev-parse', 'master') == before, 'master moved'
    assert not _master_has(repo, 'AGENT')
    assert Path(path).exists()
    assert _git(repo, 'branch', '--list', w.branch_name('nr1')).strip()


def test_review_of_an_older_sha_does_not_count(project, repo):
    path, tip1 = _agent(project, 'st1')
    _review(project, 'st1', tip1)
    tip2 = _commit(path, 'def one():\n    return "AGENT v2"\n')
    status, detail = w.merge_back(project, 'st1')
    assert status == 'awaiting_review'
    assert tip2[:8] in detail
    assert not _master_has(repo, 'AGENT')


def test_changes_requested_holds_the_merge(project, repo):
    _, tip = _agent(project, 'cr1')
    _review(project, 'cr1', tip, verdict='changes_requested')
    status, detail = w.merge_back(project, 'cr1')
    assert status == 'awaiting_review' and 'requested changes' in detail
    assert not _master_has(repo, 'AGENT')


def test_later_changes_requested_withdraws_an_earlier_pass(project, repo):
    _, tip = _agent(project, 'wd1')
    _review(project, 'wd1', tip)
    _review(project, 'wd1', tip, verdict='changes_requested', reviewer='second-reviewer')
    assert w.merge_back(project, 'wd1')[0] == 'awaiting_review'


def test_pass_on_the_current_tip_merges(project, repo):
    _, tip = _agent(project, 'ps1')
    _review(project, 'ps1', tip)
    status, detail = w.merge_back(project, 'ps1')
    assert status == 'clean', detail
    assert _master_has(repo, 'AGENT')
    assert _git(repo, 'rev-parse', 'master') == tip


def test_pass_merge_commit_names_the_reviewed_sha(project, repo):
    """With master diverged the merge makes a commit; the exact reviewed sha is
    what gets merged and the message says so."""
    _, tip = _agent(project, 'ps2')
    _commit(repo, 'X = 2\n', name='other.py')
    _review(project, 'ps2', tip)
    status, detail = w.merge_back(project, 'ps2')
    assert status == 'clean', detail
    assert tip in _git(repo, 'rev-list', '--parents', '-n', '1', 'master')
    subject = _git(repo, 'log', '-1', '--format=%s', 'master')
    assert subject == f"Merge branch '{w.branch_name('ps2')}' (reviewed {tip[:8]})"


def test_gate_only_guards_the_base_branch(project, repo):
    """An explicit target_ref is the integration path's own call (hivemind)."""
    _git(repo, 'branch', 'integration')
    _agent(project, 'tg1')
    _git(repo, 'checkout', '-q', 'integration')
    status, _ = w.merge_back(project, 'tg1', target_ref='integration')
    assert status == 'clean'


def test_corrupt_store_fails_closed(project, repo, tmp_path):
    _, tip = _agent(project, 'cs1')
    _review(project, 'cs1', tip)
    store = gate.store_path('testproj')
    store.write_text('{ not json', encoding='utf-8')
    assert w.merge_back(project, 'cs1')[0] == 'awaiting_review'
    # Writing a new review keeps the unreadable file rather than overwriting it.
    _review(project, 'cs1', tip)
    assert store.with_name(store.name + '.corrupt').read_text(encoding='utf-8') == '{ not json'
    assert w.merge_back(project, 'cs1')[0] == 'clean'


# ── teardown never deletes a held branch ────────────────────────────────────

def test_held_branch_survives_has_unmerged_work_and_remove(project, repo):
    path, _ = _agent(project, 'td1')
    assert w.merge_back(project, 'td1')[0] == 'awaiting_review'
    assert w.has_unmerged_work(project, 'td1')
    ok, msg = w.remove(project, 'td1', delete_branch=True)
    assert not ok and 'refused' in msg
    assert Path(path).exists()
    assert _git(repo, 'branch', '--list', w.branch_name('td1')).strip()


def test_gc_preserves_awaiting_review_and_does_not_cache_it(project, repo):
    path, _ = _agent(project, 'gc1')
    out = w.gc_stale(project, live_session_ids=(), use_cache=True)
    assert 'gc1' in out['preserved'] and out['removed'] == 0 and out['merged'] == 0
    assert Path(path).exists()
    assert _git(repo, 'branch', '--list', w.branch_name('gc1')).strip()
    assert 'gc1' not in w._load_gc_cache(project), 'verdict cached; a later pass would be ignored'


def test_gc_merges_once_a_pass_is_recorded(project, repo):
    _, tip = _agent(project, 'gc2')
    assert 'gc2' in w.gc_stale(project, live_session_ids=(), use_cache=True)['preserved']
    _review(project, 'gc2', tip)
    out = w.gc_stale(project, live_session_ids=(), use_cache=True)
    assert out['merged'] == 1 and out['removed'] == 1
    assert _master_has(repo, 'AGENT')


# ── record rules ────────────────────────────────────────────────────────────

def test_self_review_is_refused_and_writes_nothing(project):
    _, tip = _agent(project, 'sr1')
    with pytest.raises(gate.ReviewRefused) as e:
        _review(project, 'sr1', tip, reviewer='sr1')
    assert e.value.status == 403 and 'self-review' in e.value.message
    assert gate.find('testproj', w.branch_name('sr1'), tip) is None


def test_sha_that_is_not_the_tip_is_refused(project):
    path, tip1 = _agent(project, 'ns1')
    _commit(path, 'def one():\n    return "AGENT v2"\n')
    with pytest.raises(gate.ReviewRefused) as e:
        _review(project, 'ns1', tip1)
    assert e.value.status == 409 and e.value.extra['tip'] != tip1


@pytest.mark.parametrize('sha', ['', 'abc123', None, 'z' * 40, 123])
def test_sha_must_be_a_full_commit_sha(project, sha):
    _agent(project, 'bs1')
    with pytest.raises(gate.ReviewRefused) as e:
        gate.record(project, w.branch_name('bs1'), sha=sha, verdict='pass',
                    reviewer_session='r')
    assert e.value.status == 400


def test_bad_verdict_and_unknown_branch_are_refused(project):
    _, tip = _agent(project, 'bv1')
    with pytest.raises(gate.ReviewRefused) as e:
        gate.record(project, w.branch_name('bv1'), sha=tip, verdict='approve',
                    reviewer_session='r')
    assert e.value.status == 400
    with pytest.raises(gate.ReviewRefused) as e:
        gate.record(project, w.branch_name('nonesuch'), sha=tip, verdict='pass',
                    reviewer_session='r')
    assert e.value.status == 404
    with pytest.raises(gate.ReviewRefused) as e:
        gate.record(project, 'master', sha=tip, verdict='pass', reviewer_session='r')
    assert e.value.status == 400


def test_record_carries_the_documented_fields(project):
    _, tip = _agent(project, 'rf1')
    rec = gate.record(project, w.branch_name('rf1'), sha=tip, verdict='pass',
                      reviewer_session='fenn-session', reviewer_character='Fenn',
                      report_path='docs/_journal/x.md')
    assert {'sha', 'branch', 'verdict', 'reviewer_session', 'reviewer_character',
            'report_path', 'at'} <= set(rec)
    assert gate.find('testproj', w.branch_name('rf1'), tip) == rec


# ── the route ───────────────────────────────────────────────────────────────

@pytest.fixture
def client(project, monkeypatch):
    monkeypatch.setattr(project_routes, 'load_project',
                        lambda pid: project if pid == 'testproj' else None)
    app = Flask(__name__)
    app.register_blueprint(rr.bp)
    with app.test_client() as c:
        yield c


def _as(monkeypatch, status, session_id='', detail='test'):
    monkeypatch.setattr(rr.caller_attribution, 'attribute_caller',
                        lambda *a, **k: ca.Attribution(status, session_id, detail=detail))


def _post(client, sid, **body):
    return client.post(f'/api/project/testproj/agent/{sid}/review', json=body)


def test_route_records_a_review_by_another_session(client, project, monkeypatch):
    _, tip = _agent(project, 'rt1')
    _as(monkeypatch, ca.ATTRIBUTED, 'fenn-session')
    agent_sessions['fenn-session'] = {'character': {'agent_name': 'Fenn'}}
    try:
        r = _post(client, 'rt1', sha=tip, verdict='pass', report_path='r.md',
                  reviewer_session='someone-else')   # a typed reviewer must be ignored
    finally:
        agent_sessions.pop('fenn-session', None)
    assert r.status_code == 200, r.get_json()
    rec = r.get_json()['record']
    assert rec['reviewer_session'] == 'fenn-session'
    assert rec['reviewer_character'] == 'Fenn'
    assert rec['attribution'] == 'attributed'
    assert w.merge_back(project, 'rt1')[0] == 'clean'


def test_route_refuses_the_branch_owner(client, project, monkeypatch):
    _, tip = _agent(project, 'rt2')
    _as(monkeypatch, ca.ATTRIBUTED, 'rt2')
    r = _post(client, 'rt2', sha=tip, verdict='pass', reviewer_session='fenn-session')
    assert r.status_code == 403 and 'self-review' in r.get_json()['error']
    assert w.merge_back(project, 'rt2')[0] == 'awaiting_review'


def test_route_refuses_when_the_caller_cannot_be_attributed(client, project, monkeypatch):
    _, tip = _agent(project, 'rt3')
    _as(monkeypatch, ca.UNAVAILABLE, detail='psutil not installed')
    r = _post(client, 'rt3', sha=tip, verdict='pass')
    assert r.status_code == 403 and 'cannot tell which session' in r.get_json()['error']
    assert gate.find('testproj', w.branch_name('rt3'), tip) is None


def test_route_refuses_an_unattributed_caller_and_writes_nothing(client, project, monkeypatch):
    """An unattributed caller (e.g. a builder's detached helper process) cannot
    be told apart from the branch owner, so its review must not be recorded."""
    _, tip = _agent(project, 'rt4')
    _as(monkeypatch, ca.UNATTRIBUTED)
    for verdict in ('pass', 'changes_requested'):
        r = _post(client, 'rt4', sha=tip, verdict=verdict)
        assert r.status_code == 403, r.get_json()
        assert 'self-review' in r.get_json()['error']
    assert gate.find('testproj', w.branch_name('rt4'), tip) is None
    assert w.merge_back(project, 'rt4')[0] == 'awaiting_review'


def test_route_refuses_a_stale_sha_with_the_current_tip(client, project, monkeypatch):
    path, tip1 = _agent(project, 'rt5')
    tip2 = _commit(path, 'def one():\n    return "AGENT v2"\n')
    _as(monkeypatch, ca.ATTRIBUTED, 'fenn-session')
    r = _post(client, 'rt5', sha=tip1, verdict='pass')
    assert r.status_code == 409 and r.get_json()['tip'] == tip2


def test_route_404_for_unknown_project_and_400_for_no_body(client, monkeypatch):
    _as(monkeypatch, ca.ATTRIBUTED, 'fenn-session')
    assert client.post('/api/project/nope/agent/x/review', json={}).status_code == 404
    assert client.post('/api/project/testproj/agent/x/review', data='x').status_code == 400


# ── hand merges: tools/merge-review-check.py + the opt-in hook ──────────────

def _install_project_record(data_root: Path, repo: Path, **extra):
    pdir = data_root / 'data' / 'projects'
    pdir.mkdir(parents=True, exist_ok=True)
    rec = {'id': 'testproj', 'project_path': str(repo), **extra}
    (pdir / 'testproj.json').write_text(json.dumps(rec), encoding='utf-8')


def _check(*args, env=None):
    r = subprocess.run([sys.executable, str(CHECK), *args], capture_output=True,
                       text=True, stdin=subprocess.DEVNULL, env={**os.environ, **(env or {})})
    return r.returncode, r.stdout + r.stderr


def test_check_script_needs_a_pass_for_the_current_tip(project, repo, tmp_path):
    data_root = tmp_path / 'install'
    _install_project_record(data_root, repo, merge_requires_review=True)
    path, tip1 = _agent(project, 'ck1')
    branch = w.branch_name('ck1')
    base = ['--repo', str(repo), '--data-root', str(data_root)]
    # The check reads the store the server wrote: point both at the same dir.
    gate.STORE_DIR = data_root / 'data' / gate.STORE_DIRNAME
    rc, out = _check(branch, *base)
    assert rc == 1 and 'BLOCKED' in out
    _review(project, 'ck1', tip1)
    assert _check(branch, *base)[0] == 0
    _commit(path, 'def one():\n    return "AGENT v2"\n')
    rc, out = _check(branch, *base)
    assert rc == 1, 'a pass for an older tip must not clear the new one'


def test_check_script_exit_2_when_it_cannot_decide(project, repo, tmp_path):
    rc, out = _check('clayrune/agent/x', '--repo', str(repo), '--data-root', str(tmp_path / 'empty'))
    assert rc == 2 and 'Not treating this as a pass' in out


def test_check_respect_setting_passes_when_gate_is_off(project, repo, tmp_path):
    data_root = tmp_path / 'install'
    _install_project_record(data_root, repo)               # setting absent = off
    _agent(project, 'ck2')
    args = [w.branch_name('ck2'), '--repo', str(repo), '--data-root', str(data_root)]
    assert _check(*args, '--respect-setting')[0] == 0
    assert _check(*args)[0] == 1                           # explicit check is strict


def test_merge_head_mode_blocks_an_unreviewed_agent_merge(project, repo, tmp_path):
    data_root = tmp_path / 'install'
    _install_project_record(data_root, repo, merge_requires_review=True)
    _agent(project, 'mh1')
    _commit(repo, 'X = 2\n', name='other.py')              # diverge so it is a real merge
    base = ['--merge-head', '--respect-setting', '--repo', str(repo),
            '--data-root', str(data_root)]
    assert _check(*base)[0] == 0, 'no merge in progress: nothing to gate'
    _git(repo, 'merge', '--no-commit', '--no-ff', w.branch_name('mh1'))
    rc, out = _check(*base)
    assert rc == 1 and 'BLOCKED' in out
    _git(repo, 'merge', '--abort')


def test_merge_head_mode_reads_the_merge_arguments_when_git_has_no_merge_head_yet(
        project, repo, tmp_path):
    """Measured on git 2.51: during pre-merge-commit for a fresh auto-merge
    MERGE_HEAD does not exist yet; git only exports GIT_REFLOG_ACTION."""
    data_root = tmp_path / 'install'
    _install_project_record(data_root, repo, merge_requires_review=True)
    _, tip = _agent(project, 'mh2')
    base = ['--merge-head', '--respect-setting', '--repo', str(repo),
            '--data-root', str(data_root)]
    branch = w.branch_name('mh2')
    assert _check(*base, env={'GIT_REFLOG_ACTION': 'merge ' + branch})[0] == 1
    assert _check(*base, env={'GIT_REFLOG_ACTION': 'merge -m msg ' + tip})[0] == 1
    assert _check(*base, env={'GIT_REFLOG_ACTION': 'merge master'})[0] == 0
    assert _check(*base, env={'GIT_REFLOG_ACTION': 'pull . ' + branch})[0] == 1
    assert _check(*base, env={'GIT_REFLOG_ACTION': 'pull --no-rebase --no-ff . ' + branch})[0] == 1
    assert _check(*base, env={'GIT_REFLOG_ACTION': 'pull . master'})[0] == 0


needs_sh = pytest.mark.skipif(shutil.which('sh') is None, reason='needs a POSIX sh')


def _hooked_repo(repo, tmp_path, **record_extra):
    """Copy the gate's files into `repo`, run the REAL installer from it, and
    point the gate's store at the data root the hooks' check script will read.
    Returns the hooks dir."""
    install = tmp_path / 'install'
    for rel in ('mc/__init__.py', 'mc/project_sync.py', 'mc/atomic_json.py',
                'mc/merge_review_gate.py', 'tools/merge-review-check.py',
                'tools/install-merge-review-hook.sh', 'tools/git-hooks/pre-merge-commit',
                'tools/git-hooks/commit-msg'):
        (install / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO_ROOT / rel, install / rel)
    _install_project_record(install, repo, **record_extra)
    hooks = repo / '.git' / 'hooks'
    assert not (hooks / 'pre-merge-commit').exists(), 'must not be installed automatically'
    assert not (hooks / 'commit-msg').exists(), 'must not be installed automatically'
    shutil.copytree(install / 'tools', repo / 'tools')
    shutil.copytree(install / 'mc', repo / 'mc')
    r = subprocess.run(['sh', str(repo / 'tools' / 'install-merge-review-hook.sh')],
                       cwd=str(repo), capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stderr
    shutil.copytree(install / 'data', repo / 'data', dirs_exist_ok=True)
    gate.STORE_DIR = repo / 'data' / gate.STORE_DIRNAME
    _git(repo, 'add', '-A')
    _git(repo, 'commit', '-q', '-m', 'tooling')
    return hooks


def _run(cwd, *args):
    return subprocess.run(['git', *args], cwd=str(cwd), capture_output=True, text=True,
                          stdin=subprocess.DEVNULL)


@needs_sh
def test_installed_hook_blocks_the_merge_commit_and_is_opt_in(project, repo, tmp_path):
    """End to end: run the real installer and let git itself refuse the merge."""
    hooks = _hooked_repo(repo, tmp_path, merge_requires_review=True)
    assert (hooks / 'pre-merge-commit').exists() and (hooks / 'commit-msg').exists()
    _agent(project, 'hk1')
    _commit(repo, 'X = 3\n', name='other.py')
    m = _run(repo, 'merge', '--no-edit', w.branch_name('hk1'))
    assert m.returncode != 0 and 'BLOCKED' in m.stderr, (m.stdout, m.stderr)
    _run(repo, 'merge', '--abort')
    tip = _git(repo, 'rev-parse', w.branch_name('hk1'))
    _review(project, 'hk1', tip)
    m = _run(repo, 'merge', '--no-edit', w.branch_name('hk1'))
    assert m.returncode == 0, (m.stdout, m.stderr)


@needs_sh
def test_commit_after_a_rejected_merge_is_refused_until_a_pass_is_recorded(
        project, repo, tmp_path):
    """Finding 1: the rejected merge leaves MERGE_HEAD behind, and `git commit
    --no-edit` used to finish it without any check."""
    _hooked_repo(repo, tmp_path, merge_requires_review=True)
    _agent(project, 'hk2')
    _commit(repo, 'X = 4\n', name='other.py')
    before = _git(repo, 'rev-parse', 'HEAD')
    m = _run(repo, 'merge', '--no-edit', w.branch_name('hk2'))
    assert m.returncode != 0 and 'BLOCKED' in m.stderr, (m.stdout, m.stderr)
    c = _run(repo, 'commit', '--no-edit')
    assert c.returncode != 0 and 'BLOCKED' in c.stderr, (c.stdout, c.stderr)
    assert _git(repo, 'rev-parse', 'HEAD') == before, 'the unreviewed merge must not land'
    tip = _git(repo, 'rev-parse', w.branch_name('hk2'))
    _review(project, 'hk2', tip)
    c = _run(repo, 'commit', '--no-edit')
    assert c.returncode == 0, (c.stdout, c.stderr)
    assert _git(repo, 'rev-parse', 'HEAD^2') == tip


def _advance(repo, branch):
    """Move `branch` one empty commit forward without checking it out."""
    tip = _git(repo, 'rev-parse', branch)
    new = _git(repo, 'commit-tree', tip + '^{tree}', '-p', tip, '-m', 'advance')
    _git(repo, 'update-ref', f'refs/heads/{branch}', new)
    return new


@needs_sh
def test_advancing_the_branch_does_not_unlock_a_rejected_merge(project, repo, tmp_path):
    """Round 2: MERGE_HEAD keeps the rejected SHA; moving the agent branch past
    it used to make the check match no branch tip and wave the commit through."""
    _hooked_repo(repo, tmp_path, merge_requires_review=True)
    _agent(project, 'hk5')
    _commit(repo, 'X = 7\n', name='other.py')
    before = _git(repo, 'rev-parse', 'HEAD')
    branch = w.branch_name('hk5')
    m = _run(repo, 'merge', '--no-edit', branch)
    assert m.returncode != 0 and 'BLOCKED' in m.stderr, (m.stdout, m.stderr)
    new_tip = _advance(repo, branch)
    _review(project, 'hk5', new_tip)     # a pass on the NEW tip covers nothing pending
    c = _run(repo, 'commit', '--no-edit')
    assert c.returncode != 0 and 'BLOCKED' in c.stderr, (c.stdout, c.stderr)
    assert _git(repo, 'rev-parse', 'HEAD') == before


@needs_sh
def test_merging_an_unreviewed_ancestor_of_an_agent_branch_is_gated(project, repo, tmp_path):
    """`<agent branch>~1`, or a copy-named branch at it, is still agent work."""
    _hooked_repo(repo, tmp_path, merge_requires_review=True)
    _agent(project, 'hk6')
    _commit(repo, 'X = 8\n', name='other.py')
    branch = w.branch_name('hk6')
    old = _git(repo, 'rev-parse', branch)
    _review(project, 'hk6', _advance(repo, branch))
    _git(repo, 'branch', 'copy', old)
    for target in (branch + '~1', 'copy'):
        m = _run(repo, 'merge', '--no-edit', target)
        assert m.returncode != 0 and 'BLOCKED' in m.stderr, (target, m.stdout, m.stderr)
        _run(repo, 'merge', '--abort')
    m = _run(repo, 'merge', '--no-edit', branch)   # the reviewed tip still merges
    assert m.returncode == 0, (m.stdout, m.stderr)


@needs_sh
def test_a_branch_forked_from_a_reviewed_agent_tip_does_not_block_it(project, repo, tmp_path):
    """Round 3: B forked from A's reviewed tip contains it too, and could never
    record a review for it (not B's tip). The review under A is enough."""
    _hooked_repo(repo, tmp_path, merge_requires_review=True)
    _, a = _agent(project, 'hk8')
    _review(project, 'hk8', a)
    _, bpath = w.create(project, 'hk9', base_ref=w.branch_name('hk8'))
    _review(project, 'hk9', _commit(bpath, 'B = 1\n', name='b.py'))
    _commit(repo, 'X = 10\n', name='other.py')
    m = _run(repo, 'merge', '--no-edit', w.branch_name('hk8'))
    assert m.returncode == 0, (m.stdout, m.stderr)
    assert _git(repo, 'rev-parse', 'HEAD^2') == a


@needs_sh
def test_a_feature_branch_an_agent_forked_from_still_merges(project, repo, tmp_path):
    """Round 3: an agent branch forked from an ordinary branch used to make that
    branch's own commits need an agent review."""
    _hooked_repo(repo, tmp_path, merge_requires_review=True)
    _git(repo, 'checkout', '-q', '-b', 'feature')
    _commit(repo, 'Y = 2\n', name='feature.py')
    _git(repo, 'checkout', '-q', '-')
    _, bpath = w.create(project, 'hk10', base_ref='feature')
    _commit(bpath, 'B = 2\n', name='b.py')
    _commit(repo, 'X = 11\n', name='other.py')
    m = _run(repo, 'merge', '--no-edit', 'feature')
    assert m.returncode == 0, (m.stdout, m.stderr)


@needs_sh
def test_recreating_an_agent_branch_at_a_rejected_commit_does_not_launder_it(project, repo, tmp_path):
    """The fork-point rule must not let a branch disown its own rejected work by
    being deleted and recreated there: with no other branch holding the
    commit, every containing agent branch owns it."""
    _hooked_repo(repo, tmp_path, merge_requires_review=True)
    _, a = _agent(project, 'hk11')
    _review(project, 'hk11', a, verdict='changes_requested')
    ref = 'refs/heads/' + w.branch_name('hk11')
    _git(repo, 'update-ref', '-d', ref)
    _git(repo, 'update-ref', ref, a)
    _commit(repo, 'X = 12\n', name='other.py')
    m = _run(repo, 'merge', '--no-edit', a)
    assert m.returncode != 0 and 'BLOCKED' in m.stderr, (m.stdout, m.stderr)


@needs_sh
def test_renaming_the_rejecting_branch_does_not_let_another_pass_through(project, repo, tmp_path):
    """Round 4: B was moved onto A's commit and passed; A rejected it. Renaming
    A (or its creation entry expiring from the reflog) made A look forked from
    the commit, so only B owned it and B's pass let it land."""
    _hooked_repo(repo, tmp_path, merge_requires_review=True)
    _, c = _agent(project, 'hk14')
    w.create(project, 'hk15')
    _git(repo, 'update-ref', 'refs/heads/' + w.branch_name('hk15'), c)
    _review(project, 'hk15', c)
    _review(project, 'hk14', c, verdict='changes_requested')
    _commit(repo, 'X = 13\n', name='other.py')
    renamed = 'clayrune/agent/renamed'
    _git(repo, 'branch', '-m', w.branch_name('hk14'), renamed)
    m = _run(repo, 'merge', '--no-edit', renamed)
    assert m.returncode != 0 and 'BLOCKED' in m.stderr, (m.stdout, m.stderr)


@needs_sh
def test_merging_an_ordinary_branch_is_not_gated(project, repo, tmp_path):
    """Base commits are contained in every agent branch; only work not yet on
    the base may trigger the gate."""
    _hooked_repo(repo, tmp_path, merge_requires_review=True)
    _agent(project, 'hk7')
    _git(repo, 'checkout', '-q', '-b', 'feature')
    _commit(repo, 'Y = 1\n', name='feature.py')
    _git(repo, 'checkout', '-q', '-')
    _commit(repo, 'X = 9\n', name='other.py')
    m = _run(repo, 'merge', '--no-edit', 'feature')
    assert m.returncode == 0, (m.stdout, m.stderr)


@needs_sh
def test_commit_msg_hook_leaves_ordinary_commits_alone(repo, tmp_path):
    _hooked_repo(repo, tmp_path, merge_requires_review=True)
    (repo / 'other.py').write_text('X = 5\n', encoding='utf-8')
    _git(repo, 'add', 'other.py')
    c = _run(repo, 'commit', '-m', 'plain commit')
    assert c.returncode == 0, (c.stdout, c.stderr)


@needs_sh
def test_pull_of_an_agent_branch_is_gated(project, repo, tmp_path):
    """Finding 2: `git pull --no-rebase --no-ff . <branch>` exports a
    GIT_REFLOG_ACTION that starts with `pull`, which the check ignored."""
    _hooked_repo(repo, tmp_path, merge_requires_review=True)
    _agent(project, 'hk3')
    _commit(repo, 'X = 6\n', name='other.py')
    before = _git(repo, 'rev-parse', 'HEAD')
    branch = w.branch_name('hk3')
    p = _run(repo, 'pull', '--no-rebase', '--no-ff', '--no-edit', '.', branch)
    assert p.returncode != 0 and 'BLOCKED' in p.stderr, (p.stdout, p.stderr)
    assert _git(repo, 'rev-parse', 'HEAD') == before
    _run(repo, 'merge', '--abort')
    _review(project, 'hk3', _git(repo, 'rev-parse', branch))
    p = _run(repo, 'pull', '--no-rebase', '--no-ff', '--no-edit', '.', branch)
    assert p.returncode == 0, (p.stdout, p.stderr)


@needs_sh
@pytest.mark.parametrize('gate_on', [False, True])
def test_merge_from_a_linked_worktree_resolves_the_main_checkout(
        project, repo, tmp_path, gate_on):
    """Finding 4: hooks are shared via the common git dir, but the check looked
    the project up by the LINKED worktree's path, found nothing and exited 2 --
    blocking every merge there, even with the gate off."""
    extra = {'merge_requires_review': True} if gate_on else {}
    _hooked_repo(repo, tmp_path, **extra)
    _agent(project, 'hk4')
    linked = tmp_path / 'linked'
    _git(repo, 'worktree', 'add', '-q', '-b', 'side', str(linked))
    _commit(linked, 'X = 7\n', name='other.py')
    branch = w.branch_name('hk4')
    m = _run(linked, 'merge', '--no-edit', branch)
    if not gate_on:
        assert m.returncode == 0, (m.stdout, m.stderr)
        return
    assert m.returncode != 0 and 'BLOCKED' in m.stderr, (m.stdout, m.stderr)
    _run(linked, 'merge', '--abort')
    _review(project, 'hk4', _git(repo, 'rev-parse', branch))
    m = _run(linked, 'merge', '--no-edit', branch)
    assert m.returncode == 0, (m.stdout, m.stderr)
