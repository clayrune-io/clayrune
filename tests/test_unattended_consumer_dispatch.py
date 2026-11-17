"""ML4 (docs/_review/2026-11-16_03_memory_learning.md) — the READER half of
_UNATTENDED_LOOP_RULE at dispatch.

Writers stamp `origin` with `distiller.is_unattended_session`: anything whose
`trigger_type` is not 'manual' is unattended. The dispatch-time reader used the
task TEXT only, so a session started by an ordinary schedule or by another
agent wrote as unattended and read as attended: its read floor and its
exploration read floor delivered unattended-origin notes. These tests dispatch
with a plain task (no steward marker, no "you run unattended") and pin what the
two read floors are told about the consumer.
"""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from test_launch_marker import env as dispatch_env  # noqa: E402,F401

PLAIN_TASK = 'tidy the changelog headings'
UNATTENDED_TRIGGERS = ['schedule', 'dispatch', 'workflow', 'hivemind_worker', '', 'some_future_type']


@pytest.fixture()
def floors(tmp_path, monkeypatch):
    """`_build_agent_context` with both read floors replaced by recorders."""
    import server  # noqa: F401
    from mc import memory as mem
    from mc import state as mc_state
    from mc.blueprints import agent_routes as ar

    monkeypatch.setattr(mem, '_get_memory_path', lambda p: tmp_path / 'MEMORY.md')
    monkeypatch.setitem(mc_state.CONFIG, 'exploration_readback_enabled', True)
    seen = {}

    def fake_memory_search(project, task, topk, **kw):
        seen['memory'] = kw.get('consumer_unattended')
        return []

    def fake_exploration_floor(project_id, task, topk, **kw):
        seen['exploration'] = kw.get('consumer_unattended')
        return []

    monkeypatch.setattr(ar, '_memory_search', fake_memory_search)
    monkeypatch.setattr(ar._distiller, 'exploration_read_floor', fake_exploration_floor)
    project = {'id': 'p1', 'project_path': str(tmp_path)}
    registered = []

    def build(trigger_type=None, session_trigger=None, sid='sess-ml4', task=PLAIN_TASK, **kw):
        if session_trigger is not None:
            mc_state.agent_sessions[sid] = {'session_id': sid, 'trigger_type': session_trigger}
            registered.append(sid)
        if trigger_type is not None:
            kw['trigger_type'] = trigger_type
        seen.clear()
        ar._build_agent_context(project, task=task, session_id=sid, **kw)
        return dict(seen)

    try:
        yield build
    finally:
        for sid in registered:
            mc_state.agent_sessions.pop(sid, None)


# ── the registered session's own provenance decides (respawn / non-claude) ──

@pytest.mark.parametrize('trigger', UNATTENDED_TRIGGERS)
def test_a_registered_non_manual_session_is_an_unattended_reader(floors, trigger):
    assert floors(session_trigger=trigger) == {'memory': True, 'exploration': True}


def test_a_registered_manual_session_is_an_attended_reader(floors):
    assert floors(session_trigger='manual') == {'memory': False, 'exploration': False}


# ── a fresh dispatch: the session dict does not exist yet, the caller knows ──

@pytest.mark.parametrize('trigger', UNATTENDED_TRIGGERS)
def test_the_dispatchs_own_trigger_type_decides_before_the_session_exists(floors, trigger):
    assert floors(trigger_type=trigger) == {'memory': True, 'exploration': True}


def test_a_manual_dispatch_with_no_session_yet_is_an_attended_reader(floors):
    assert floors(trigger_type='manual') == {'memory': False, 'exploration': False}


def test_the_dispatchs_trigger_type_outranks_a_stale_dict_under_a_reused_id(floors):
    # A reused session id can still have its PREVIOUS run's dict registered. The
    # session this dispatch is about to create carries the dispatch's value.
    assert floors(trigger_type='schedule', session_trigger='manual') == {
        'memory': True, 'exploration': True}


def test_a_named_session_with_no_known_provenance_fails_closed(floors):
    assert floors(sid='never-registered') == {'memory': True, 'exploration': True}


