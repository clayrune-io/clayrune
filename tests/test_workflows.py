"""Tests for the workflow builder runner + stores (mc/workflows.py,
mc/blueprints/workflow_routes.py). Spec: docs/WORKFLOW_BUILDER_SPEC.md, MC-871
Phase 1.

Determinism: patches `mc.workflows` module globals ONLY (never server.*) --
WORKFLOWS_PATH / WORKFLOW_RUNS_DIR point at tmp paths, `_dispatch_agent_internal`
is a recorder that never spawns a real agent, `_load_agent_log` is a fake in
front of a plain dict. Route-level tests go through `server.app.test_client()`
(registration + the agent-caller refusal need the real Flask routing), with
`mc.workflows`'s own globals patched the same way underneath.
"""
import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

UI_HEADERS = {'Origin': 'http://localhost:5199'}  # a real browser fetch always carries one


class _DispatchRecorder:
    """Stand-in for _dispatch_agent_internal: records calls, returns a fake
    session id per call, never spawns anything."""
    def __init__(self):
        self.calls = []
        self._n = 0

    def __call__(self, project_id, task, **kwargs):
        self._n += 1
        sid = f'sess-fake-{self._n:03d}'
        self.calls.append({'project_id': project_id, 'task': task, 'session_id': sid, **kwargs})
        return sid


def _agent_step(name, prompt='do the thing', project_id='p1', character=''):
    return {'type': 'agent', 'name': name, 'project_id': project_id,
            'character': character, 'prompt': prompt}


def _paths(branches, otherwise):
    return {'type': 'paths', 'branches': branches, 'otherwise': otherwise}


@pytest.fixture()
def wf(tmp_path, monkeypatch):
    import server  # noqa: F401  (registers the blueprint + runs wire() on import)
    from mc import workflows as m

    monkeypatch.setattr(m, 'WORKFLOWS_PATH', tmp_path / 'workflows.json')
    monkeypatch.setattr(m, 'WORKFLOW_RUNS_DIR', tmp_path / 'workflow_runs')
    dispatch = _DispatchRecorder()
    monkeypatch.setattr(m, '_dispatch_agent_internal', dispatch)
    agent_logs = {}  # project_id -> list of entries
    monkeypatch.setattr(m, '_load_agent_log', lambda pid: agent_logs.get(pid, []))

    class Ctx:
        pass
    c = Ctx()
    c.m = m
    c.dispatch = dispatch
    c.agent_logs = agent_logs
    c.client = server.app.test_client()
    server.app.config['TESTING'] = True
    return c


def _complete(wf_ctx, run, step_name, summary='done', status='idle'):
    """Fire the completion callback for the session dispatched at `step_name`."""
    sid = run['steps'][step_name]['session_id']
    pid = run['steps'][step_name]['project_id']
    wf_ctx.m.on_agent_step_complete(
        run_id=run['id'], step_name=step_name, project_id=pid,
        session_id=sid, status=status, summary=summary)


# ── store round-trip ──────────────────────────────────────────────────────────

def test_create_list_update_delete_roundtrip(wf):
    doc = {'name': 'Simple', 'steps': [_agent_step('only')]}
    record = wf.m.create_workflow(doc)
    assert record['id'].startswith('wf-')
    assert [w['id'] for w in wf.m.list_workflows()] == [record['id']]

    updated = wf.m.update_workflow(record['id'], {'name': 'Renamed',
                                                    'steps': [_agent_step('only')]})
    assert updated['name'] == 'Renamed'
    assert wf.m.get_workflow(record['id'])['name'] == 'Renamed'

    assert wf.m.delete_workflow(record['id']) is True
    assert wf.m.get_workflow(record['id']) is None
    assert wf.m.delete_workflow(record['id']) is False

    # Definitions persisted to the tmp store, not data/projects.
    saved = json.loads(wf.m.WORKFLOWS_PATH.read_text(encoding='utf-8'))
    assert saved == []


def test_create_rejects_invalid_definition(wf):
    with pytest.raises(ValueError):
        wf.m.create_workflow({'name': 'bad', 'steps': []})


# ── mandatory-otherwise rule ──────────────────────────────────────────────────

