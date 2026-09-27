"""MC-961 (backlog de998c45): opt-in engine fallback.

Ron's design (2026-09-26): an empty `engine_fallback_order` is a no-op (the
standing VENDOR_AGNOSTIC_PROGRAM.md §4 refusal is unchanged); once the user
opts in, an allowance/model-unavailable refusal may swap the dispatch to the
first configured, usable entry instead of failing the run — but the swap must
always be recorded (run history + agent_log + a visible chat line + a
notification) and a swap must NEVER happen silently.

Layers covered, cheapest first:
  1. mc/engine_fallback.py — pure functions, no Flask.
  2. agent_routes._apply_engine_fallback — the one place a swap is recorded
     (item 3's "no silent path may exist" gets its own test: the record calls
     are asserted, not just the return value).
  3. agent_routes._dispatch_agent_internal — the wiring: exhausted + no
     config raises EngineFallbackBlocked (never falls back on its own);
     exhausted + configured calls _apply_engine_fallback and carries its
     result into the dispatch (provider swap, chat line, non-claude runtime
     call); a resume never swaps regardless of config (native `-r` can't
     cross vendors).
  4. scheduler_routes._scheduler_loop — a blocked timer fire notifies through
     the same push surface a manual chat refusal's card would use.
  5. workflows._dispatch_step — a swapped step's record survives onto the
     run's own `steps` entry; a blocked step fails the run with the same
     vendor+reset+pointer message and notifies.
"""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc import engine_fallback as ef
from tests.test_revive_notify_carry import ar, _project  # noqa: F401 (fixture + helper)


# ── 1. pure module ───────────────────────────────────────────────────────────

def test_get_order_empty_by_default():
    assert ef.get_order({}) == []
    assert ef.get_order(None) == []


def test_get_order_drops_malformed_entries():
    cfg = {ef.CONFIG_KEY: [
        {'provider': 'gemini', 'model': 'gemini-3-pro'},
        {'provider': ''},          # no provider -> dropped
        'not-a-dict',              # wrong shape -> dropped
        {'model': 'x'},            # missing provider -> dropped
        {'provider': 'CODEX'},     # normalized to lowercase, model defaults ''
    ]}
    assert ef.get_order(cfg) == [
        {'provider': 'gemini', 'model': 'gemini-3-pro'},
        {'provider': 'codex', 'model': ''},
    ]


def test_get_order_non_list_value_is_ignored():
    assert ef.get_order({ef.CONFIG_KEY: 'gemini'}) == []


def test_resolve_fallback_skips_blocked_vendor_exhausted_and_unavailable(monkeypatch):
    from mc import allowance_state as al
    monkeypatch.setattr(al, '_STATE', {})
    al.record_exhaustion('gemini', limit_kind='usage_limit', resets_at_display='later')
    cfg = {ef.CONFIG_KEY: [
        {'provider': 'claude', 'model': ''},   # same as blocked -> skipped
        {'provider': 'gemini', 'model': ''},   # exhausted -> skipped
        {'provider': 'codex', 'model': ''},    # not installed -> skipped
        {'provider': 'qwen', 'model': 'qwen-max'},  # first usable
    ]}
    result = ef.resolve_fallback(
        'claude', cfg, is_available=lambda p: p == 'qwen')
    assert result == {'provider': 'qwen', 'model': 'qwen-max'}
    al._STATE.clear()


def test_resolve_fallback_none_when_order_empty():
    assert ef.resolve_fallback('claude', {}, is_available=lambda p: True) is None


def test_resolve_fallback_none_when_every_entry_unusable():
    cfg = {ef.CONFIG_KEY: [{'provider': 'gemini', 'model': ''}]}
    assert ef.resolve_fallback('claude', cfg, is_available=lambda p: False) is None


def test_resolve_fallback_availability_exception_is_skipped_not_raised():
    def boom(_p):
        raise RuntimeError('probe blew up')
    cfg = {ef.CONFIG_KEY: [{'provider': 'gemini', 'model': ''},
                           {'provider': 'qwen', 'model': ''}]}
    assert ef.resolve_fallback('claude', cfg, is_available=boom) is None


def test_blocked_payload_shape():
    payload = ef.blocked_payload('claude is out of allowance, resets 3pm')
    assert payload['allowance_blocked'] is True
    assert payload['refusal_message'] == 'claude is out of allowance, resets 3pm'
    assert payload['settings_deep_link'] == ef.SETTINGS_DEEP_LINK
    assert 'claude is out of allowance, resets 3pm' in payload['error']
    assert 'Settings' in payload['error']


def test_engine_fallback_blocked_str_is_the_payload_error():
    payload = ef.blocked_payload('X is out')
    exc = ef.EngineFallbackBlocked(payload)
    assert str(exc) == payload['error']
    assert exc.payload is payload
    assert isinstance(exc, ValueError)


