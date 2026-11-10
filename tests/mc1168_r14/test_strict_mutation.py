"""Mutation reads must succeed before any callback or save can run."""
import inspect
from copy import deepcopy

import pytest

from mc import launch_transaction as tx
from test_runtime_completion_log import env, _log_rows  # noqa: F401
from test_launch_transaction import _completed


@pytest.mark.parametrize('boundary', ['reserve', 'restore'])
def test_persistent_leaf_failure_never_mutates_or_saves(env, monkeypatch, boundary):
    ar = env['ar']
    session = _completed(env)
    attempt = tx.start_turn(session, None) if boundary == 'restore' else None
    before = _log_rows(env)
    live = deepcopy({k: session[k] for k in tx._TURN_KEYS if k in session})
    original = ar.read_text_with_retry
    original_update = ar._update_agent_log
    calls, saves, mutators = [], [], []

    def tracked_update(project_id, mutate):
        def tracked_mutator(rows):
            mutators.append(True)
            return mutate(rows)
        return original_update(project_id, tracked_mutator)

    def read(path, *args, **kwargs):
        names = [frame.function for frame in inspect.stack()]
        if names[1:3] == ['_load_agent_log', '_update_agent_log']:
            calls.append(True)
            raise PermissionError('persistent leaf read failure')
        return original(path, *args, **kwargs)

    def update(rows):
        rows.clear()

    monkeypatch.setattr(ar, 'read_text_with_retry', read)
    monkeypatch.setattr(ar, '_update_agent_log', tracked_update)
    monkeypatch.setattr(ar, '_save_agent_log', lambda *a: saves.append(True))
    with pytest.raises(PermissionError, match='persistent'):
        if attempt:
            attempt.restore()
        else:
            tx.reserve(session, lambda: ar._update_agent_log('proj1', update))
    assert len(calls) == 2
    assert not saves
    assert not mutators
    assert _log_rows(env) == before
    assert {k: session[k] for k in tx._TURN_KEYS if k in session} == live


@pytest.mark.parametrize('content', ['{broken', '{}', '[null]', '[1]', 'null'])
def test_invalid_mutation_input_never_saves_or_quarantines(env, monkeypatch, content):
    ar = env['ar']
    path = ar._agent_log_path('proj1')
    path.write_text(content, encoding='utf-8')
    saves, mutations = [], []
    monkeypatch.setattr(ar, '_save_agent_log', lambda *a: saves.append(True))
    with pytest.raises(OSError, match='unreadable'):
        ar._update_agent_log('proj1', lambda rows: mutations.append(True))
    assert not mutations
    assert not saves
    assert path.read_text(encoding='utf-8') == content
    assert not list(path.parent.glob('*.corrupt-*'))


@pytest.mark.parametrize('boundary', ['mutator', 'save'])
def test_read_retry_never_replays_mutation_or_save(env, monkeypatch, boundary):
    ar = env['ar']
    _completed(env)
    before = _log_rows(env)
    mutations, saves = [], []

    def update(rows):
        mutations.append(True)
        rows.clear()
        if boundary == 'mutator':
            raise OSError('callback failed')

    def save(*args):
        saves.append(True)
        raise OSError('save failed')

    monkeypatch.setattr(ar, '_save_agent_log', save)
    with pytest.raises(OSError):
        ar._update_agent_log('proj1', update)
    assert mutations == [True]
    assert saves == ([] if boundary == 'mutator' else [True])
    assert _log_rows(env) == before


@pytest.mark.parametrize('successor', [False, True])
def test_rollback_reread_preserves_intervening_rows_and_successor(env, monkeypatch, successor):
    ar = env['ar']
    session = _completed(env)
    before = _log_rows(env)
    attempt = tx.start_turn(session, None)
    unrelated = {'session_id': 'intervening', 'summary': 'new unrelated result'}

    def intervene(rows):
        rows.append(unrelated)
        if successor:
            rows[0].update(delivery_attempt_id='successor', summary='successor result')

    ar._update_agent_log('proj1', intervene)
    latest = _log_rows(env)
    original = ar.read_text_with_retry
    faults = []

    def read(path, *args, **kwargs):
        names = [frame.function for frame in inspect.stack()]
        if not faults and names[1:3] == ['_load_agent_log', '_update_agent_log']:
            faults.append(True)
            raise PermissionError('one failed rollback read')
        return original(path, *args, **kwargs)

    monkeypatch.setattr(ar, 'read_text_with_retry', read)
    assert attempt.restore() is (not successor)
    assert faults == [True]
    assert _log_rows(env) == (latest if successor else before + [unrelated])
