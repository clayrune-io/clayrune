"""Persisted Documents-tab scan cache (mc/doc_write_cache_store.py).

Before this, `_DOC_WRITE_CACHE` was memory-only: the first Documents open after
a server restart re-read every transcript (8.7 s on mission_control). The
headline test simulates that restart (empty in-memory cache, store not yet
loaded) and asserts only the CHANGED transcript is re-parsed.
"""
import json
import os
import sys
import types
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import mc.agent_runtime as art  # noqa: E402
from mc import doc_write_cache_store as store  # noqa: E402


@pytest.fixture()
def env(tmp_path, monkeypatch):
    project = tmp_path / 'repo'
    (project / 'docs').mkdir(parents=True)
    home = tmp_path / 'claude_projects'
    home.mkdir()
    monkeypatch.setattr(art, '_CLAUDE_HOME', home)
    monkeypatch.setattr(art, '_DOC_WRITE_CACHE', {})
    cache_file = tmp_path / 'store' / 'doc_write_cache.json'
    store.reset_for_tests(cache_file)
    d = home / art.ClaudeRuntime._encode_project_path(str(project))
    d.mkdir()

    return types.SimpleNamespace(project=project, dir=d, file=cache_file,
                                 rt=art.get_runtime('claude'))


def _transcript(env, sid, rel, ts='2026-09-01T00:00:00Z'):
    target = env.project / rel
    target.write_text('# t\n', encoding='utf-8')
    line = {'type': 'assistant', 'session_id': sid, 'timestamp': ts,
            'message': {'content': [{'type': 'tool_use', 'name': 'Write', 'id': 't1',
                                     'input': {'file_path': str(target), 'content': 'x'}}]}}
    p = env.dir / f'{sid}.jsonl'
    p.write_text(json.dumps(line) + '\n', encoding='utf-8')
    return p


def _restart(monkeypatch):
    """What a server restart does to the cache: memory gone, store not loaded."""
    monkeypatch.setattr(art, '_DOC_WRITE_CACHE', {})
    store.reset_for_tests(store.CACHE_FILE)


def _count_parses(env, monkeypatch):
    calls = []
    real = env.rt.parse_event

    def spy(raw, *a, **k):
        calls.append(raw)
        return real(raw, *a, **k)
    monkeypatch.setattr(env.rt, 'parse_event', spy)
    return calls


def test_restart_rescans_only_changed_transcripts(env, monkeypatch):
    for i in range(5):
        _transcript(env, f's{i}', f'docs/D{i}.md')
    first = env.rt.list_written_markdown(str(env.project))
    assert len(first) == 5
    store.flush(art._DOC_WRITE_CACHE)               # the debounced save, on demand
    assert env.file.exists()

    _restart(monkeypatch)
    calls = _count_parses(env, monkeypatch)
    # one transcript changes while the server is down (new size + mtime)
    changed = env.dir / 's2.jsonl'
    changed.write_text(changed.read_text(encoding='utf-8') * 2, encoding='utf-8')
    os.utime(changed, (changed.stat().st_atime, changed.stat().st_mtime + 5))

    second = env.rt.list_written_markdown(str(env.project))
    assert sorted(h['path'] for h in second) == sorted(h['path'] for h in first)
    assert len(calls) == 2, f'only the changed transcript (2 lines now) may be re-parsed, got {len(calls)}'


def test_without_persistence_a_restart_reparses_everything(env, monkeypatch):
    """Control: no flush -> the restarted process has nothing to warm from."""
    for i in range(4):
        _transcript(env, f's{i}', f'docs/D{i}.md')
    env.rt.list_written_markdown(str(env.project))
    store.reset_for_tests(env.file)                  # drops the pending timer, no write
    assert not env.file.exists()
    _restart(monkeypatch)
    calls = _count_parses(env, monkeypatch)
    env.rt.list_written_markdown(str(env.project))
    assert len(calls) == 4