def test_paths_without_otherwise_rejected(wf):
    doc = {'name': 'no-otherwise', 'steps': [
        _agent_step('triage'),
        {'type': 'paths', 'branches': {'yes': [_agent_step('followup')]}},  # no 'otherwise' key
    ]}
    errors = wf.m.validate_workflow(doc)
    assert any('otherwise' in e for e in errors)
    with pytest.raises(ValueError):
        wf.m.create_workflow(doc)


def test_paths_with_empty_otherwise_is_valid(wf):
    # An empty otherwise list is a real DAG terminal (end here), not missing.
    doc = {'name': 'ok', 'steps': [
        _agent_step('triage'),
        _paths({'yes': [_agent_step('followup')]}, otherwise=[]),
    ]}
    assert wf.m.validate_workflow(doc) == []


def test_paths_must_follow_an_agent_step(wf):
    doc = {'name': 'bad-order', 'steps': [
        {'type': 'action', 'name': 'a1', 'action': 'desk_harvest', 'config': {}},
        _paths({'x': []}, otherwise=[]),
    ]}
    errors = wf.m.validate_workflow(doc)
    assert any('must directly follow an agent step' in e for e in errors)


# ── handoff substitution ──────────────────────────────────────────────────────

def test_prev_output_and_named_step_slot_substitution(wf):
    doc = {'name': 'chain', 'steps': [
        _agent_step('first', prompt='first prompt'),
        _agent_step('second', prompt='use {{prev.output}} and {{steps.first.output}}'),
    ]}
    record = wf.m.create_workflow(doc)
    run = wf.m.start_run(record['id'])
    assert run['current_step'] == 'first'
    assert wf.dispatch.calls[0]['task'] == 'first prompt'

    _complete(wf, run, 'first', summary='FIRST OUTPUT')
    run = wf.m.get_run(run['id'])
    assert run['current_step'] == 'second'
    assert run['steps']['first']['output'] == 'FIRST OUTPUT'
    assert wf.dispatch.calls[1]['task'] == 'use FIRST OUTPUT and FIRST OUTPUT'

    _complete(wf, run, 'second', summary='SECOND OUTPUT')
    run = wf.m.get_run(run['id'])
    assert run['status'] == 'completed'


def test_unresolved_slot_fails_the_run_loudly(wf):
    doc = {'name': 'missing-slot', 'steps': [
        _agent_step('only', prompt='needs {{steps.nope.output}}'),
    ]}
    record = wf.m.create_workflow(doc)
    run = wf.m.start_run(record['id'])
    run = wf.m.get_run(run['id'])
    assert run['status'] == 'failed'
    assert 'unresolved slot' in run['error']
    assert wf.dispatch.calls == []  # never actually dispatched a fabricated prompt


# ── branching: matched outcome, otherwise fallback ───────────────────────────

def test_branch_routes_on_declared_outcome(wf):
    doc = {'name': 'branchy', 'steps': [
        _agent_step('triage'),
        _paths(
            {'worth_it': [_agent_step('draft')], 'nothing': []},
            otherwise=[],
        ),
    ]}
    record = wf.m.create_workflow(doc)
    run = wf.m.start_run(record['id'])
    _complete(wf, run, 'triage',
              summary='```wf:result\n{"outcome": "worth_it", "summary": "yes"}\n```')
    run = wf.m.get_run(run['id'])
    assert run['current_step'] == 'draft'
    assert run['steps']['triage']['result']['outcome'] == 'worth_it'
    # The raw fence must not leak forward into the handoff text.
    assert 'wf:result' not in run['steps']['triage']['output']


def test_unparseable_outcome_falls_back_to_otherwise(wf):
    doc = {'name': 'branchy2', 'steps': [
        _agent_step('triage'),
        _paths({'worth_it': [_agent_step('draft')]}, otherwise=[]),
    ]}
    record = wf.m.create_workflow(doc)
    run = wf.m.start_run(record['id'])
    _complete(wf, run, 'triage', summary='no fenced block here at all')
    run = wf.m.get_run(run['id'])
    # otherwise=[] is an immediate terminal.
    assert run['current_step'] is None
    assert run['status'] == 'completed'


