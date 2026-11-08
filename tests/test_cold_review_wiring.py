"""MC-1165 wiring: reviewer = toolless one-shot, review on a worker thread before
merge-back, 2 attempts per tip SHA with visible state, and a cold record required
whenever `cold_review_required` is on. Temporary repos; the model call is a fake
`transform`; nothing is dispatched and no server runs."""
from __future__ import annotations

import json
import threading

import pytest
from flask import Flask

import mc.agent_worktree as w
import mc.cold_review_gate as cg
import mc.cold_review_runner as runner
import mc.merge_review_gate as gate
from mc import caller_attribution as ca, cold_review
from mc.blueprints import merge_review_routes as rr, project_routes
from mc.state import agent_sessions
from test_merge_review_gate import _agent, _commit, _git, _register, _store, repo  # noqa: F401

PASS_REPLY = json.dumps({'verdict': 'pass', 'summary': 'fine'})
CHANGES_REPLY = json.dumps({'verdict': 'changes_requested', 'summary': 'bug in one()'})


@pytest.fixture
def project(repo):
    return {'id': 'testproj', 'project_path': str(repo), 'cold_review_required': True}


class Fake:
    """A transform that returns / raises the queued replies in order."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        r = self.replies.pop(0) if self.replies else 'not json'
        if isinstance(r, BaseException):
            raise r
        return r


def _branch(sid):
    return w.branch_name(sid)


# ── (cold record required) ──────────────────────────────────────────────────

def test_hand_recorded_pass_does_not_satisfy_cold_requirement(project):
    _, tip = _agent(project, 'h1')
    gate.record(project, _branch('h1'), sha=tip, verdict='pass', reviewer_session='fenn')
    assert gate.hold_reason('testproj', _branch('h1'), tip) == ''            # plain gate: ok
    reason = gate.hold_reason('testproj', _branch('h1'), tip, require_cold=True)
    assert 'no cold review on record' in reason
    assert w.merge_back(project, 'h1')[0] == 'awaiting_review'               # via merge_back


def test_cold_pass_satisfies_and_lands(project, repo):
    _, tip = _agent(project, 'h2')
    gate.record(project, _branch('h2'), sha=tip, verdict='pass', reviewer_session='',
                cold=True, attribution='server_oneshot')
    assert cg.hold_reason(project, _branch('h2'), tip) == ''
    assert w.merge_back(project, 'h2')[0] == 'clean'
    assert 'AGENT' in (repo / 'app.py').read_text(encoding='utf-8')


def test_cold_requirement_gates_even_when_merge_requires_review_is_off(project):
    assert gate.enabled(project) is False
    _agent(project, 'h3')
    status, detail = w.merge_back(project, 'h3')
    assert status == 'awaiting_review' and 'no review on record' in detail


def test_setting_off_is_the_old_behaviour(project, repo):
    project.pop('cold_review_required')
    _agent(project, 'h4')
    assert w.merge_back(project, 'h4')[0] == 'clean'
    assert runner.needs_review(project, _branch('h4')) is False


def test_cold_pass_withdrawn_by_a_later_hand_pass_is_not_cold(project):
    # Latest record for a branch+SHA wins, so a hand-recorded pass replaces the cold one.
    _, tip = _agent(project, 'h5')
    gate.record(project, _branch('h5'), sha=tip, verdict='pass', cold=True)
    gate.record(project, _branch('h5'), sha=tip, verdict='pass', reviewer_session='fenn')
    assert 'no cold review' in cg.hold_reason(project, _branch('h5'), tip)


def test_record_route_cannot_mint_a_cold_record(project, monkeypatch):
    _, tip = _agent(project, 'h6')
    monkeypatch.setattr(project_routes, 'load_project', lambda pid: project)
    monkeypatch.setitem(agent_sessions, 'fenn', {'session_id': 'fenn'})
    monkeypatch.setattr(rr.caller_attribution, 'attribute_caller',
                        lambda *a, **k: ca.Attribution(ca.ATTRIBUTED, 'fenn', detail='t'))
    app = Flask(__name__)
    app.register_blueprint(rr.bp)
    with app.test_client() as c:
        r = c.post('/api/project/testproj/agent/h6/review',
                   json={'sha': tip, 'verdict': 'pass', 'cold': True,
                         'reviewer_session': '', 'attribution': 'server_oneshot'})
    assert r.status_code == 200, r.get_json()
    assert 'cold' not in r.get_json()['record']
    assert 'no cold review' in cg.hold_reason(project, _branch('h6'), tip)


def test_no_new_http_route(project):
    assert sorted(r.rule for r in _blueprint_rules()) == ['/api/project/<project_id>/agent/<session_id>/review']


def _blueprint_rules():
    app = Flask(__name__)
    app.register_blueprint(rr.bp)
    return [r for r in app.url_map.iter_rules() if r.endpoint != 'static']


# ── 1. reviewer = toolless one-shot, cold input only ───────────────────────

def test_review_records_a_cold_pass_and_lands(project, repo):
    _, tip = _agent(project, 'r1')
    fake = Fake(PASS_REPLY)
    out = runner.review_tip(project, _branch('r1'), 'Make one() return AGENT', transform=fake, model='')
    assert out == {'status': 'recorded', 'verdict': 'pass', 'attempts': 1}
    rec = gate.find('testproj', _branch('r1'), tip)
    assert rec and rec['cold'] is True and rec['reviewer_session'] == ''
    assert rec['attribution'] == 'server_oneshot' and rec['summary'] == 'fine'
    assert w.merge_back(project, 'r1')[0] == 'clean'


def test_reviewer_receives_only_the_prompt_built_from_ask_and_diff(project, monkeypatch):
    _, tip = _agent(project, 'r2')
    monkeypatch.setitem(agent_sessions, 'r2', {'log_lines': ['SECRET-TRANSCRIPT'],
                                               'final': 'SECRET-FINAL'})
    fake = Fake(PASS_REPLY)
    runner.review_tip(project, _branch('r2'), 'the ask', transform=fake, model='')
    assert set(fake.calls[0]) == {'prompt', 'model', 'timeout'}
    prompt = fake.calls[0]['prompt']
    assert 'the ask' in prompt and '+    return "AGENT"' in prompt and tip in prompt
    assert 'SECRET' not in prompt


def test_changes_requested_is_recorded_holds_and_is_not_reasked(project):
    _agent(project, 'r3')
    fake = Fake(CHANGES_REPLY, PASS_REPLY)
    first = runner.review_tip(project, _branch('r3'), 'ask', transform=fake, model='')
    assert first['verdict'] == 'changes_requested'
    again = runner.review_tip(project, _branch('r3'), 'ask', transform=fake, model='')
    assert again['status'] == 'recorded' and len(fake.calls) == 1
    status, detail = w.merge_back(project, 'r3')
    assert status == 'awaiting_review' and 'requested changes' in detail


def test_default_transform_goes_through_the_authorized_text_seam(monkeypatch):
    import mc.agent_runtime as ar
    seen = {}
    monkeypatch.setattr(ar, 'claude_oneshot_available', lambda: True)
    monkeypatch.setattr(ar, 'run_text_transform',
                        lambda provider, **kw: seen.update(provider=provider, **kw) or 'x')
    assert runner._default_transform(prompt='p', model='m', timeout=7) == 'x'
    assert seen == {'provider': 'claude', 'prompt': 'p', 'model': 'm', 'timeout': 7}
    monkeypatch.setattr(ar, 'claude_oneshot_available', lambda: False)
    with pytest.raises(RuntimeError, match='claude_unavailable'):
        runner._default_transform(prompt='p', model='m', timeout=7)


# ── 3. hold: one retry, cap 2 per tip SHA, state visible ───────────────────

def test_bad_reply_is_retried_once_then_recorded(project):
    _agent(project, 'a1')
    fake = Fake('I think it is fine', PASS_REPLY)
    out = runner.review_tip(project, _branch('a1'), 'ask', transform=fake, model='')
    assert out == {'status': 'recorded', 'verdict': 'pass', 'attempts': 2}
    assert len(fake.calls) == 2


def test_two_failures_hold_for_a_human_with_visible_state(project):
    _, tip = _agent(project, 'a2')
    fake = Fake('nope', TimeoutError('slow'), PASS_REPLY)
    out = runner.review_tip(project, _branch('a2'), 'ask', transform=fake, model='')
    assert out['status'] == 'held' and out['attempts'] == 2 and out['error'] == 'model_timeout'
    assert len(fake.calls) == 2
    # Third call to review_tip: attempts spent, no model call.
    assert runner.review_tip(project, _branch('a2'), 'ask', transform=fake, model='')['status'] == 'held'
    assert len(fake.calls) == 2
    # Visible in the merge state, not only a log line.
    status, detail = w.merge_back(project, 'a2')
    assert status == 'awaiting_review'
    assert 'cold review attempt 2/2' in detail and 'model_timeout' in detail
    assert 'needs a human' in detail and len(detail) < 160
    assert cg.attempts('testproj', _branch('a2'), tip)['count'] == 2


@pytest.mark.parametrize('failure, code', [
    (RuntimeError('boom'), 'model_error:RuntimeError'),
    (RuntimeError('claude_unavailable'), 'claude_unavailable'),
    (TimeoutError('x'), 'model_timeout'),
    ('', 'reply:empty_reply'),
    ('{"verdict": "approve"}', 'reply:unknown_verdict'),
])
def test_every_failure_kind_holds_with_a_code(project, failure, code):
    _agent(project, 'a3')
    out = runner.review_tip(project, _branch('a3'), 'ask', transform=Fake(failure, failure), model='')
    assert out['status'] == 'held' and out['error'] == code


def test_missing_ask_holds_without_a_model_call(project):
    _agent(project, 'a4')
    fake = Fake(PASS_REPLY)
    out = runner.review_tip(project, _branch('a4'), '   ', transform=fake, model='')
    assert out['status'] == 'held' and out['error'] == 'packet:missing_ask' and not fake.calls


def test_new_commit_starts_a_fresh_attempt_count(project):
    path, tip = _agent(project, 'a5')
    runner.review_tip(project, _branch('a5'), 'ask', transform=Fake('x', 'x'), model='')
    assert cg.exhausted('testproj', _branch('a5'), tip)
    new_tip = _commit(path, 'def one():\n    return "AGENT2"\n')
    fake = Fake(PASS_REPLY)
    assert runner.review_tip(project, _branch('a5'), 'ask', transform=fake, model='')['status'] == 'recorded'
    assert cg.attempts('testproj', _branch('a5'), new_tip)['count'] == 1


def test_attempts_live_in_the_same_store_and_survive_a_record_write(project):
    _, tip = _agent(project, 'a6')
    cg.begin_attempt('testproj', _branch('a6'), tip)
    gate.record(project, _branch('a6'), sha=tip, verdict='pass', reviewer_session='fenn')
    assert cg.attempts('testproj', _branch('a6'), tip)['count'] == 1
    path = gate.store_path('testproj')
    assert sorted(json.loads(path.read_text(encoding='utf-8'))) == ['cold_attempts', 'records', 'version']


def test_runner_never_raises(project, monkeypatch):
    _agent(project, 'a7')
    monkeypatch.setattr(cg, 'begin_attempt', lambda *a: (_ for _ in ()).throw(OSError('disk')))
    out = runner.review_tip(project, _branch('a7'), 'ask', transform=Fake(PASS_REPLY), model='')
    assert out['status'] == 'held' and out['error'] == 'internal:OSError'


# ── 2. timing: worker thread, merge-back runs after the review ─────────────

def test_start_reviews_on_a_worker_thread_then_calls_then(project):
    _agent(project, 't1')
    main = threading.current_thread()
    order = []

    def transform(**kw):
        order.append(('review', threading.current_thread()))
        return PASS_REPLY

    t = runner.start(project, _branch('t1'), 'ask',
                     lambda: order.append(('then', threading.current_thread())),
                     transform=transform, model='')
    assert t is not None
    t.join(30)
    assert [o[0] for o in order] == ['review', 'then']
    assert all(o[1] is not main for o in order) and order[0][1] is order[1][1]


def test_then_runs_even_when_the_review_fails_and_merge_back_holds(project):
    _agent(project, 't2')
    result = []
    t = runner.start(project, _branch('t2'), 'ask', lambda: result.append(w.merge_back(project, 't2')),
                     transform=Fake(TimeoutError(), TimeoutError()), model='')
    t.join(30)
    assert result and result[0][0] == 'awaiting_review'


def test_concurrent_start_for_the_same_branch_is_deduplicated(project):
    _agent(project, 't3')
    gate_open = threading.Event()
    calls = []

    def slow(**kw):
        gate_open.wait(10)
        return PASS_REPLY

    t1 = runner.start(project, _branch('t3'), 'ask', lambda: calls.append(1), transform=slow, model='')
    t2 = runner.start(project, _branch('t3'), 'ask', lambda: calls.append(2), transform=slow, model='')
    assert t1 is not None and t2 is None
    gate_open.set()
    t1.join(30)
    assert calls == [1]
    # The slot is released afterwards.
    t3 = runner.start(project, _branch('t3'), 'ask', lambda: None, transform=slow, model='')
    assert t3 is not None
    t3.join(30)


def test_no_commits_means_no_review(project):
    w.create(project, 't4')
    assert runner.needs_review(project, _branch('t4')) is False
    _agent(project, 't5')
    assert runner.needs_review(project, _branch('t5')) is True


# ── session-end handler wiring ─────────────────────────────────────────────

@pytest.fixture
def routes(monkeypatch, project):
    from mc.blueprints import agent_routes as ar
    monkeypatch.setattr(ar, 'load_project', lambda pid: project)
    monkeypatch.setattr(ar, '_log_agent_activity', lambda *a, **k: None)
    return ar


def _session(sid):
    return {'session_id': sid, 'project_id': 'testproj', '_worktree_isolated': True,
            'task': 'Make one() return AGENT'}


def test_session_end_defers_to_the_worker_and_does_not_merge_inline(routes, project, monkeypatch):
    _agent(project, 'e1')
    started, merged = [], []
    monkeypatch.setattr(routes._cold_review_runner, 'start',
                        lambda p, branch, ask, then, **kw: started.append((branch, ask, then)))
    monkeypatch.setattr(routes._agent_worktree, 'merge_back',
                        lambda p, sid: merged.append(sid) or ('nothing', 'x'))
    routes._worktree_merge_back_on_end(_session('e1'))
    assert [(b, a) for b, a, _ in started] == [(_branch('e1'), 'Make one() return AGENT')]
    assert merged == []                                   # nothing ran inline
    started[0][2]()                                       # the worker's `then`
    assert merged == ['e1'] and len(started) == 1         # merges, does not defer again


def test_session_end_is_unchanged_when_the_setting_is_off(routes, project, monkeypatch):
    project.pop('cold_review_required')
    _agent(project, 'e2')
    started = []
    monkeypatch.setattr(routes._cold_review_runner, 'start', lambda *a, **k: started.append(1))
    monkeypatch.setattr(routes._agent_worktree, 'merge_back', lambda p, sid: ('conflict', 'x'))
    routes._worktree_merge_back_on_end(_session('e2'))
    assert started == []


def test_hivemind_worker_is_never_cold_reviewed(routes, project, monkeypatch):
    started = []
    monkeypatch.setattr(routes._cold_review_runner, 'start', lambda *a, **k: started.append(1))
    monkeypatch.setattr(routes._agent_worktree, 'is_hivemind_worker', lambda sid: True)
    monkeypatch.setattr(routes._agent_worktree, 'merge_back', lambda p, sid: ('skipped', 'x'))
    routes._worktree_merge_back_on_end(_session('e3'))
    assert started == []


def test_end_to_end_session_end_to_landing(routes, project, repo, monkeypatch):
    _agent(project, 'e4')
    fake = Fake(PASS_REPLY)
    real_start = runner.start
    threads = []
    monkeypatch.setattr(routes._cold_review_runner, 'start',
                        lambda *a, **k: threads.append(real_start(*a, transform=fake, model='', **k)) or threads[-1])
    routes._worktree_merge_back_on_end(_session('e4'))
    threads[0].join(60)
    assert len(fake.calls) == 1
    assert 'AGENT' in (repo / 'app.py').read_text(encoding='utf-8')   # landed after the review
    assert not w.worktree_path(project, 'e4').exists()                # and was cleaned up


def test_cold_review_module_imports_nothing_from_session_state():
    # Cold by construction: the runner has no access path to a builder's transcript.
    import inspect
    src = inspect.getsource(runner)
    for banned in ('agent_sessions', 'log_lines', 'agent_log', '_journal'):
        assert banned not in src.replace('docs/_journal', ''), banned
    assert cold_review.PACKET_KEYS == ('ask', 'base_sha', 'tip_sha', 'diffstat', 'diff')


@pytest.mark.parametrize('first', ['mc.dev_harness_diff', 'mc.agent_worktree',
                                   'mc.cold_review_gate', 'mc.cold_review'])
def test_modules_import_cleanly_from_any_entry_point(first):
    """`merge-review-check.py` and the git hook import `dev_harness_diff` first;
    that chain reaches `agent_worktree` and the cold gate. A top-level import of
    `mc.cold_review` in the gate once closed a cycle there (21 hook tests broke)."""
    import subprocess, sys
    done = subprocess.run([sys.executable, '-I', '-c', f'import sys; sys.path.insert(0, {str(__import__('pathlib').Path(__file__).resolve().parents[1])!r}); import {first}'],
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr[-600:]
