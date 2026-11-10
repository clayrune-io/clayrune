"""Strict history authority and serialization at the launch boundary."""
from copy import deepcopy
import threading

import pytest

from mc import launch_transaction as tx
from mc.agent_log_file import transaction
from test_runtime_completion_log import env, _log_rows  # noqa: F401
from test_launch_transaction import _completed


@pytest.mark.parametrize('content', ['{broken', '{}', '[null]', '[1]', 'null'])
def test_invalid_snapshot_refuses_without_touching_history_or_live_turn(env, content):
    ar = env['ar']
    session = _completed(env)
    before = {key: deepcopy(session[key]) for key in tx._TURN_KEYS if key in session}
    path = ar._agent_log_path('proj1')
    path.write_text(content, encoding='utf-8')
    with pytest.raises(OSError, match='snapshot'):
        tx.start_turn(session, None)
    assert path.read_text(encoding='utf-8') == content
    assert not list(path.parent.glob('*.corrupt-*'))
    assert {key: session[key] for key in tx._TURN_KEYS if key in session} == before


def test_unreadable_launch_check_never_spawns_or_changes_the_reservation(env, monkeypatch):
    ar = env['ar']
    session = _completed(env)
    attempt = tx.start_turn(session, None)
    before = _log_rows(env)

    def refuse(*args):
        raise PermissionError('read retries exhausted')

    monkeypatch.setattr(ar, 'read_text_with_retry', refuse)
    with pytest.raises(OSError, match='snapshot is unreadable'):
        attempt.launch(lambda: pytest.fail('unreadable ownership must refuse spawn'))
    assert not attempt.launched
    assert not attempt.restored
    assert _log_rows(env) == before
    assert session['_delivery_attempt_id'] == attempt.attempt.attempt_id


@pytest.mark.parametrize('writer', ['leaf', 'shared-file'])
def test_snapshot_and_reservation_exclude_unrelated_leaf_and_offline_writers(env, writer):
    ar = env['ar']
    session = _completed(env)
    prior = _log_rows(env)
    attempted, entered = threading.Event(), threading.Event()
    errors = []
    path = ar._agent_log_path('proj1')

    def concurrent_write():
        attempted.set()
        try:
            if writer == 'leaf':
                ar._update_agent_log('proj1', lambda rows: entered.set())
            else:
                with transaction(path):
                    entered.set()
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=concurrent_write)

    def reserve():
        # The worker begins only after the authoritative snapshot. It cannot
        # enter either writer boundary until the reservation has finished.
        worker.start()
        assert attempted.wait(2)
        assert not entered.wait(0.1)
        ar._log_agent_dispatch_pending(session, strict=True)

    try:
        attempt = tx.reserve(session, reserve)
    finally:
        worker.join(timeout=5)
    assert not worker.is_alive()
    assert not errors
    assert entered.is_set()
    assert attempt.prior_row == prior[0]
    assert attempt.restore()
    assert _log_rows(env) == prior