def test_nothing_branch_ends_without_a_hack(wf):
    """Acceptance mapping (spec): 'nothing worth saying produces nothing' is a
    branch to a terminal, not special-cased runner logic."""
    doc = {'name': 'nothing-branch', 'steps': [
        _agent_step('triage'),
        _paths({'nothing': [], 'worth_it': [_agent_step('draft')]}, otherwise=[]),
    ]}
    record = wf.m.create_workflow(doc)
    run = wf.m.start_run(record['id'])
    _complete(wf, run, 'triage',
              summary='```wf:result\n{"outcome": "nothing"}\n```')
    run = wf.m.get_run(run['id'])
    assert run['status'] == 'completed'
    assert len(wf.dispatch.calls) == 1  # 'draft' never dispatched


# ── approval gate ─────────────────────────────────────────────────────────────

def test_approval_gate_parks_and_decision_routes_like_paths(wf):
    doc = {'name': 'gated', 'steps': [
        {'type': 'approval', 'name': 'gate', 'options': ['release', 'push back'],
         'branches': {'release': [_agent_step('publish')], 'push back': []}},
    ]}
    record = wf.m.create_workflow(doc)
    run = wf.m.start_run(record['id'])
    run = wf.m.get_run(run['id'])
    assert run['status'] == 'waiting'
    assert run['current_step'] == 'gate'

    run = wf.m.resolve_decision(run['id'], 'release')
    assert run['status'] == 'running'
    assert run['current_step'] == 'publish'


def test_approval_gate_rejects_choice_outside_options(wf):
    doc = {'name': 'gated2', 'steps': [
        {'type': 'approval', 'name': 'gate', 'options': ['release'],
         'branches': {'release': []}},
    ]}
    record = wf.m.create_workflow(doc)
    run = wf.m.start_run(record['id'])
    with pytest.raises(ValueError):
        wf.m.resolve_decision(run['id'], 'not-an-option')


# ── one-live-run-per-workflow ─────────────────────────────────────────────────

def test_one_live_run_per_workflow(wf):
    doc = {'name': 'busy', 'steps': [_agent_step('only')]}
    record = wf.m.create_workflow(doc)
    wf.m.start_run(record['id'])
    with pytest.raises(RuntimeError):
        wf.m.start_run(record['id'])


def test_run_now_route_returns_409_when_busy(wf):
    doc = wf.m.create_workflow({'name': 'busy2', 'steps': [_agent_step('only')]})
    r1 = wf.client.post(f"/api/workflows/{doc['id']}/run")
    assert r1.status_code == 200
    r2 = wf.client.post(f"/api/workflows/{doc['id']}/run")
    assert r2.status_code == 409


# ── Clayrune action node ──────────────────────────────────────────────────────

def test_action_step_runs_synchronously_then_continues(wf, monkeypatch):
    calls = []

    def fake_http(method, path, payload=None):
        calls.append((method, path, payload))
        return {'ok': True, 'item': {'key': 'MC-99'}}

    monkeypatch.setattr(wf.m, '_http_json', fake_http)
    doc = {'name': 'with-action', 'steps': [
        {'type': 'action', 'name': 'note', 'action': 'backlog_create',
         'config': {'project_id': 'p1', 'text': 'hi'}},
        _agent_step('after'),
    ]}
    record = wf.m.create_workflow(doc)
    run = wf.m.start_run(record['id'])
    run = wf.m.get_run(run['id'])
    assert calls and calls[0][0] == 'POST' and calls[0][1] == '/api/project/p1/backlog'
    assert run['steps']['note']['status'] == 'completed'
    assert run['current_step'] == 'after'  # advanced past the action without waiting


def test_unknown_action_rejected_at_validation(wf):
    doc = {'name': 'bad-action', 'steps': [
        {'type': 'action', 'name': 'x', 'action': 'send_email', 'config': {}},
    ]}
    errors = wf.m.validate_workflow(doc)
    assert any('must be one of' in e for e in errors)


# ── restart adoption (fail-closed) ───────────────────────────────────────────

def test_adoption_advances_a_confirmed_completed_child(wf):
    doc = wf.m.create_workflow({'name': 'adopt-ok', 'steps': [
        _agent_step('first'), _agent_step('second'),
    ]})
    run = wf.m.start_run(doc['id'])
    sid = run['steps']['first']['session_id']
    wf.agent_logs['p1'] = [{'session_id': sid, 'status': 'completed', 'summary': 'FIRST DONE'}]

    wf.m.adopt_on_startup()
    run = wf.m.get_run(run['id'])
    assert run['current_step'] == 'second'
    assert run['steps']['first']['output'] == 'FIRST DONE'


