"""mc.core._atomic_write_text under a second writer in the same process (MC-1179 C1).

The temp name used to be `.{name}.tmp{pid}` -- unique per process, not per
call -- so two threads writing one target shared one temp file: both truncated
it, the first `replace` moved a file the other was still writing, and the
second `replace` raised FileNotFoundError. Pinned here:

1. two calls never share a temp path,
2. the temp path still matches the shape `sweep_orphan_tmpfiles` cleans,
3. a failed write leaves no temp file behind (names are per call now, so a
   leak would accumulate rather than be overwritten),
4. N threads x M writes of different lengths to one path: no exception, and
   every read of the target is exactly one of the payloads.
"""
import re
import sys
import threading
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc import atomic_json, core  # noqa: E402


def test_two_writes_never_share_a_temp_path(tmp_path, monkeypatch):
    seen = []
    real = atomic_json.replace_with_retry

    def _spy(src, dst, *a, **kw):
        seen.append(Path(src).name)
        return real(src, dst, *a, **kw)

    monkeypatch.setattr(atomic_json, 'replace_with_retry', _spy)
    target = tmp_path / 'state.txt'
    core._atomic_write_text(target, 'one')
    core._atomic_write_text(target, 'two')

    assert len(seen) == 2 and seen[0] != seen[1]
    assert target.read_text(encoding='utf-8') == 'two'


def test_temp_name_still_matches_the_orphan_sweep(tmp_path, monkeypatch):
    seen = []
    real = atomic_json.replace_with_retry

    def _spy(src, dst, *a, **kw):
        seen.append(Path(src).name)
        return real(src, dst, *a, **kw)

    monkeypatch.setattr(atomic_json, 'replace_with_retry', _spy)
    core._atomic_write_text(tmp_path / 'state.txt', 'x')

    # Same pattern sweep_orphan_tmpfiles uses.
    assert re.match(r'^\..+\.tmp\d+$', seen[0])
    # Must not end in .json: a stranded temp under data/projects/ would
    # otherwise be loaded as a project (CLAUDE.md, DATA_DIR pollution).
    assert not seen[0].endswith('.json')


def test_failed_replace_leaves_no_temp_file(tmp_path, monkeypatch):
    def _boom(*_a, **_kw):
        raise OSError('replace failed')

    monkeypatch.setattr(atomic_json, 'replace_with_retry', _boom)
    with pytest.raises(OSError):
        core._atomic_write_text(tmp_path / 'state.txt', 'x')
    assert list(tmp_path.iterdir()) == []


def test_concurrent_writers_never_tear_or_raise(tmp_path):
    target = tmp_path / 'shared.txt'
    payloads = ['A' * 300_000, 'B' * 120_000]
    core._atomic_write_text(target, payloads[1])
    errors, torn = [], []
    stop = threading.Event()

    def _writer(text):
        try:
            for _ in range(60):
                core._atomic_write_text(target, text)
        except Exception as e:  # noqa: BLE001
            errors.append(repr(e))

    def _reader():
        while not stop.is_set():
            try:
                got = target.read_text(encoding='utf-8')
            except OSError:
                continue  # transient sharing violation on Windows, not a tear
            if got not in payloads:
                torn.append(len(got))

    reader = threading.Thread(target=_reader)
    writers = [threading.Thread(target=_writer, args=(p,)) for p in payloads]
    reader.start()
    for t in writers:
        t.start()
    for t in writers:
        t.join()
    stop.set()
    reader.join()

    assert errors == []
    assert torn == []
    assert target.read_text(encoding='utf-8') in payloads
    assert list(tmp_path.glob('.shared.txt*')) == []