def test_swap_record_and_chat_line():
    record = ef.swap_record(from_provider='claude', to_provider='gemini',
                            reason='claude is out of allowance',
                            reset_display='Sep 30, 2026 3:00 PM')
    assert record == {'from': 'claude', 'to': 'gemini',
                      'reason': 'claude is out of allowance',
                      'vendor_reset': 'Sep 30, 2026 3:00 PM'}
    line = ef.swap_chat_line(record)
    assert line.startswith('[Engine fallback: claude → gemini')
    assert 'claude is out of allowance' in line
    assert 'Sep 30, 2026 3:00 PM' in line


def test_swap_chat_line_omits_empty_reset():
    record = ef.swap_record(from_provider='claude', to_provider='gemini', reason='out')
    assert '()' not in ef.swap_chat_line(record)


# ── 2. _apply_engine_fallback — "no silent path may exist" ─────────────────

@pytest.fixture(autouse=True)
def _clean_allowance_state():
    from mc import allowance_state as al
    al._STATE = {}
    yield
    al._STATE = {}


def test_apply_engine_fallback_records_on_every_swap(ar, monkeypatch):
    """The literal brief requirement: a test that FAILS if a swap ever
    happens without the record. Both the durable activity-log write and the
    notification are asserted, not just the returned tuple."""
    activity_calls = []
    notify_calls = []
    monkeypatch.setattr(ar, '_log_agent_activity',
                        lambda *a, **k: activity_calls.append((a, k)))
    monkeypatch.setattr(ar, '_notify_push',
                        lambda **k: notify_calls.append(k))
    monkeypatch.setattr(ar, '_provider_available_for_fallback', lambda p: True)
    monkeypatch.setitem(ar.state.CONFIG, ef.CONFIG_KEY,
                        [{'provider': 'gemini', 'model': ''}])

    provider, model, record = ar._apply_engine_fallback(
        'claude', 'claude is out of allowance, resets 3pm', project_id='p1')

    assert provider == 'gemini'
    assert model is None
    assert record['from'] == 'claude' and record['to'] == 'gemini'
    assert len(activity_calls) == 1, 'swap happened without a run-history/agent_log record'
    assert len(notify_calls) == 1, 'swap happened without a notification'
    assert 'claude' in activity_calls[0][0][1] and 'gemini' in activity_calls[0][0][1]


def test_apply_engine_fallback_raises_when_nothing_configured(ar, monkeypatch):
    activity_calls = []
    notify_calls = []
    monkeypatch.setattr(ar, '_log_agent_activity',
                        lambda *a, **k: activity_calls.append((a, k)))
    monkeypatch.setattr(ar, '_notify_push',
                        lambda **k: notify_calls.append(k))

    with pytest.raises(ef.EngineFallbackBlocked) as exc:
        ar._apply_engine_fallback('claude', 'claude is out of allowance, resets 3pm',
                                  project_id='p1')

    assert exc.value.payload['refusal_message'] == 'claude is out of allowance, resets 3pm'
    assert exc.value.payload['settings_deep_link'] == ef.SETTINGS_DEEP_LINK
    # No fallback resolved -> no swap -> nothing to record.
    assert activity_calls == []
    assert notify_calls == []


def test_apply_engine_fallback_raises_when_every_entry_unusable(ar, monkeypatch):
    monkeypatch.setattr(ar, '_provider_available_for_fallback', lambda p: False)
    monkeypatch.setitem(ar.state.CONFIG, ef.CONFIG_KEY,
                        [{'provider': 'gemini', 'model': ''}])
    with pytest.raises(ef.EngineFallbackBlocked):
        ar._apply_engine_fallback('claude', 'claude is out of allowance', project_id='p1')


# ── 3. _dispatch_agent_internal wiring ──────────────────────────────────────

def test_dispatch_raises_blocked_when_exhausted_with_no_fallback_configured(ar, tmp_path, monkeypatch):
    """No-fallback message (item 4): names the vendor + reset time AND the
    settings pointer, on the same exception type the chat routes special-case
    to build the actionable card."""
    from mc import allowance_state as al
    project = _project(tmp_path)
    monkeypatch.setattr(ar, 'load_project', lambda _: project)
    monkeypatch.setattr(ar, 'probe_allowance', None, raising=False)
    from mc import agent_runtime
    for rt in agent_runtime._RUNTIMES.values():
        monkeypatch.setattr(rt, 'probe_allowance', lambda: None)
    al._LAST_PROBE.clear()
    al.record_exhaustion('claude', limit_kind='five_hour',
                         resets_at_display='Sep 24, 2026 7:58 AM')

    with pytest.raises(ef.EngineFallbackBlocked) as exc:
        ar._dispatch_agent_internal('p1', 'do something', provider_override='claude')

    payload = exc.value.payload
    assert payload['allowance_blocked'] is True
    assert 'claude' in payload['refusal_message']
    assert 'Sep 24, 2026 7:58 AM' in payload['refusal_message']
    assert payload['settings_deep_link'] == ef.SETTINGS_DEEP_LINK
    assert 'Settings' in payload['error']


