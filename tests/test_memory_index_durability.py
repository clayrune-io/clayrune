"""MEMORY.md durability (MC-1085).

2026-10-06 a Windows bugcheck left mission_control's MEMORY.md zero-filled at
its full length (20,458 NUL bytes). `_atomic_write_text` renamed its temp file
without fsync, and `_commit_managed_entry` rewrote the UNCHANGED index on every
scribe run, so the rename was always in flight. Pinned here:

1. the temp file is fsynced before the replace,
2. an unchanged index is not rewritten,
3. a NUL-bearing index is never written back (and never silently "healed").

The two route-level pieces (409s, lock + atomic write) live in
tests/test_project_routes.py beside the other memory-route tests.
"""
import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import server  # noqa: F401
    from mc import memory as mem

    monkeypatch.setattr(mem, '_get_memory_path',
                        lambda p: tmp_path / 'MEMORY.md')
    monkeypatch.setattr(mem, '_should_condense', lambda *a, **k: False)
    monkeypatch.setattr(mem, '_scribe_stat', lambda *a, **k: None)
    return mem, tmp_path


def _proj():
    return {'id': 'p1', 'name': 'P1'}


def _spy_writes(mem, monkeypatch):
    written = []
    real = mem._atomic_write_text

    def _spy(path, text, *a, **kw):
        written.append(Path(path).name)
        return real(path, text, *a, **kw)

    monkeypatch.setattr(mem, '_atomic_write_text', _spy)
    return written


# ── (1) fsync before replace ─────────────────────────────────────────────────

def test_atomic_write_text_fsyncs_before_replace(tmp_path, monkeypatch):
    from mc import atomic_json, core

    calls = []
    real_fsync = os.fsync
    real_replace = atomic_json.replace_with_retry

    def _fsync(fd):
        calls.append('fsync')
        return real_fsync(fd)

    def _replace(src, dst, *a, **kw):
        calls.append('replace')
        # Data is already on the temp file by the time it is renamed in.
        assert Path(src).read_text(encoding='utf-8') == 'payload'
        return real_replace(src, dst, *a, **kw)

    monkeypatch.setattr(os, 'fsync', _fsync)
    monkeypatch.setattr(atomic_json, 'replace_with_retry', _replace)

    target = tmp_path / 'MEMORY.md'
    core._atomic_write_text(target, 'payload')

    assert calls == ['fsync', 'replace']
    assert target.read_text(encoding='utf-8') == 'payload'
    assert not list(tmp_path.glob('.MEMORY.md.tmp*'))


# ── (2) no-op rewrite skipped ────────────────────────────────────────────────

def test_commit_does_not_rewrite_unchanged_index(env, monkeypatch):
    mem, tmp_path = env
    idx = tmp_path / 'MEMORY.md'
    mem._write_curated_only(idx, '- [a](a.md) — hook\n- [b](b.md) — hook')
    before = idx.read_bytes()
    mtime = idx.stat().st_mtime_ns

    written = _spy_writes(mem, monkeypatch)
    mem._commit_managed_entry(_proj(), mem_entry='- [2026-10-11] did a thing')

    assert 'MEMORY.md' not in written              # index untouched ...
    assert 'SESSION_LOG.md' in written             # ... the log still lands
    assert idx.read_bytes() == before
    assert idx.stat().st_mtime_ns == mtime


def test_changed_index_is_still_written(env, monkeypatch):
    mem, tmp_path = env
    idx = tmp_path / 'MEMORY.md'
    mem._write_curated_only(idx, '- [a](a.md) — hook')
    written = _spy_writes(mem, monkeypatch)
    mem._write_curated_only(idx, '- [a](a.md) — hook\n- [b](b.md) — new')
    assert written == ['MEMORY.md']
    assert 'new' in idx.read_text(encoding='utf-8')


# ── (3) NUL never round-trips ────────────────────────────────────────────────

def test_nul_index_never_round_trips(env, monkeypatch):
    mem, tmp_path = env
    idx = tmp_path / 'MEMORY.md'
    zeroed = b'\x00' * 20458 + b'\r\n'
    idx.write_bytes(zeroed)

    logs = []
    monkeypatch.setattr(mem, '_log', lambda msg, *a, **k: logs.append(msg))
    written = _spy_writes(mem, monkeypatch)

    mem._commit_managed_entry(_proj(), mem_entry='- [2026-10-11] did a thing')

    assert idx.read_bytes() == zeroed              # evidence kept, not healed
    assert 'MEMORY.md' not in written
    assert 'SESSION_LOG.md' in written             # log still written
    nul_logs = [m for m in logs if m.startswith('[memory]') and 'NUL' in m]
    assert nul_logs and '20458' in nul_logs[0]


def test_write_curated_only_refuses_any_nul_text(env, monkeypatch):
    mem, tmp_path = env
    idx = tmp_path / 'MEMORY.md'
    idx.write_text('clean\n', encoding='utf-8')
    monkeypatch.setattr(mem, '_log', lambda *a, **k: None)
    mem._write_curated_only(idx, 'clean\n\x00\x00')
    assert idx.read_text(encoding='utf-8') == 'clean\n'


def test_demote_line_refuses_nul_index(env):
    mem, tmp_path = env
    (tmp_path / 'MEMORY.md').write_bytes(b'\x00' * 64)
    with pytest.raises(mem.DemotionRefused):
        # Candidate lookup fails first on a NUL file; either refusal is the
        # contract: demote_line never writes a NUL-bearing index.
        mem.demote_line(_proj(), 0)
    assert (tmp_path / 'MEMORY.md').read_bytes() == b'\x00' * 64
