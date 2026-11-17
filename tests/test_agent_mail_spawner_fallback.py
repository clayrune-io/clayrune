"""MC-1177: a reply to a dispatched chat whose worktree is gone goes to its spawner."""
import io
import json
import threading
from types import SimpleNamespace

import pytest

from test_agent_mail_wake import TEXT, ready, setup  # noqa: F401
from test_runtime_completion_log import env  # noqa: F401
from test_runtime_completion_log import _mode_b_session
from mc.agent_mail_store import SUBMITTED


@pytest.fixture
def spawned(setup):
    """The bound chat lost its worktree; 'spawner' dispatched it and is idle."""
    spawner = _mode_b_session({'sessions': setup.sessions}, 'spawner')
    spawner.update(status='idle', claude_session_id='native-spawner', project_generation=1,
                   proc=SimpleNamespace(pid=-992, poll=lambda: None, stdin=io.StringIO()),
                   stdin_lock=threading.Lock(), _agent_cwd=str(setup.tmp_path))
    setup.session.update(_spawned_by='spawner', _agent_cwd='missing-tree')
    setup.spawner = spawner
    return setup


def test_forwarded_to_spawner_with_prefix_and_no_hold(spawned):
    ready(spawned)
    result = spawned.wake.process_next(spawned.app)
    assert result['state'] == SUBMITTED
    content = json.loads(spawned.spawner['proc'].stdin.getvalue())['message']['content']
    assert TEXT in content
    assert '[Email reply from the project owner to a dispatched agent (session mail-session)' in content
    assert 'forwarded to you as its spawner' in content
    assert spawned.session['proc'].stdin.getvalue() == ''
    assert spawned.store.get_event('mail-one')['state'] == SUBMITTED
    assert spawned.wake.process_next(spawned.app)['state'] == 'idle'  # nothing held behind it


def test_no_spawner_keeps_the_hold(spawned):
    spawned.session.pop('_spawned_by')
    ready(spawned)
    result = spawned.wake.process_next(spawned.app)
    assert result['state'] == 'blocked' and result['reason'] == 'worktree_unavailable'
    assert spawned.spawner['proc'].stdin.getvalue() == ''


@pytest.mark.parametrize('change', [
    {'incognito': True},                    # fails its own snapshot
    {'_agent_cwd': 'missing-tree'},         # spawner's own worktree gone too
    {'project_id': 'other-project'},        # not in the same project
    {'status': 'stopped'},                  # closed
    {'_launch_uncertain': True},            # provider recovery held
])
def test_invalid_spawner_keeps_the_hold(spawned, change):
    spawned.spawner.update(change)
    ready(spawned)
    result = spawned.wake.process_next(spawned.app)
    assert result['state'] == 'blocked' and result['reason'] == 'worktree_unavailable'
    assert spawned.spawner['proc'].stdin.getvalue() == ''
    assert spawned.session['proc'].stdin.getvalue() == ''


def test_spawner_not_live_keeps_the_hold(spawned):
    spawned.sessions.pop('spawner')
    ready(spawned)
    result = spawned.wake.process_next(spawned.app)
    assert result['state'] == 'blocked' and result['reason'] == 'worktree_unavailable'


def test_session_naming_itself_as_spawner_keeps_the_hold(spawned):
    spawned.session['_spawned_by'] = 'mail-session'
    ready(spawned)
    assert spawned.wake.process_next(spawned.app)['reason'] == 'worktree_unavailable'


def test_transient_reason_is_retried_not_forwarded(spawned):
    # MC-1186: provider_recovery_held used to be a permanent hold; it now retries, still unforwarded.
    spawned.session.update(_agent_cwd=str(spawned.tmp_path), _launch_uncertain=True)
    ready(spawned)
    result = spawned.wake.process_next(spawned.app)
    assert result['state'] == 'retryable' and result['reason'] == 'provider_recovery_held'
    assert spawned.spawner['proc'].stdin.getvalue() == ''


@pytest.mark.parametrize('change,reason', [
    ({'claude_session_id': 'different-native'}, 'conversation_identity_changed'),
    ({'status': 'stopped'}, 'session_closed'),
    ({'incognito': True}, 'incognito_not_persistent'),
    ({'character': {'name': 'different'}}, 'persona_changed'),
])
def test_other_reasons_are_never_forwarded(spawned, change, reason):
    spawned.session.update(_agent_cwd=str(spawned.tmp_path))  # worktree is fine
    spawned.session.update(change)
    ready(spawned)
    result = spawned.wake.process_next(spawned.app)
    assert result['state'] == 'blocked' and result['reason'] == reason
    assert spawned.spawner['proc'].stdin.getvalue() == ''