def test_dispatch_swaps_provider_when_fallback_configured(ar, tmp_path, monkeypatch):
    """The walk: exhausted claude + configured fallback swaps the dispatch to
    the fallback vendor instead of failing the run, and the swap is loud
    (display_task carries the chat line)."""
    from mc import allowance_state as al
    project = _project(tmp_path)
    monkeypatch.setattr(ar, 'load_project', lambda _: project)
    from mc import agent_runtime
    for rt in agent_runtime._RUNTIMES.values():
        monkeypatch.setattr(rt, 'probe_allowance', lambda: None)
    al._LAST_PROBE.clear()
    al.record_exhaustion('claude', limit_kind='five_hour',
                         resets_at_display='Sep 24, 2026 7:58 AM')

    swap_calls = []
    def fake_apply(blocked_provider, refusal_message, *, project_id):
        record = ef.swap_record(from_provider=blocked_provider, to_provider='gemini',
                                reason=refusal_message, reset_display='Sep 24, 2026 7:58 AM')
        swap_calls.append(record)
        return 'gemini', None, record
    monkeypatch.setattr(ar, '_apply_engine_fallback', fake_apply)

    runtime_calls = []
    def fake_runtime_dispatch(p, task, *, provider_name, **kw):
        runtime_calls.append({'provider_name': provider_name, 'task': task, **kw})
        return 'fake-sid-gemini'
    monkeypatch.setattr(ar, '_dispatch_via_runtime', fake_runtime_dispatch)

    sid = ar._dispatch_agent_internal('p1', 'do something', provider_override='claude')

    assert sid == 'fake-sid-gemini'
    assert len(swap_calls) == 1
    assert len(runtime_calls) == 1
    assert runtime_calls[0]['provider_name'] == 'gemini'
    assert runtime_calls[0]['engine_fallback'] == swap_calls[0]
    # The chat-visible line (item 3) rides on display_task, not the raw task text.
    assert runtime_calls[0]['display_task'].startswith('[Engine fallback: claude → gemini')


def test_dispatch_never_swaps_on_a_resume(ar, tmp_path, monkeypatch):
    """A native `-r` resume can't cross vendors — exhaustion on a resumed
    conversation must hard-refuse even with a fallback configured, never
    silently (or loudly) swap it."""
    from mc import allowance_state as al
    project = _project(tmp_path)
    monkeypatch.setattr(ar, 'load_project', lambda _: project)
    from mc import agent_runtime
    for rt in agent_runtime._RUNTIMES.values():
        monkeypatch.setattr(rt, 'probe_allowance', lambda: None)
    al._LAST_PROBE.clear()
    al.record_exhaustion('claude', limit_kind='five_hour',
                         resets_at_display='Sep 24, 2026 7:58 AM')
    monkeypatch.setitem(ar.state.CONFIG, ef.CONFIG_KEY,
                        [{'provider': 'gemini', 'model': ''}])

    apply_calls = []
    monkeypatch.setattr(ar, '_apply_engine_fallback',
                        lambda *a, **k: apply_calls.append((a, k)) or (_ for _ in ()).throw(
                            AssertionError('must not be called on a resume')))

    with pytest.raises(ef.EngineFallbackBlocked):
        ar._dispatch_agent_internal('p1', 'still there?', resume_id='native-thread-1',
                                    provider_override='claude')
    assert apply_calls == []


# ── 4. scheduler blocked-run notification ───────────────────────────────────