def test_scan_does_not_write_on_the_request_path(env):
    _transcript(env, 's0', 'docs/D0.md')
    env.rt.list_written_markdown(str(env.project))
    assert not env.file.exists(), 'the save is debounced onto a timer thread'
    assert store._timer is not None
    store.reset_for_tests(env.file)


def test_burst_of_scans_schedules_one_save(env):
    _transcript(env, 's0', 'docs/D0.md')
    env.rt.list_written_markdown(str(env.project))
    t = store._timer
    for i in range(1, 4):
        _transcript(env, f's{i}', f'docs/D{i}.md')
        env.rt.list_written_markdown(str(env.project))
    assert store._timer is t
    store.reset_for_tests(env.file)


def test_a_fully_warm_pass_schedules_no_write(env):
    _transcript(env, 's0', 'docs/D0.md')
    env.rt.list_written_markdown(str(env.project))
    store.reset_for_tests(env.file)                  # clear the first pass's timer
    store._loaded = True                             # keep the in-memory cache as is
    env.rt.list_written_markdown(str(env.project))
    assert store._timer is None


@pytest.mark.parametrize('payload', [
    '{not json', '', '[]', '{"version": 999, "entries": {}}',
    '{"version": 1, "entries": []}', '{"version": 1}',
])
def test_corrupt_or_foreign_file_falls_back_to_a_full_scan(env, monkeypatch, capsys, payload):
    _transcript(env, 's0', 'docs/D0.md')
    env.file.parent.mkdir(parents=True, exist_ok=True)
    env.file.write_text(payload, encoding='utf-8')
    calls = _count_parses(env, monkeypatch)
    hits = env.rt.list_written_markdown(str(env.project))     # must not raise
    assert len(hits) == 1 and len(calls) == 1
    if payload:
        assert '[doc-cache]' in capsys.readouterr().out
    store.reset_for_tests(env.file)


def test_malformed_entries_are_dropped_individually(env):
    good = _transcript(env, 's0', 'docs/D0.md')
    st = good.stat()
    env.file.parent.mkdir(parents=True, exist_ok=True)
    env.file.write_text(json.dumps({'version': 1, 'entries': {
        str(good): [st.st_mtime, st.st_size, [{'path': 'x.md', 'ts': 't', 'tool': 'Write', 'session_id': 's0'}]],
        'bad1': 'nope', 'bad2': [1, 2], 'bad3': [1, 2, 'notalist'],
    }}), encoding='utf-8')
    got = store.read_entries(env.file)
    assert list(got) == [str(good)]


def test_save_is_atomic_and_leaves_no_temp_file(env):
    _transcript(env, 's0', 'docs/D0.md')
    env.rt.list_written_markdown(str(env.project))
    assert store.flush(art._DOC_WRITE_CACHE) is True
    doc = json.loads(env.file.read_text(encoding='utf-8'))
    assert doc['version'] == store.SCHEMA_VERSION and len(doc['entries']) == 1
    assert [p.name for p in env.file.parent.iterdir()] == [env.file.name]


def test_unwritable_location_is_swallowed(tmp_path, capsys):
    blocker = tmp_path / 'afile'
    blocker.write_text('x', encoding='utf-8')
    assert store.write_entries({'k': (1.0, 1, [])}, blocker / 'sub' / 'c.json') is False
    assert '[doc-cache] could not save' in capsys.readouterr().out


def test_mtime_float_round_trips_exactly(env):
    entries = {'k': (1759700000.123456789, 4242, [])}
    store.write_entries(entries, env.file)
    assert store.read_entries(env.file) == entries


def test_memory_entries_win_over_disk(env):
    store.write_entries({'k': (1.0, 1, [{'path': 'disk.md'}])}, env.file)
    cache = {'k': (2.0, 2, [{'path': 'mem.md'}])}
    assert store.load_into(cache) == 0
    assert cache['k'][2][0]['path'] == 'mem.md'
