"""The update route must not hard-reset away commits that exist only locally
(MC-1179 pass 07, P43), and the reset fallback is human-only.

Before: a bare `POST /api/system/update` ran `pull --ff-only` and, on any
failure, `fetch` + `reset --hard origin/<branch>`. On a diverged checkout that
dropped the local commit; on a local-only branch it repointed the branch at
origin/master. Both answered 200 `resynced: true`.

Scratch repos in tmp_path only; the route is pointed at them by patching
`_APP_DIR`. Same lab shape as test_system_update_resync.py.
"""
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

UI = {'Origin': 'http://localhost:5199'}  # a real browser fetch always carries one


def _run(args, cwd):
    r = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True)
    assert r.returncode == 0, f"{args} failed in {cwd}: {r.stdout}{r.stderr}"
    return r.stdout.strip()


def _sha(repo, rev='HEAD'):
    return _run(['git', 'rev-parse', rev], repo)


def _commit(repo, name, text='x\n', msg=None):
    (repo / name).write_text(text)
    _run(['git', 'add', name], repo)
    _run(['git', '-c', 'user.email=t@example.com', '-c', 'user.name=T',
          'commit', '-m', msg or name], repo)


@pytest.fixture()
def lab(tmp_path):
    upstream = tmp_path / 'upstream.git'
    work = tmp_path / 'work'
    checkout = tmp_path / 'Clayrune'
    _run(['git', 'init', '--bare', '-b', 'master', str(upstream)], tmp_path)
    _run(['git', 'init', '-b', 'master', str(work)], tmp_path)
    _commit(work, 'server.py', 'v1\n', 'v1')
    _run(['git', 'remote', 'add', 'origin', str(upstream)], work)
    _run(['git', 'push', 'origin', 'master'], work)
    _run(['git', 'clone', str(upstream), str(checkout)], tmp_path)
    return {'upstream': upstream, 'work': work, 'checkout': checkout}


def push_upstream_commit(lab, name='feature.md'):
    _commit(lab['work'], name)
    _run(['git', 'push', 'origin', 'master'], lab['work'])


def force_push_rewrite(lab):
    work = lab['work']
    _run(['git', 'checkout', '--orphan', 'rewritten'], work)
    subprocess.run(['git', 'rm', '-rf', '.'], cwd=str(work), capture_output=True, text=True)
    _commit(work, 'server.py', 'v2-rewritten\n', 'v2 rewritten history')
    _run(['git', 'branch', '-M', 'master'], work)
    _run(['git', 'push', '--force', 'origin', 'master'], work)


@pytest.fixture()
def client(lab, monkeypatch, passcode_calls):
    import server
    from mc.blueprints import system_routes as sr
    monkeypatch.setattr(sr, '_APP_DIR', lab['checkout'])
    server.app.config['TESTING'] = True
    return server.app.test_client()


@pytest.fixture()
def passcode_calls(monkeypatch):
    """Record every call to the passcode gate; the gate itself passes."""
    from mc.blueprints import system_routes as sr
    calls = []

    def _gate(data):
        calls.append(dict(data or {}))
        return None
    monkeypatch.setattr(sr, '_require_human_passcode', _gate)
    return calls


class TestLocalCommitsAreNeverReset:
    def test_diverged_master_with_local_commit_is_refused(self, client, lab):
        """Report P43 row 1: master, one local commit, origin one ahead."""
        co = lab['checkout']
        _commit(co, 'mine.txt', 'unpushed work\n')
        tip = _sha(co)
        push_upstream_commit(lab)

        resp = client.post('/api/system/update', json={'passcode': 'x'}, headers=UI)

        assert resp.status_code == 409, resp.get_data(as_text=True)
        body = resp.get_json()
        assert body['local_commits'] == 1
        assert body['reason'] == 'local_commits'
        assert '1 local commit' in body['error'] and 'Nothing was reset' in body['error']
        assert _sha(co) == tip
        assert (co / 'mine.txt').read_text() == 'unpushed work\n'

    def test_local_only_branch_is_refused(self, client, lab):
        """Report P43 row 2: `my-work` has no origin/my-work."""
        co = lab['checkout']
        _run(['git', 'checkout', '-b', 'my-work'], co)
        _commit(co, 'mine.txt', 'unpushed work\n')
        tip = _sha(co)
        push_upstream_commit(lab)

        resp = client.post('/api/system/update', json={'passcode': 'x'}, headers=UI)

        assert resp.status_code == 409, resp.get_data(as_text=True)
        body = resp.get_json()
        assert body['reason'] == 'no_remote_branch'
        assert body['local_commits'] == 1
        assert _run(['git', 'rev-parse', '--abbrev-ref', 'HEAD'], co) == 'my-work'
        assert _sha(co) == tip
        assert (co / 'mine.txt').exists()

    def test_refusal_is_the_same_for_an_agent_caller(self, client, lab):
        """The safety refusal comes before the human check: an agent gets the
        409 too, and nothing is reset."""
        co = lab['checkout']
        _commit(co, 'mine.txt')
        tip = _sha(co)
        push_upstream_commit(lab)
        resp = client.post('/api/system/update')  # no Origin
        assert resp.status_code == 409
        assert _sha(co) == tip

    def test_force_push_with_local_commit_is_refused(self, client, lab):
        """A local commit on top of pre-rewrite history is still local work."""
        co = lab['checkout']
        _commit(co, 'mine.txt')
        tip = _sha(co)
        force_push_rewrite(lab)
        resp = client.post('/api/system/update', json={'passcode': 'x'}, headers=UI)
        assert resp.status_code == 409, resp.get_data(as_text=True)
        assert resp.get_json()['local_commits'] == 1
        assert _sha(co) == tip