def test_scheduler_notifies_on_blocked_fallback(tmp_path, monkeypatch):
    import server  # noqa: F401
    from mc.blueprints import local_auth as la
    from mc.blueprints import scheduler_routes as sr
    from mc import state

    monkeypatch.setattr(la, 'LOCAL_AUTH_PATH', tmp_path / 'local_auth.json')
    sched_path = tmp_path / 'schedules.json'
    monkeypatch.setattr(sr, 'SCHEDULES_PATH', sched_path)
    projects = [{'id': 'p1', 'name': 'Project One', 'project_path': str(tmp_path / 'ws')}]
    monkeypatch.setattr(sr, 'load_projects', lambda: list(projects))
    monkeypatch.setattr(sr, 'load_project',
                        lambda pid: next((p for p in projects if p['id'] == pid), None))
    monkeypatch.setattr(sr, '_latest_session_id_for_schedule', lambda pid, sid: '')
    monkeypatch.setattr(sr, '_latest_claude_sid_for_schedule', lambda pid, sid: '')
    monkeypatch.setattr(sr, '_newest_run_session_id_for_schedule', lambda pid, sid: '')
    monkeypatch.setattr(sr, '_enrich_run_entries', lambda entries: entries)
    monkeypatch.setattr(sr, '_log_agent_activity', lambda *a, **k: None)

    payload = ef.blocked_payload('claude is out of allowance, resets 3pm')
    exc = ef.EngineFallbackBlocked(payload)
    monkeypatch.setattr(sr, '_dispatch_agent_internal', lambda *a, **k: (_ for _ in ()).throw(exc))

    notify_calls = []
    monkeypatch.setattr(sr, '_notify_push', lambda **k: notify_calls.append(k))

    sched_path.write_text(__import__('json').dumps([{
        'id': 's1', 'project_id': 'p1', 'task': 'nightly digest',
        'enabled': True, 'schedule_type': 'cron', 'cron_expr': '*/5 * * * *',
        'next_run': '2020-01-01T00:00:00Z',
    }]), encoding='utf-8')

    # Same stand-in as test_scheduler_routes.py's _OneShotStop: falsy on the
    # first is_set() so the loop body runs exactly once, truthy after so it
    # returns instead of blocking on the real 30s wait() (a bare `return
    # False` here spins _scheduler_loop's `while not is_set()` forever).
    class _OneShotStop:
        def __init__(self):
            self.checks = 0
        def is_set(self):
            self.checks += 1
            return self.checks > 1
        def wait(self, _timeout):
            return True
    monkeypatch.setattr(sr, '_scheduler_stop', _OneShotStop())
    monkeypatch.setitem(state.CONFIG, 'scheduler_paused', False)
    sr._scheduler_loop()

    assert len(notify_calls) == 1
    assert notify_calls[0]['title'] == 'Scheduled run blocked'
    assert 'Settings' in notify_calls[0]['body']
    assert 'claude is out of allowance' in notify_calls[0]['body']
    rows = __import__('json').loads(sched_path.read_text(encoding='utf-8'))
    assert 'Settings' in rows[0]['last_error']


# ── 5. workflow step swap record + blocked notification ────────────────────

@pytest.fixture()
def wfm_ctx(tmp_path, monkeypatch):
    import server  # noqa: F401
    from mc import workflows as wfm
    from mc import state
    monkeypatch.setattr(wfm, 'WORKFLOWS_PATH', tmp_path / 'workflows.json')
    monkeypatch.setattr(wfm, 'WORKFLOW_RUNS_DIR', tmp_path / 'workflow_runs')
    (tmp_path / 'workflow_runs').mkdir(parents=True, exist_ok=True)
    snapshot = dict(state.agent_sessions)
    state.agent_sessions.clear()
    try:
        yield wfm
    finally:
        state.agent_sessions.clear()
        state.agent_sessions.update(snapshot)


def _bare_run(run_id='run1'):
    return {'id': run_id, 'status': 'running', 'steps': {}}


def _bare_node(name='only', project_id='p1'):
    return {'name': name, 'project_id': project_id, 'character': '',
           'prompt': 'do the thing', 'outcomes': []}


def test_dispatch_step_records_swap_from_the_live_session(wfm_ctx, monkeypatch):
    from mc import state
    record = ef.swap_record(from_provider='claude', to_provider='gemini', reason='out')
    def fake_dispatch(project_id, prompt, **kw):
        state.agent_sessions['sess-1'] = {'engine_fallback': record}
        return 'sess-1'
    monkeypatch.setattr(wfm_ctx, '_dispatch_agent_internal', fake_dispatch)

    run = _bare_run()
    ok = wfm_ctx._dispatch_step(run, _bare_node())

    assert ok is True
    assert run['steps']['only']['engine_fallback'] == record


def test_dispatch_step_blocked_fails_run_and_notifies(wfm_ctx, monkeypatch, tmp_path):
    payload = ef.blocked_payload('claude is out of allowance, resets 3pm')
    exc = ef.EngineFallbackBlocked(payload)
    monkeypatch.setattr(wfm_ctx, '_dispatch_agent_internal',
                        lambda *a, **k: (_ for _ in ()).throw(exc))
    notify_calls = []
    monkeypatch.setattr(wfm_ctx, '_notify_push', lambda **k: notify_calls.append(k))

    run = _bare_run()
    ok = wfm_ctx._dispatch_step(run, _bare_node())

    assert ok is False
    assert run['status'] == 'failed'
    assert 'Settings' in run['error']
    assert 'claude is out of allowance' in run['error']
    assert len(notify_calls) == 1
    assert notify_calls[0]['title'] == 'Workflow run blocked'
    assert 'Settings' in notify_calls[0]['body']