def test_adoption_marks_interrupted_when_unconfirmed(wf):
    doc = wf.m.create_workflow({'name': 'adopt-bad', 'steps': [_agent_step('only')]})
    run = wf.m.start_run(doc['id'])
    sid = run['steps']['only']['session_id']
    # Orphaned pending row: reconcile flips it to 'interrupted' at boot, which
    # means "we don't know what happened" -- NOT a confirmation of success.
    wf.agent_logs['p1'] = [{'session_id': sid, 'status': 'interrupted', 'summary': ''}]

    wf.m.adopt_on_startup()
    run = wf.m.get_run(run['id'])
    assert run['status'] == 'interrupted'


def test_adoption_leaves_waiting_runs_untouched(wf):
    doc = wf.m.create_workflow({'name': 'adopt-wait', 'steps': [
        {'type': 'approval', 'name': 'gate', 'options': ['go'], 'branches': {'go': []}},
    ]})
    run = wf.m.start_run(doc['id'])
    run = wf.m.get_run(run['id'])
    assert run['status'] == 'waiting'
    wf.m.adopt_on_startup()
    run = wf.m.get_run(run['id'])
    assert run['status'] == 'waiting'  # untouched


# ── route-level: agent-caller refusal on CRUD + decision ─────────────────────

def test_get_workflows_open_to_everyone(wf):
    resp = wf.client.get('/api/workflows')  # no Origin header at all
    assert resp.status_code == 200


def test_create_workflow_refused_without_origin_header(wf):
    """Simulates an agent's Bash/curl call: no Origin header, no way to
    self-report around the gate (unlike agent_dispatch's source/client)."""
    resp = wf.client.post('/api/workflows',
                          json={'name': 'x', 'steps': [_agent_step('only')]})
    assert resp.status_code == 403
    assert wf.m.list_workflows() == []  # nothing was created


def test_create_workflow_succeeds_with_origin_header(wf):
    resp = wf.client.post('/api/workflows', headers=UI_HEADERS,
                          json={'name': 'x', 'steps': [_agent_step('only')]})
    assert resp.status_code == 200
    assert len(wf.m.list_workflows()) == 1


def test_update_and_delete_workflow_refused_without_origin_header(wf):
    doc = wf.m.create_workflow({'name': 'x', 'steps': [_agent_step('only')]})
    r1 = wf.client.put(f"/api/workflows/{doc['id']}", json={'name': 'y'})
    assert r1.status_code == 403
    r2 = wf.client.delete(f"/api/workflows/{doc['id']}")
    assert r2.status_code == 403
    assert wf.m.get_workflow(doc['id'])['name'] == 'x'  # unchanged


def test_decision_refused_without_origin_header(wf):
    doc = wf.m.create_workflow({'name': 'gated', 'steps': [
        {'type': 'approval', 'name': 'gate', 'options': ['go'], 'branches': {'go': []}},
    ]})
    run = wf.m.start_run(doc['id'])
    resp = wf.client.post(f"/api/workflow-runs/{run['id']}/decision",
                          json={'choice': 'go'})
    assert resp.status_code == 403
    run = wf.m.get_run(run['id'])
    assert run['status'] == 'waiting'  # the gate was NOT auto-answered


def test_decision_succeeds_with_origin_header(wf):
    doc = wf.m.create_workflow({'name': 'gated', 'steps': [
        {'type': 'approval', 'name': 'gate', 'options': ['go'], 'branches': {'go': []}},
    ]})
    run = wf.m.start_run(doc['id'])
    resp = wf.client.post(f"/api/workflow-runs/{run['id']}/decision",
                          headers=UI_HEADERS, json={'choice': 'go'})
    assert resp.status_code == 200
    assert resp.get_json()['run']['status'] == 'completed'


def test_run_endpoint_not_gated_for_agent_callers(wf):
    """Running an already-authored, human-approved workflow is not a
    capability expansion -- only authoring/approving is gated."""
    doc = wf.m.create_workflow({'name': 'runnable', 'steps': [_agent_step('only')]})
    resp = wf.client.post(f"/api/workflows/{doc['id']}/run")  # no Origin header
    assert resp.status_code == 200