class TestResyncIsHumanOnly:
    def test_agent_caller_cannot_trigger_the_reset(self, client, lab, passcode_calls):
        co = lab['checkout']
        tip = _sha(co)
        force_push_rewrite(lab)
        resp = client.post('/api/system/update')  # no Origin = bare HTTP call
        assert resp.status_code == 403, resp.get_data(as_text=True)
        assert 'human-only' in resp.get_json()['error']
        assert _sha(co) == tip
        assert passcode_calls == []

    def test_agent_with_forged_origin_still_needs_the_passcode(self, client, lab, monkeypatch):
        """Origin alone is forgeable; the retyped passcode is the gate."""
        from mc.blueprints import system_routes as sr
        from flask import jsonify
        monkeypatch.setattr(sr, '_require_human_passcode',
                            lambda _data: (jsonify({'error': 'bad_passcode'}), 403))
        co = lab['checkout']
        tip = _sha(co)
        force_push_rewrite(lab)
        resp = client.post('/api/system/update', json={'passcode': 'guess'}, headers=UI)
        assert resp.status_code == 403
        assert resp.get_json()['error'] == 'bad_passcode'
        assert _sha(co) == tip

    def test_human_without_passcode_is_told_to_confirm(self, client, lab, passcode_calls):
        co = lab['checkout']
        tip = _sha(co)
        force_push_rewrite(lab)
        resp = client.post('/api/system/update', headers=UI)  # Origin, no passcode
        assert resp.status_code == 403
        body = resp.get_json()
        assert body['error'] == 'resync_needs_confirmation'
        assert body['needs_passcode'] is True and body['message']
        assert _sha(co) == tip
        assert passcode_calls == []  # no wasted guess on an empty body

    def test_human_with_passcode_recovers_from_force_push(self, client, lab):
        force_push_rewrite(lab)
        resp = client.post('/api/system/update', json={'passcode': 'x'}, headers=UI)
        assert resp.status_code == 200, resp.get_data(as_text=True)
        assert resp.get_json()['resynced'] is True
        assert (lab['checkout'] / 'server.py').read_text().strip() == 'v2-rewritten'

    def test_stash_path_asks_for_the_passcode_once(self, client, lab, passcode_calls):
        """stash:true already verified the passcode; the reset must not spend
        a second guess on the same request."""
        force_push_rewrite(lab)
        (lab['checkout'] / 'server.py').write_text('local edit\n')
        resp = client.post('/api/system/update', json={'stash': True, 'passcode': 'x'}, headers=UI)
        assert resp.status_code == 200, resp.get_data(as_text=True)
        assert resp.get_json()['resynced'] is True
        assert len(passcode_calls) == 1


class TestPlainPullUnchanged:
    def test_agent_can_still_fast_forward(self, client, lab):
        push_upstream_commit(lab)
        resp = client.post('/api/system/update')  # no Origin, no passcode
        assert resp.status_code == 200
        assert resp.get_json()['resynced'] is False
        assert (lab['checkout'] / 'feature.md').exists()

    def test_local_commits_with_nothing_new_upstream_are_left_alone(self, client, lab):
        """ff-only reports up to date (rc 0); no fallback, no refusal."""
        co = lab['checkout']
        _commit(co, 'mine.txt')
        tip = _sha(co)
        resp = client.post('/api/system/update')
        assert resp.status_code == 200
        assert resp.get_json()['resynced'] is False
        assert _sha(co) == tip