def test_the_text_marker_still_wins_for_a_manual_trigger(floors):
    got = floors(trigger_type='manual', task='You run unattended. ' + PLAIN_TASK)
    assert got == {'memory': True, 'exploration': True}


def test_an_explicit_unattended_flag_is_never_downgraded(floors):
    got = floors(trigger_type='manual', consumer_unattended=True)
    assert got == {'memory': True, 'exploration': True}


# ── the real dispatch and revive paths hand that value over ─────────────────

def _record_context_kwargs(env):
    calls = []

    def fake_context(*a, **k):
        calls.append(k)
        return 'context'

    env['monkeypatch'].setattr(env['ar'], '_build_agent_context', fake_context)
    env['monkeypatch'].setattr(
        env['ar'], '_dispatch_with_routing_parallel',
        lambda p, task, context_builder=None, **k: (
            'sonnet', 'auto', [], context_builder(), ''))
    return calls


@pytest.mark.parametrize('trigger', ['schedule', 'dispatch', 'manual'])
def test_dispatch_tells_the_context_builder_its_trigger_type(dispatch_env, trigger):
    calls = _record_context_kwargs(dispatch_env)
    dispatch_env['set_mode'](True)
    dispatch_env['ar']._dispatch_agent_internal('p1', PLAIN_TASK, trigger_type=trigger)
    assert calls, 'dispatch never built a context'
    assert [c.get('trigger_type') for c in calls] == [trigger] * len(calls)


@pytest.mark.parametrize('trigger', ['schedule', 'dispatch', 'manual'])
def test_dispatch_with_a_pinned_model_tells_the_context_builder_too(dispatch_env, trigger):
    calls = _record_context_kwargs(dispatch_env)
    dispatch_env['set_mode'](True)
    dispatch_env['ar']._dispatch_agent_internal(
        'p1', PLAIN_TASK, trigger_type=trigger, model_override='sonnet')
    assert calls, 'dispatch never built a context'
    assert [c.get('trigger_type') for c in calls] == [trigger] * len(calls)


@pytest.mark.parametrize('trigger', ['dispatch', 'manual'])
def test_revive_tells_the_context_builder_the_logged_trigger_type(dispatch_env, trigger):
    calls = _record_context_kwargs(dispatch_env)
    ar = dispatch_env['ar']
    dispatch_env['set_mode'](False)
    entry = {'session_id': 'old-sid', 'claude_session_id': 'claude-old',
             'trigger_type': trigger, 'project_id': 'p1', 'task': 'earlier',
             'provider': 'claude'}
    dispatch_env['monkeypatch'].setattr(ar, '_load_agent_log', lambda pid, **k: [entry])
    ar._revive_from_agent_log('p1', 'old-sid', 'hello again', dispatch_env['project'])
    assert calls, 'revive never built a context'
    assert [c.get('trigger_type') for c in calls] == [trigger] * len(calls)


# ── turn 1's delivered-set seed uses the same test as the builder ───────────

@pytest.fixture()
def seed(monkeypatch):
    from mc import memory_turn

    seen = {}

    def fake_memory_search(project, task, topk, **kw):
        seen['memory'] = kw.get('consumer_unattended')
        return []

    monkeypatch.setattr(memory_turn, 'enabled', lambda: True)
    monkeypatch.setattr(memory_turn._mem, '_memory_search', fake_memory_search)

    def run(session):
        seen.clear()
        memory_turn.seed_delivered({'id': 'p1'}, session, PLAIN_TASK)
        return seen.get('memory')

    return run


@pytest.mark.parametrize('trigger', UNATTENDED_TRIGGERS)
def test_seed_delivered_treats_a_non_manual_session_as_unattended(seed, trigger):
    assert seed({'session_id': 's1', 'trigger_type': trigger}) is True


def test_seed_delivered_treats_a_session_with_no_trigger_type_as_unattended(seed):
    assert seed({'session_id': 's1'}) is True


def test_seed_delivered_keeps_a_manual_session_attended(seed):
    assert seed({'session_id': 's1', 'trigger_type': 'manual'}) is False
