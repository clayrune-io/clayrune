"""A checkpoint launch that fails gives the session's in-flight slot back (ML20).

`_maybe_checkpoint` marks the session in flight and THEN builds the worker's
snapshot. When that build raised, the outer handler returned with the id still
in the set: every later boundary took the `checkpoint_coalesced` branch, the
session never checkpointed again, and nothing was logged.
"""
import threading

import pytest


@pytest.fixture
def env(tmp_path, monkeypatch):
    import server  # noqa: F401: initialize the real module's injected dependencies
    from mc import memory as mem
    transcript = tmp_path / 't.jsonl'
    transcript.write_text('x' * 4096, encoding='utf-8')
    monkeypatch.setitem(mem.state.CONFIG, 'scribe_checkpoint_enabled', True)
    monkeypatch.setitem(mem.state.CONFIG, 'scribe_checkpoint_kb', 1)
    monkeypatch.setitem(mem.state.CONFIG, 'scribe_enabled', True)
    monkeypatch.setattr(mem, 'load_project',
                        lambda pid: {'id': pid, 'project_path': str(tmp_path)})
    monkeypatch.setattr(mem, '_find_transcript_file', lambda *a: transcript)
    monkeypatch.setattr(mem, '_checkpoint_prev_offset', lambda *a: 0)
    monkeypatch.setattr(mem, '_canonical_scribe_reader', None)
    monkeypatch.setattr(mem, '_checkpoint_inflight', set())
    monkeypatch.setattr(mem, '_checkpoint_guard', threading.Lock())
    logs = []
    monkeypatch.setattr(mem, '_log', lambda message, *a, **k: logs.append(message))
    session = {'project_id': 'p', 'session_id': 'mc-session', 'provider': 'claude',
               'claude_session_id': 'native', 'process_alive': True}
    return mem, session, logs


def test_failed_snapshot_build_releases_the_session(env, monkeypatch):
    mem, session, logs = env

    def boom(*a, **k):
        raise TypeError('stored model value has the wrong shape')

    monkeypatch.setattr(mem, '_model_for_provider', boom)
    assert mem._maybe_checkpoint(session) is False
    assert 'mc-session' not in mem._checkpoint_inflight
    assert any('checkpoint launch failed' in line and 'wrong shape' in line
               for line in logs)


def test_failed_thread_start_releases_the_session(env, monkeypatch):
    mem, session, logs = env

    class NoThread:
        def __init__(self, *a, **k):
            pass

        def start(self):
            raise RuntimeError("can't start new thread")

    monkeypatch.setattr(mem.threading, 'Thread', NoThread)
    assert mem._maybe_checkpoint(session) is False
    assert 'mc-session' not in mem._checkpoint_inflight
    assert any('checkpoint launch failed' in line for line in logs)


def test_a_launched_worker_keeps_the_slot_until_it_finishes(env, monkeypatch):
    """The release is for a launch that never happened. Once the worker has
    the snapshot, the slot is the worker's to give back."""
    mem, session, _ = env
    started = []

    class FakeThread:
        def __init__(self, target=None, args=(), **k):
            started.append(args[0])

        def start(self):
            return None

    monkeypatch.setattr(mem.threading, 'Thread', FakeThread)
    mem._maybe_checkpoint(session)
    assert len(started) == 1
    assert 'mc-session' in mem._checkpoint_inflight
