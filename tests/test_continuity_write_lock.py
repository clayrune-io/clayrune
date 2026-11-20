"""write_continuity is a locked read-modify-write over one shared file (ML17).

Every agent on a project shares CONTINUITY.md. The write reads the whole
record, replaces one owner's slots and writes the whole file back, so two
things must hold: only one writer is inside that sequence at a time, and a
read that FAILED is never taken for "no record yet" (the write that followed
used to persist a file holding only the caller's bucket).
"""
import sys
import threading
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

P = {'id': 'p-cont-lock'}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import server  # noqa: F401
    from mc import memory as mem
    monkeypatch.setattr(mem, '_get_memory_path', lambda p: tmp_path / 'MEMORY.md')
    (tmp_path / 'MEMORY.md').write_text('# index\n', encoding='utf-8')
    return mem, tmp_path


def test_unreadable_record_is_not_overwritten(env, monkeypatch):
    mem, root = env
    mem.write_continuity(P, threads=['dave is mid-merge'], owner='Dave')
    fp = root / mem.CONTINUITY_FILE
    before = fp.read_bytes()

    original_read = Path.read_text

    def failed_read(self, *args, **kwargs):
        if self == fp:
            raise PermissionError('simulated locked continuity file')
        return original_read(self, *args, **kwargs)

    monkeypatch.setattr(Path, 'read_text', failed_read)
    with pytest.raises(PermissionError):
        mem.write_continuity(P, threads=['fenn starts a review'], owner='Fenn')
    monkeypatch.setattr(Path, 'read_text', original_read)

    assert fp.read_bytes() == before
    assert mem.read_continuity(P, owner='Dave')['threads'] == ['dave is mid-merge']


def test_display_read_stays_best_effort(env, monkeypatch):
    mem, root = env
    mem.write_continuity(P, threads=['x'])
    fp = root / mem.CONTINUITY_FILE
    original_read = Path.read_text

    def failed_read(self, *args, **kwargs):
        if self == fp:
            raise PermissionError('simulated')
        return original_read(self, *args, **kwargs)

    monkeypatch.setattr(Path, 'read_text', failed_read)
    assert mem.read_continuity(P)['threads'] == []


def test_write_waits_for_the_continuity_lock(env):
    mem, _ = env
    lock = mem._get_mem_write_lock(f"continuity:{P['id']}")
    done = threading.Event()

    def writer():
        mem.write_continuity(P, threads=['queued'], owner='Dave')
        done.set()

    lock.acquire()
    try:
        t = threading.Thread(target=writer, daemon=True)
        t.start()
        assert not done.wait(0.3), 'write ran while the lock was held'
    finally:
        lock.release()
    assert done.wait(5)
    assert mem.read_continuity(P, owner='Dave')['threads'] == ['queued']
