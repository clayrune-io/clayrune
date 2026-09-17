"""Unreadable managed history is not an empty log that writers may overwrite."""
from pathlib import Path

import pytest


@pytest.fixture
def env(tmp_data_dir, tmp_path, monkeypatch):
    import server  # noqa: F401: initialize injected memory dependencies
    from mc import memory as mem
    project = {'id':'read-failure-test','name':'Read failure'}
    root = tmp_path / 'memory'
    root.mkdir()
    path = root / 'SESSION_LOG.md'
    path.write_bytes(b'original managed history')
    (root / 'MEMORY.md').write_bytes(b'curated pointers\n')
    monkeypatch.setattr(mem,'_get_memory_path',lambda p:root / 'MEMORY.md')
    monkeypatch.setattr(mem,'_get_session_log_path',lambda p:path)
    monkeypatch.setattr(mem,'_get_archive_path',lambda p:root / 'MEMORY_ARCHIVE.md')
    monkeypatch.setattr(mem,'_should_condense',lambda *a,**k:False)
    logs, writes = [], []
    monkeypatch.setattr(mem,'_log',lambda message,*a,**k:logs.append(message))
    monkeypatch.setattr(mem,'_atomic_write_text',lambda *a,**k:writes.append(a))
    original_read = Path.read_text
    def failed_read(self,*args,**kwargs):
        if self == path:
            raise PermissionError('simulated locked session log')
        return original_read(self,*args,**kwargs)
    monkeypatch.setattr(Path,'read_text',failed_read)
    return mem,project,path,logs,writes


def test_display_read_reports_error_but_remains_best_effort(env):
    mem,project,_,logs,_ = env
    assert mem._session_log_read(project) == ([],[])
    assert any('session log read failed' in line for line in logs)


def test_strict_read_does_not_treat_failure_as_absence(env):
    mem,project,_,_,_ = env
    with pytest.raises(PermissionError):
        mem._session_log_read(project,strict=True)


@pytest.mark.parametrize('writer', ['commit','migrate','condense'])
def test_writers_leave_all_files_unchanged_on_read_failure(env,writer):
    mem,project,path,_,writes = env
    with pytest.raises(PermissionError):
        if writer == 'commit':
            mem._commit_managed_entry(project,mem_entry='- [today] New summary')
        elif writer == 'migrate':
            mem.migrate_session_log_split(project)
        else:
            mem._condense_apply(project,{'entry_decisions':[]})
    assert writes == []
    assert path.read_bytes() == b'original managed history'
    assert (path.parent / 'MEMORY.md').read_bytes() == b'curated pointers\n'
