"""MC-1179 AP34: snapshot() is a read; it writes only when a row needs a claim."""
import json
import os

import pytest

from mc import agent_log_file, agent_log_maintenance
from test_runtime_completion_log import env, _log_rows  # noqa: F401


def _stamp(path):
    st = os.stat(path)
    return st.st_mtime_ns, st.st_size, path.read_bytes()


def _write(path, rows):
    path.write_text(json.dumps(rows, indent=2), encoding='utf-8')


def test_file_snapshot_of_a_fully_claimed_log_writes_nothing(tmp_path, monkeypatch):
    path = tmp_path / 'p_agent_log.json'
    _write(path, [{'session_id': 'a', 'delivery_attempt_id': 'x1'}])
    monkeypatch.setattr(agent_log_file, 'write_json_atomic',
                        lambda *a, **k: pytest.fail('a read must not rewrite the log'))
    before = agent_log_file.snapshot(path)
    assert before == [{'session_id': 'a', 'delivery_attempt_id': 'x1'}]


def test_file_snapshot_still_claims_a_row_without_an_attempt_id(tmp_path):
    path = tmp_path / 'p_agent_log.json'
    _write(path, [{'session_id': 'a'}])
    before = agent_log_file.snapshot(path)
    assert before[0]['delivery_attempt_id']
    assert json.loads(path.read_text(encoding='utf-8'))[0]['delivery_attempt_id'] \
        == before[0]['delivery_attempt_id']


def test_server_snapshot_of_a_fully_claimed_log_does_not_rewrite_it(env):
    ar = env['ar']
    path = ar._agent_log_path('proj1')
    _write(path, [{'session_id': 'a', 'delivery_attempt_id': 'x1'},
                  {'session_id': 'b', 'delivery_attempt_id': 'x2'}])
    stamp = _stamp(path)
    before = agent_log_maintenance.snapshot('proj1')
    assert [r['session_id'] for r in before] == ['a', 'b']
    assert _stamp(path) == stamp


def test_server_snapshot_returns_a_copy_not_the_loaded_rows(env):
    ar = env['ar']
    path = ar._agent_log_path('proj1')
    _write(path, [{'session_id': 'a', 'delivery_attempt_id': 'x1'}])
    first = agent_log_maintenance.snapshot('proj1')
    first[0]['session_id'] = 'changed'
    assert agent_log_maintenance.snapshot('proj1')[0]['session_id'] == 'a'


def test_server_snapshot_still_claims_an_unstamped_row(env):
    ar = env['ar']
    path = ar._agent_log_path('proj1')
    _write(path, [{'session_id': 'a'}])
    before = agent_log_maintenance.snapshot('proj1')
    assert before[0]['delivery_attempt_id']
    assert _log_rows(env)[0]['delivery_attempt_id'] == before[0]['delivery_attempt_id']


def test_server_snapshot_of_an_unreadable_log_still_refuses(env):
    ar = env['ar']
    ar._agent_log_path('proj1').write_text('{broken', encoding='utf-8')
    with pytest.raises(Exception):
        agent_log_maintenance.snapshot('proj1')
