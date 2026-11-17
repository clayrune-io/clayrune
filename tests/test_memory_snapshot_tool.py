"""tools/memory-snapshot.py: the restore guard must hold on a case-insensitive
filesystem and for path spellings, a snapshot of a directory with no MEMORY.md
is refused, and restore checks the snapshot's own hash (MC-1179 pass 12 TL27)."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location('memory_snapshot_tool', ROOT / 'tools' / 'memory-snapshot.py')
tool = importlib.util.module_from_spec(_spec)
_before = sys.path[:]
_spec.loader.exec_module(tool)
sys.path[:] = _before


@pytest.fixture
def snap(tmp_path, monkeypatch):
    live = tmp_path / 'live'
    live.mkdir()
    (live / 'MEMORY.md').write_text('CURRENT INDEX')
    (live / 'topic.md').write_text('now')
    monkeypatch.setattr(tool, 'memory_dir', lambda: live)
    monkeypatch.setattr(tool, 'SNAP_ROOT', tmp_path / 'snaps')
    d = tool.create('t')
    (live / 'topic.md').write_text('changed since')
    return live, d


@pytest.mark.parametrize('name', ['memory.md', 'Memory_Archive.MD', './MEMORY.md',
                                  'sub/../MEMORY.md', '..'])
def test_restore_refuses_protected_names_in_any_spelling(snap, name):
    live, d = snap
    with pytest.raises(SystemExit):
        tool.restore(d, [name])
    assert (live / 'MEMORY.md').read_text() == 'CURRENT INDEX'


def test_restore_topic_file_still_works(snap):
    live, d = snap
    tool.restore(d, ['topic.md'])
    assert (live / 'topic.md').read_text() == 'now'


def test_restore_refuses_a_snapshot_file_that_no_longer_matches_its_hash(snap):
    live, d = snap
    (Path(d) / 'memory' / 'topic.md').write_text('tampered')
    with pytest.raises(SystemExit):
        tool.restore(d, ['topic.md'])
    assert (live / 'topic.md').read_text() == 'changed since'


def test_create_refuses_a_directory_with_no_memory_index(tmp_path, monkeypatch):
    empty = tmp_path / 'empty'
    empty.mkdir()
    monkeypatch.setattr(tool, 'memory_dir', lambda: empty)
    monkeypatch.setattr(tool, 'SNAP_ROOT', tmp_path / 'snaps')
    with pytest.raises(SystemExit):
        tool.create('x')
    assert not (tmp_path / 'snaps').exists()
