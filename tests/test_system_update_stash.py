"""Regression tests for the self-service "set aside local changes and update"
path on /api/system/update (2026-09-14, Amit's "Blocked" report).

Before this, a dirty working tree left Settings -> Update Clayrune
permanently disabled ("Blocked: Local changes... Stash or commit first.")
with zero path forward for someone who doesn't use git. The endpoint now
accepts {"stash": true}: it runs `git stash push` (TRACKED changes only,
never -u) before the normal ff-only/hard-reset pull, and returns the stash
message so the UI can tell the user how to get their work back.

Reuses the upstream+checkout "lab" fixture shape from
test_system_update_resync.py (kept local/self-contained per that file's own
convention — no shared conftest fixture for this).
"""
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

UI_HEADERS = {'Origin': 'http://localhost:5199'}  # a real browser fetch always carries one


def _run(args, cwd):
    r = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True)
    assert r.returncode == 0, f"{args} failed in {cwd}: {r.stdout}{r.stderr}"
    return r.stdout.strip()


@pytest.fixture()
def lab(tmp_path):
    """A bare upstream + a clone of it (the checkout under test)."""
    upstream = tmp_path / 'upstream.git'
    work = tmp_path / 'work'
    checkout = tmp_path / 'Clayrune'

    _run(['git', 'init', '--bare', '-b', 'master', str(upstream)], tmp_path)
    _run(['git', 'init', '-b', 'master', str(work)], tmp_path)
    _run(['git', 'config', 'user.email', 'test@example.com'], work)
    _run(['git', 'config', 'user.name', 'Test'], work)
    (work / 'server.py').write_text('v1\n')
    _run(['git', 'add', 'server.py'], work)
    _run(['git', 'commit', '-m', 'v1'], work)
    _run(['git', 'remote', 'add', 'origin', str(upstream)], work)
    _run(['git', 'push', 'origin', 'master'], work)

    _run(['git', 'clone', str(upstream), str(checkout)], tmp_path)
    return {'upstream': upstream, 'work': work, 'checkout': checkout}


def push_new_commit(lab):
    """Give upstream a new commit so the checkout is genuinely behind."""
    work = lab['work']
    (work / 'feature.md').write_text('shipped upstream\n')
    _run(['git', 'add', 'feature.md'], work)
    _run(['git', 'commit', '-m', 'feature'], work)
    _run(['git', 'push', 'origin', 'master'], work)


@pytest.fixture()
def client(lab, monkeypatch):
    import server
    from mc.blueprints import system_routes as sr
    monkeypatch.setattr(sr, '_APP_DIR', lab['checkout'])
    server.app.config['TESTING'] = True
    return server.app.test_client()


class TestCleanTree:
    def test_clean_tree_unaffected_by_stash_flag(self, client, lab):
        """stash:true on an already-clean tree must be a harmless no-op —
        the endpoint should behave exactly like a plain update."""
        push_new_commit(lab)
        resp = client.post('/api/system/update', json={'stash': True}, headers=UI_HEADERS)
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_json()
        assert body['ok'] is True
        assert body['stashed'] == ''


class TestDirtyTreeStash:
    def test_stash_created_and_update_proceeds(self, client, lab):
        push_new_commit(lab)
        co = lab['checkout']
        (co / 'server.py').write_text('local edit\n')

        resp = client.post('/api/system/update', json={'stash': True}, headers=UI_HEADERS)
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_json()
        assert body['ok'] is True
        assert body['stashed'].startswith('clayrune-auto-stash ')

        # The pull actually landed — server.py now matches upstream, not the
        # locally-edited content, and the tree is clean again.
        assert (co / 'feature.md').exists()
        status = _run(['git', 'status', '--porcelain'], co)
        assert status == ''

        # The stash entry is real and recoverable, by the same message we
        # returned to the UI.
        stash_list = _run(['git', 'stash', 'list'], co)
        assert body['stashed'] in stash_list

    def test_stash_is_tracked_only_untracked_data_untouched(self, client, lab):
        """`git stash push` (no -u) must never sweep up untracked user data —
        that's DATA_DIR, config.json, etc. in a real install."""
        push_new_commit(lab)
        co = lab['checkout']
        (co / 'server.py').write_text('local edit\n')
        (co / 'data').mkdir(parents=True, exist_ok=True)
        (co / 'data' / 'untracked_user_file.json').write_text('MY DATA')

        resp = client.post('/api/system/update', json={'stash': True}, headers=UI_HEADERS)
        assert resp.status_code == 200, resp.get_data(as_text=True)

        # Untracked file survived on disk, completely untouched by the stash.
        assert (co / 'data' / 'untracked_user_file.json').read_text() == 'MY DATA'

        # And the stash itself only carries the tracked edit, not the untracked file.
        stash_show = _run(['git', 'stash', 'show', '-u', 'stash@{0}'], co)
        assert 'untracked_user_file.json' not in stash_show

    def test_recoverable_via_git_stash_apply(self, client, lab):
        """The whole point: the user's edit is not gone, just shelved."""
        push_new_commit(lab)
        co = lab['checkout']
        (co / 'server.py').write_text('precious local edit\n')

        resp = client.post('/api/system/update', json={'stash': True}, headers=UI_HEADERS)
        assert resp.status_code == 200

        _run(['git', 'stash', 'apply', 'stash@{0}'], co)
        assert (co / 'server.py').read_text() == 'precious local edit\n'

    def test_failure_after_stash_still_reports_the_stash(self, client, lab):
        """If the pull AND the fetch fall over after we already stashed, the
        error must still name the stash: the tree is clean again, so the next
        status check shows nothing and the user would never learn their
        edits were shelved."""
        co = lab['checkout']
        (co / 'server.py').write_text('local edit\n')
        _run(['git', 'remote', 'set-url', 'origin', str(lab['upstream']) + '-gone'], co)

        resp = client.post('/api/system/update', json={'stash': True}, headers=UI_HEADERS)
        assert resp.status_code == 500, resp.get_data(as_text=True)
        body = resp.get_json()
        assert body['stashed'].startswith('clayrune-auto-stash ')
        assert body['stashed'] in _run(['git', 'stash', 'list'], co)

    def test_without_stash_flag_still_blocked_unchanged(self, client, lab):
        """The plain (no stash) 409 path must be completely unchanged."""
        push_new_commit(lab)
        co = lab['checkout']
        (co / 'server.py').write_text('local edit\n')

        resp = client.post('/api/system/update', headers=UI_HEADERS)
        assert resp.status_code == 409
        body = resp.get_json()
        assert 'local changes' in body['error'].lower()
        assert (co / 'server.py').read_text().strip() == 'local edit'
        assert _run(['git', 'stash', 'list'], co) == ''


