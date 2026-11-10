"""Real queue/storage read faults after the new strict snapshot succeeds."""
import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
from conftest import *
import pytest
from test_runtime_completion_log import env, _log_rows
from test_launch_transaction import _completed
from mc import launch_transaction as tx


@pytest.mark.parametrize('fault_at', ['none', 'reserve', 'restore'])
def test_queue_refusal_keeps_all_history_after_late_read_fault(env, monkeypatch, fault_at):
    ar = env['ar']
    session = _completed(env)
    session['pending_followups'] = ['later input']
    ar._update_agent_log('proj1', lambda rows: rows.append({
        'session_id': 'unrelated-completed', 'delivery_attempt_id': 'unrelated-owner',
        'status': 'completed', 'summary': 'unrelated answer', 'usage': {'input_tokens': 456}}))
    before = _log_rows(env)
    original = ar.read_text_with_retry
    faults, reads, spawns = [], [], []

    def read(path, *args, **kwargs):
        names = [f.function for f in inspect.stack()]
        reads.append(names[1:7])
        is_leaf_load = names[1:3] == ['_load_agent_log', '_update_agent_log']
        boundary = 'restore' if 'restore' in names else 'reserve' if 'reserve' in names else None
        if not faults and is_leaf_load and boundary == fault_at:
            faults.append(boundary)
            raise PermissionError('transient leaf read failure after successful readability check')
        return original(path, *args, **kwargs)

    def refuse(*args, **kwargs):
        spawns.append(True)
        exc = FileNotFoundError('provider executable missing; no child')
        exc._mc_prechild_refusal = True
        raise exc

    monkeypatch.setattr(ar, 'read_text_with_retry', read)
    monkeypatch.setattr(ar, 'spawn_engine_proc', tx.spawn_boundary(refuse))
    ar._auto_dispatch_followup_locked(session, 'first input', env['project'],
                                     env['project']['project_path'], ['fake'], None)
    after = _log_rows(env)
    print({'fault_at': fault_at, 'faults': faults, 'reads': reads,
           'spawns': spawns, 'before': before, 'after': after,
           'queue': session['pending_followups']})
    assert faults == ([] if fault_at == 'none' else [fault_at])
    assert after == before, 'later forgiving read erased history despite authoritative launch snapshot'
    assert session['pending_followups'] == ['first input', 'later input']
