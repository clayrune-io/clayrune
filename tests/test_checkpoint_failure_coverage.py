"""Checkpoint acknowledgements require complete processing, not partial success."""
import threading

import pytest


@pytest.fixture
def env(tmp_path, monkeypatch):
    import server  # noqa: F401: initialize the real module's injected dependencies
    from mc import memory as mem
    project = {'id': 'checkpoint-test', 'name': 'Checkpoint test'}
    monkeypatch.setattr(mem, '_get_memory_path', lambda p: tmp_path / 'MEMORY.md')
    monkeypatch.setattr(mem, 'load_project', lambda pid: project)
    monkeypatch.setattr(mem, '_should_condense', lambda *a, **k: False)
    monkeypatch.setattr(mem, '_dispatch_condense', lambda *a: None)
    monkeypatch.setattr(mem, '_get_checkpoint_sema', lambda pid: threading.Semaphore(1))
    monkeypatch.setattr(mem, '_checkpoint_guard', threading.Lock())
    monkeypatch.setattr(mem, '_checkpoint_inflight', set())
    monkeypatch.setitem(mem.state.CONFIG, 'continuity_enabled', False)
    stats = []
    monkeypatch.setattr(mem, '_scribe_stat', lambda pid, reason: stats.append(reason))
    # Real atomic writer and real watermark reader; all state lives in tmp_path.
    mem._commit_managed_entry(project, wm_upsert={
        'session_id': 'session', 'transcript_path': 'fake-transcript',
        'byte_offset': 12, 'running_summary': 'Previous knowledge'})
    snap = dict(pid=project['id'], sid='session', csid='native', task='task', tf='fake-transcript')
    monkeypatch.setattr(mem, '_scribe_render_delta', lambda path, offset, provider='claude': ('ACTION: new work', 123))
    return mem, project, snap, stats


def watermark(mem, project):
    return mem._wm_find(mem._session_log_read(project)[1], 'session')


@pytest.mark.parametrize('reason', ['model_error', 'model_refused', 'incomplete', 'unknown_failure'])
def test_operational_failure_keeps_source_pending(env, monkeypatch, reason):
    mem, project, snap, stats = env
    monkeypatch.setattr(mem, '_scribe_summarize_text', lambda *a: (None, reason))
    mem._checkpoint_worker(snap)
    assert watermark(mem, project)['byte_offset'] == 12
    assert watermark(mem, project)['running_summary'] == 'Previous knowledge'
    assert f'checkpoint_pending:{reason}' in stats


@pytest.mark.parametrize('reason', ['parse_empty'])
def test_explicit_policy_skip_acknowledges_span(env, monkeypatch, reason):
    mem, project, snap, stats = env
    monkeypatch.setattr(mem, '_scribe_summarize_text', lambda *a: (None, reason))
    mem._checkpoint_worker(snap)
    assert watermark(mem, project)['byte_offset'] == 123
    assert watermark(mem, project)['running_summary'] == 'Previous knowledge'
    assert f'checkpoint_skipped:{reason}' in stats


@pytest.mark.parametrize('result', ['', None, RuntimeError('offline')])
def test_reduce_failure_cannot_drop_previous_knowledge(env, monkeypatch, result):
    mem, project, snap, stats = env
    monkeypatch.setattr(mem, '_scribe_summarize_text', lambda *a: ('New knowledge', 'extracted'))
    def call(*args):
        if isinstance(result, Exception):
            raise result
        return result
    monkeypatch.setattr(mem, '_scribe_call', call)
    mem._checkpoint_worker(snap)
    assert watermark(mem, project)['byte_offset'] == 12
    assert watermark(mem, project)['running_summary'] == 'Previous knowledge'


def test_retry_reprocesses_failed_span_then_commits(env, monkeypatch):
    mem, project, snap, stats = env
    offsets = []
    monkeypatch.setattr(mem, '_scribe_render_delta', lambda path, offset, provider='claude': (offsets.append(offset) or 'ACTION: work', 123))
    outcomes = iter([(None, 'model_error'), ('New knowledge', 'extracted')])
    monkeypatch.setattr(mem, '_scribe_summarize_text', lambda *a: next(outcomes))
    monkeypatch.setattr(mem, '_scribe_call', lambda *a: 'Previous and new knowledge')
    mem._checkpoint_worker(snap)
    mem._checkpoint_worker(snap)
    assert offsets == [12, 12]
    assert watermark(mem, project)['byte_offset'] == 123
    assert watermark(mem, project)['running_summary'] == 'Previous and new knowledge'


def test_write_exception_keeps_offset_pending(env, monkeypatch):
    mem, project, snap, stats = env
    monkeypatch.setattr(mem, '_scribe_summarize_text', lambda *a: ('New knowledge', 'extracted'))
    monkeypatch.setattr(mem, '_scribe_call', lambda *a: 'Previous and new knowledge')
    def fail(*a, **k):
        raise OSError('injected write failure')
    monkeypatch.setattr(mem, '_commit_managed_entry', fail)
    mem._checkpoint_worker(snap)
    assert watermark(mem, project)['byte_offset'] == 12
    assert 'checkpoint_extracted' not in stats


@pytest.mark.parametrize('failure', [RuntimeError('quota'), '', None, 'I do not see a transcript'])
def test_map_failure_rejects_surviving_partial_summary(env, monkeypatch, failure):
    mem, _, _, _ = env
    monkeypatch.setattr(mem, '_SCRIBE_SINGLE_LIMIT', 20)
    calls = []
    def call(model, prompt, text):
        calls.append(prompt)
        if len(calls) == 2:
            if isinstance(failure, Exception):
                raise failure
            return failure
        return 'A valid partial note'
    monkeypatch.setattr(mem, '_scribe_call', call)
    summary, reason = mem._scribe_summarize_text('ACTION ' + 'x' * 30 + '\nACTION ' + 'y' * 30, 'fake')
    assert (summary, reason) == (None, 'model_error')
    assert calls == [mem._SCRIBE_MAP_PROMPT, mem._SCRIBE_MAP_PROMPT]


def test_complete_map_coverage_reduces_all_chunks(env, monkeypatch):
    mem, _, _, _ = env
    monkeypatch.setattr(mem, '_SCRIBE_SINGLE_LIMIT', 20)
    calls = []
    def call(model, prompt, text):
        calls.append((prompt, text))
        return f'Note {len(calls)}'
    monkeypatch.setattr(mem, '_scribe_call', call)
    assert mem._scribe_summarize_text('ACTION ' + 'x' * 30 + '\nACTION ' + 'y' * 30, 'fake') == ('Note 3', 'extracted')
    assert calls[-1][1] == '- Note 1\n- Note 2'