class TestInstallDirWarning:
    """GET /api/system/update/status surfaces which projects (if any) have a
    project_path inside the install dir — the Settings-UI warning banner
    that tells a human about exactly the state Amit's report was caused by,
    instead of leaving it silent (2026-09-14)."""

    def test_no_projects_in_install_dir_empty(self, client, lab, monkeypatch):
        from mc.blueprints import system_routes as sr
        monkeypatch.setattr(sr, 'load_projects', lambda: [
            {'id': 'p1', 'project_path': str(lab['checkout'] / '..' / 'elsewhere')},
        ])
        r = client.get('/api/system/update/status')
        assert r.status_code == 200
        assert r.get_json()['projects_in_install_dir'] == []

    def test_project_pointing_at_install_dir_is_reported(self, client, lab, monkeypatch):
        from mc.blueprints import system_routes as sr
        co = lab['checkout']
        monkeypatch.setattr(sr, 'load_projects', lambda: [
            {'id': 'amit_proj', 'name': "Amit's Clayrune", 'project_path': str(co)},
            {'id': 'other', 'project_path': str(co.parent / 'unrelated')},
        ])
        r = client.get('/api/system/update/status')
        assert r.status_code == 200
        hits = r.get_json()['projects_in_install_dir']
        assert len(hits) == 1
        assert hits[0]['id'] == 'amit_proj'

    def test_nested_path_under_install_dir_is_reported(self, client, lab, monkeypatch):
        from mc.blueprints import system_routes as sr
        co = lab['checkout']
        nested = co / 'some' / 'workspace'
        nested.mkdir(parents=True)
        monkeypatch.setattr(sr, 'load_projects', lambda: [
            {'id': 'nested_proj', 'project_path': str(nested)},
        ])
        r = client.get('/api/system/update/status')
        assert [h['id'] for h in r.get_json()['projects_in_install_dir']] == ['nested_proj']

    def test_opt_in_config_suppresses_the_warning(self, client, lab, monkeypatch):
        """Once the human has explicitly acknowledged it via
        allow_project_in_install_dir, the warning has nothing left to say."""
        from mc import state as mc_state
        from mc.blueprints import system_routes as sr
        co = lab['checkout']
        monkeypatch.setattr(sr, 'load_projects', lambda: [
            {'id': 'dev_checkout', 'project_path': str(co)},
        ])
        monkeypatch.setitem(mc_state.CONFIG, 'allow_project_in_install_dir', True)
        r = client.get('/api/system/update/status')
        assert r.get_json()['projects_in_install_dir'] == []


class TestAgentCallerRefused:
    def test_agent_caller_refused(self, client, lab):
        """No Origin header = an agent's bare HTTP call, same structural
        signal every other human-only action uses. Must be refused before
        anything is stashed."""
        push_new_commit(lab)
        co = lab['checkout']
        (co / 'server.py').write_text('local edit\n')

        resp = client.post('/api/system/update', json={'stash': True})  # no Origin header
        assert resp.status_code == 403
        assert 'human' in resp.get_json()['error'].lower()

        # Nothing was touched — no stash, no pull.
        assert (co / 'server.py').read_text().strip() == 'local edit'
        assert _run(['git', 'stash', 'list'], co) == ''

    def test_agent_caller_not_refused_when_tree_is_clean(self, client, lab):
        """The agent-caller gate only guards the STASH action. A plain update
        on a clean tree is unaffected — this endpoint's existing behavior for
        an agent-triggered update (if any) must not regress."""
        push_new_commit(lab)
        resp = client.post('/api/system/update', json={'stash': True})  # no Origin
        assert resp.status_code == 200
        assert resp.get_json()['ok'] is True
