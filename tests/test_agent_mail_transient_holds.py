"""MC-1186 / MB6: transient refusals are retried instead of becoming permanent holds.

provider_quota_held, provider_recovery_held and a busy spawner go to the store's
retryable state (existing backoff); every other reason keeps its hold, and a
rollover_required reply still needs a human (the chat must be opened once).
"""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from mc.agent_mail_recovery import Recovery
from mc.agent_mail_store import RETRYABLE, SUBMITTED
from test_agent_mail_spawner_fallback import spawned  # noqa: F401
from test_agent_mail_wake import ready, setup  # noqa: F401
from test_runtime_completion_log import env  # noqa: F401


def due_now(setup):
    with setup.store._tx() as db:
        db.execute('UPDATE events SET next_attempt=0')


def test_provider_recovery_held_is_retried_and_then_delivered(setup):
    ready(setup)
    setup.session['_launch_uncertain'] = True
    result = setup.wake.process_next(setup.app)
    assert (result['state'], result['reason']) == (RETRYABLE, 'provider_recovery_held')
    assert setup.session['proc'].stdin.getvalue() == ''
    setup.session.pop('_launch_uncertain')              # the engine recovered
    assert setup.wake.process_next(setup.app)['state'] == 'idle'   # backoff not yet due
    due_now(setup)
    assert setup.wake.process_next(setup.app)['state'] == SUBMITTED
    assert 'Friday works for me.' in json.loads(setup.session['proc'].stdin.getvalue())['message']['content']


def test_provider_quota_held_is_retried_and_then_delivered(setup, monkeypatch):
    from mc.blueprints import agent_routes as ar
    ready(setup)
    monkeypatch.setattr(ar, '_allowance_refusal', lambda *a, **k: 'exhausted')
    result = setup.wake.process_next(setup.app)
    assert (result['state'], result['reason']) == (RETRYABLE, 'provider_quota_held')
    monkeypatch.setattr(ar, '_allowance_refusal', lambda *a, **k: None)   # allowance returned
    due_now(setup)
    assert setup.wake.process_next(setup.app)['state'] == SUBMITTED


def test_later_reply_in_the_chat_is_not_frozen_forever(setup):
    ready(setup, 'mail-one')
    ready(setup, 'mail-two', 'Second reply.')
    setup.session['_launch_uncertain'] = True
    assert setup.wake.process_next(setup.app)['state'] == RETRYABLE
    assert setup.wake.process_next(setup.app)['state'] == 'idle'      # order kept behind it
    setup.session.pop('_launch_uncertain')
    due_now(setup)
    assert setup.wake.process_next(setup.app)['event_id'] == 'mail-one'


def test_retry_gives_up_into_a_visible_hold_after_the_store_attempt_limit(setup):
    ready(setup)
    setup.session['_launch_uncertain'] = True
    last = None
    for _ in range(setup.store.config.max_attempts + 1):
        due_now(setup)
        last = setup.wake.process_next(setup.app)
        if last['state'] == 'blocked':
            break
    assert (last['state'], last['reason']) == ('blocked', 'retry_exhausted')


def test_rollover_required_still_holds_and_is_not_auto_freshed(setup, monkeypatch):
    from mc.blueprints import agent_routes as ar
    ready(setup)
    monkeypatch.setattr(ar, '_auto_fresh_trigger', lambda *a, **k: ('context_size', None))
    result = setup.wake.process_next(setup.app)
    assert (result['state'], result['reason']) == ('blocked', 'rollover_required')
    assert setup.session['proc'].stdin.getvalue() == ''


def test_rollover_hold_text_says_the_chat_must_be_opened_once():
    js = (Path(__file__).resolve().parents[1] / 'static/js/agent-mail-holds.js').read_text(encoding='utf-8')
    text = next(line for line in js.splitlines() if line.strip().startswith('rollover_required:'))
    assert 'open the chat' in text.lower() and 'release' in text.lower()


@pytest.mark.parametrize('change,reason', [
    ({'status': 'stopped'}, 'session_closed'),
    ({'incognito': True}, 'incognito_not_persistent'),
    ({'character': {'name': 'different'}}, 'persona_changed'),
    ({'claude_session_id': 'different-native'}, 'conversation_identity_changed'),
])
def test_other_reasons_stay_permanent_holds(setup, change, reason):
    ready(setup)
    setup.session.update(change)
    result = setup.wake.process_next(setup.app)
    assert (result['state'], result['reason']) == ('blocked', reason)


def test_busy_spawner_is_retried_and_then_delivered(spawned):  # noqa: F811
    ready(spawned)
    spawned.spawner['status'] = 'running'
    result = spawned.wake.process_next(spawned.app)
    assert result['state'] == RETRYABLE and result['reason'] == 'spawner_busy'
    assert spawned.spawner['proc'].stdin.getvalue() == ''
    spawned.spawner['status'] = 'idle'
    due_now(spawned)
    assert spawned.wake.process_next(spawned.app)['state'] == SUBMITTED
    assert 'forwarded to you as its spawner' in json.loads(
        spawned.spawner['proc'].stdin.getvalue())['message']['content']


def test_recovery_does_not_count_a_retrying_reply_as_a_failed_step():
    states = iter([{'state': 'retryable', 'reason': 'provider_quota_held'}, {'state': 'idle', 'reason': ''}])
    factory = SimpleNamespace(require_pull_store=lambda: SimpleNamespace(reconcile=lambda: None))
    recovery = Recovery(factory, wake=lambda: next(states))
    step = recovery._inbound(factory.require_pull_store())
    assert step.state == 'ok'


def test_recovery_still_fails_the_step_for_a_retryable_launder_stage():
    factory = SimpleNamespace(require_pull_store=lambda: SimpleNamespace(reconcile=lambda: None))
    recovery = Recovery(factory, launder=lambda: {'state': 'retryable', 'reason': 'extractor_timeout'})
    assert recovery._inbound(factory.require_pull_store()).state == 'failed'
