"""_checkpoint_watermark returns a dict on every path (callers .get() it)."""
from mc import memory


def test_no_marker_and_no_memory_file_returns_empty_dict(monkeypatch, tmp_path):
    monkeypatch.setattr(memory, '_session_log_read', lambda p: ('', []))
    monkeypatch.setattr(memory, '_get_memory_path', lambda p: tmp_path / 'MEMORY.md')
    wm = memory._checkpoint_watermark({'id': 'x'}, 'sid-1')
    assert wm == {}
    assert memory._checkpoint_prev_offset({'id': 'x'}, 'sid-1') == 0
