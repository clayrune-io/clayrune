from mc import project_sync


def test_dismissing_a_short_sha_stores_the_full_commit(monkeypatch, tmp_path):
    full = '1234567' + 'b' * 33
    project = {'project_path': str(tmp_path)}
    saves = []
    monkeypatch.setattr(project_sync, '_load_project', lambda _: project)
    monkeypatch.setattr(project_sync, '_save_project', lambda pid, _: saves.append(pid))

    def resolve(cwd, args):
        assert cwd == str(tmp_path)
        assert args == ['rev-parse', '--verify', '1234567^{commit}']
        return True, full

    monkeypatch.setattr(project_sync, 'git_run', resolve)

    assert project_sync.dismiss_commit('project', full[:7]) == (True, 'dismissed')
    assert project_sync.dismiss_commit('project', full) == (True, 'dismissed')
    assert project['code_sync_dismissed_commits'] == [full]
    assert saves == ['project', 'project']
