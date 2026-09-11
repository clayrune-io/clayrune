"""Tests for the workflow builder runner + stores (mc/workflows.py,
mc/blueprints/workflow_routes.py). Spec: docs/WORKFLOW_BUILDER_SPEC.md,
MC-871 Revision 2, build order steps 1-3 (store, runner, validation).

R2 replaced the v1 tree-of-lists model (Paths nodes, `_next` pointers,
`current_step`) with a `nodes`+`edges` DAG (`_compile`/`format: 2`). Every
test below is EITHER a v1 test translated to the new node/edge shape with its
original intent intact, OR a new test for behaviour that only exists in the
DAG model (joins, skip-propagation, dominators, cycles). See the bottom of
this docstring for the map of what changed and why.

Determinism: patches `mc.workflows` module globals ONLY (never server.*) --
WORKFLOWS_PATH / WORKFLOW_RUNS_DIR point at tmp paths, `_dispatch_agent_internal`
is a recorder that never spawns a real agent, `_load_agent_log` is a fake in
front of a plain dict. Route-level tests go through `server.app.test_client()`
(registration + the agent-caller refusal need the real Flask routing), with
`mc.workflows`'s own globals patched the same way underneath.

WHAT CHANGED FROM v1, and why (so removals don't look accidental):
- `test_paths_without_otherwise_rejected` / `test_paths_with_empty_otherwise_is_valid`
  are REMOVED. R2-D6 retires the Paths node and makes "otherwise" an optional
  reserved edge label, not a mandatory authored key -- an unconnected outcome
  port is now a valid, deliberate terminal (never a validation error). Their
  intent survives as `test_unmatched_outcome_with_no_otherwise_edge_ends_the_branch`
  and the acceptance-mapping tests below.
- `test_paths_must_follow_an_agent_step` is REMOVED. That adjacency rule was
  intrinsic to the Paths node type, which no longer exists -- branching is now
  just conditional edges leaving any agent step, with no positional
  constraint to violate.
- Everywhere `current_step`/`last_step` were asserted, tests now assert on
  `frontier` and per-step `status` (`run['steps'][name]['status']`), since
  R2-D4 replaces the single current-step pointer with a frontier list.
- `test_unresolved_slot_fails_the_run_loudly` is split into
  `test_unknown_slot_reference_rejected_at_validation` (save-time, via the
  new R2-D5 dominator/existence check) and a direct unit test of
  `render_template`'s own check, which the dominator rule now makes
  unreachable end-to-end through `start_run` -- every node name valid enough
  to pass validation is pre-populated in `run['steps']` before any step
  dispatches, so the loud runtime failure is kept as a backstop (spec Q3
  says exactly that) rather than exercised by the CRUD/run path.
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


def _agent(name, prompt='do the thing', project_id='p1', character='', outcomes=None, x=0, y=0):
    node = {'type': 'agent', 'name': name, 'project_id': project_id,
            'character': character, 'prompt': prompt, 'x': x, 'y': y}
    if outcomes:
        node['outcomes'] = outcomes
    return node


def _approval(name, options, x=0, y=0):
    return {'type': 'approval', 'name': name, 'options': options, 'x': x, 'y': y}


def _action(name, action, config=None, x=0, y=0):
    return {'type': 'action', 'name': name, 'action': action, 'config': config or {}, 'x': x, 'y': y}


def _edge(frm, to, when=None):
    e = {'from': frm, 'to': to}
    if when is not None:
        e['when'] = when
    return e


def _doc(name, nodes, edges=None, trigger=None):
    return {'name': name, 'trigger': trigger or {'type': 'manual'},
            'nodes': nodes, 'edges': edges or []}


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
    doc = _doc('Simple', [_agent('only')])
    record = wf.m.create_workflow(doc)
    assert record['id'].startswith('wf-')
    assert record['format'] == 2
    assert [w['id'] for w in wf.m.list_workflows()] == [record['id']]

    updated = wf.m.update_workflow(record['id'], _doc('Renamed', [_agent('only')]))
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
        wf.m.create_workflow(_doc('bad', []))


# ── R2-D1: v1 -> v2 migration on read ────────────────────────────────────────

def test_v1_record_migrates_to_format2_on_read(wf):
    v1_record = {
        'id': 'wf-legacy1', 'name': 'legacy', 'description': '', 'enabled': True,
        'trigger': {'type': 'manual'},
        'steps': [
            {'type': 'agent', 'name': 'triage', 'project_id': 'p1', 'prompt': 'go'},
            {'type': 'paths', 'branches': {'worth_it': [
                {'type': 'agent', 'name': 'draft', 'project_id': 'p1', 'prompt': 'write'},
            ]}, 'otherwise': []},
        ],
        'created': 't', 'updated': 't',
    }
    wf.m.WORKFLOWS_PATH.parent.mkdir(parents=True, exist_ok=True)
    wf.m.WORKFLOWS_PATH.write_text(json.dumps([v1_record]), encoding='utf-8')

    migrated = wf.m.get_workflow('wf-legacy1')
    assert migrated['format'] == 2
    names = {n['name'] for n in migrated['nodes']}
    assert names == {'triage', 'draft'}
    triage = next(n for n in migrated['nodes'] if n['name'] == 'triage')
    assert triage['outcomes'] == ['worth_it']
    assert {'from': 'triage', 'to': 'draft', 'when': 'worth_it'} in migrated['edges']
    # Not rewritten to disk yet -- only on next save (R2-D1).
    on_disk = json.loads(wf.m.WORKFLOWS_PATH.read_text(encoding='utf-8'))
    assert 'format' not in on_disk[0]


# ── declared-vocabulary validation (R2-D6) ───────────────────────────────────

def test_unmatched_outcome_with_no_otherwise_edge_ends_the_branch(wf):
    """v1 required an explicit 'otherwise' branch key or validation failed
    (test_paths_without_otherwise_rejected, removed). R2-D6 makes this a
    non-error: an unconnected outcome port is a deliberate, visible stop, not
    a missing-config mistake."""
    doc = _doc('no-otherwise', [
        _agent('triage', outcomes=['worth_it']),
        _agent('draft'),
    ], edges=[_edge('triage', 'draft', 'worth_it')])
    assert wf.m.validate_workflow(doc) == []
    record = wf.m.create_workflow(doc)
    run = wf.m.start_run(record['id'])
    _complete(wf, run, 'triage', summary='no fenced block at all')
    run = wf.m.get_run(run['id'])
    assert run['status'] == 'completed'
    assert run['steps']['draft']['status'] == 'skipped'
    assert len(wf.dispatch.calls) == 1  # draft never dispatched


def test_edge_referencing_undeclared_outcome_rejected(wf):
    doc = _doc('bad-outcome', [
        _agent('triage', outcomes=['worth_it']),
        _agent('draft'),
    ], edges=[_edge('triage', 'draft', 'not_declared')])
    errors = wf.m.validate_workflow(doc)
    assert any('not a declared outcome' in e for e in errors)


def test_reserved_otherwise_cannot_be_declared_as_a_real_outcome(wf):
    doc = _doc('bad', [_agent('triage', outcomes=['otherwise'])])
    errors = wf.m.validate_workflow(doc)
    assert any('reserved' in e for e in errors)


def test_action_step_cannot_have_conditional_outgoing_edges(wf):
    doc = _doc('bad', [
        _action('note', 'desk_harvest'),
        _agent('after'),
    ], edges=[_edge('note', 'after', 'some_label')])
    errors = wf.m.validate_workflow(doc)
    assert any('cannot have conditional outgoing edges' in e for e in errors)


# ── handoff substitution ──────────────────────────────────────────────────────

def test_prev_output_and_named_step_slot_substitution(wf):
    doc = _doc('chain', [
        _agent('first', prompt='first prompt'),
        _agent('second', prompt='use {{prev.output}} and {{steps.first.output}}'),
    ], edges=[_edge('first', 'second')])
    record = wf.m.create_workflow(doc)
    run = wf.m.start_run(record['id'])
    assert run['frontier'] == []  # 'first' just left pending for running; 'second' not ready yet
    assert wf.dispatch.calls[0]['task'] == 'first prompt'

    _complete(wf, run, 'first', summary='FIRST OUTPUT')
    run = wf.m.get_run(run['id'])
    assert run['steps']['first']['output'] == 'FIRST OUTPUT'
    assert wf.dispatch.calls[1]['task'] == 'use FIRST OUTPUT and FIRST OUTPUT'

    _complete(wf, run, 'second', summary='SECOND OUTPUT')
    run = wf.m.get_run(run['id'])
    assert run['status'] == 'completed'


def test_unknown_slot_reference_rejected_at_validation(wf):
    """R2-D5 moves this failure class earlier than v1's runtime-only check:
    an unknown step name is caught at save, before any prompt is ever
    rendered or dispatched."""
    doc = _doc('missing-slot', [_agent('only', prompt='needs {{steps.nope.output}}')])
    errors = wf.m.validate_workflow(doc)
    assert any("unknown step 'nope'" in e for e in errors)
    with pytest.raises(ValueError):
        wf.m.create_workflow(doc)
    assert wf.dispatch.calls == []  # never actually dispatched a fabricated prompt


def test_render_template_fails_loudly_on_an_unresolved_slot(wf):
    """Unit-level: render_template's own check (spec Q3) survives as the
    runtime backstop even though validate_workflow (R2-D5) now catches any
    unknown or non-dominating reference earlier, through every real code path
    a workflow can be run from -- so this is no longer reachable end-to-end
    via start_run, only directly."""
    with pytest.raises(ValueError, match='unresolved slot'):
        wf.m.render_template('needs {{steps.nope.output}}',
                             {'steps': {}, 'prev': {}, 'trigger': {}, 'run': {}})


def test_prev_slot_rejected_at_a_join(wf):
    """R2-D5: {{prev.output}} is only valid with exactly one parent."""
    doc = _doc('ambiguous-prev', [
        _agent('a'), _agent('b'),
        _agent('join', prompt='use {{prev.output}}'),
    ], edges=[_edge('a', 'join'), _edge('b', 'join')])
    errors = wf.m.validate_workflow(doc)
    assert any('ambiguous' in e for e in errors)


# ── branching: matched outcome, otherwise fallback ───────────────────────────

def test_branch_routes_on_declared_outcome(wf):
    doc = _doc('branchy', [
        _agent('triage', outcomes=['worth_it', 'nothing']),
        _agent('draft'),
    ], edges=[_edge('triage', 'draft', 'worth_it')])
    record = wf.m.create_workflow(doc)
    run = wf.m.start_run(record['id'])
    _complete(wf, run, 'triage',
              summary='```wf:result\n{"outcome": "worth_it", "summary": "yes"}\n```')
    run = wf.m.get_run(run['id'])
    assert run['steps']['draft']['status'] == 'running'  # dispatched in the same advance
    assert run['steps']['triage']['result']['outcome'] == 'worth_it'
    assert run['steps']['triage']['chosen_when'] == 'worth_it'
    # The raw fence must not leak forward into the handoff text.
    assert 'wf:result' not in run['steps']['triage']['output']


def test_unparseable_outcome_falls_back_to_otherwise(wf):
    doc = _doc('branchy2', [
        _agent('triage', outcomes=['worth_it']),
        _agent('draft'),
    ], edges=[_edge('triage', 'draft', 'worth_it')])
    record = wf.m.create_workflow(doc)
    run = wf.m.start_run(record['id'])
    _complete(wf, run, 'triage', summary='no fenced block here at all')
    run = wf.m.get_run(run['id'])
    assert run['steps']['triage']['chosen_when'] == 'otherwise'
    assert run['steps']['draft']['status'] == 'skipped'
    assert run['status'] == 'completed'


def test_nothing_branch_ends_without_a_hack(wf):
    """Acceptance mapping (spec): 'nothing worth saying produces nothing' is
    an unconnected outcome port, not special-cased runner logic."""
    doc = _doc('nothing-branch', [
        _agent('triage', outcomes=['nothing', 'worth_it']),
        _agent('draft'),
    ], edges=[_edge('triage', 'draft', 'worth_it')])
    record = wf.m.create_workflow(doc)
    run = wf.m.start_run(record['id'])
    _complete(wf, run, 'triage',
              summary='```wf:result\n{"outcome": "nothing"}\n```')
    run = wf.m.get_run(run['id'])
    assert run['status'] == 'completed'
    assert run['steps']['draft']['status'] == 'skipped'
    assert len(wf.dispatch.calls) == 1  # 'draft' never dispatched


# ── R2-D2: join semantics + R2-D2 skip-propagation ───────────────────────────

def test_join_waits_for_all_parents_and_needs_one_completed(wf):
    doc = _doc('join', [
        _agent('a'), _agent('b'),
        _agent('join'),
    ], edges=[_edge('a', 'join'), _edge('b', 'join')])
    record = wf.m.create_workflow(doc)
    run = wf.m.start_run(record['id'])
    # Execution is serial (R2-D4): only 'a' (first in stored node order) is
    # dispatched; 'b' stays pending-but-ready, held in the frontier.
    assert run['frontier'] == ['b']
    assert run['steps']['a']['status'] == 'running'
    assert run['steps']['b']['status'] == 'pending'
    assert len(wf.dispatch.calls) == 1

    _complete(wf, run, 'a', summary='done')
    run = wf.m.get_run(run['id'])
    assert run['steps']['join']['status'] == 'pending'  # b's edge still 'unknown'
    assert run['steps']['b']['status'] == 'running'  # dispatched next
    assert len(wf.dispatch.calls) == 2

    _complete(wf, run, 'b', summary='done')
    run = wf.m.get_run(run['id'])
    # Both parents now terminal (both completed) -- join is ready and
    # dispatched in this same advance.
    assert run['steps']['join']['status'] == 'running'
    assert len(wf.dispatch.calls) == 3

    _complete(wf, run, 'join', summary='done')
    run = wf.m.get_run(run['id'])
    assert run['status'] == 'completed'


def test_skip_propagates_through_a_join(wf):
    doc = _doc('skip-join', [
        _agent('triage', outcomes=['worth_drafting', 'nothing']),
        _agent('draft'),
        _agent('log_nothing'),
        _agent('measure'),
    ], edges=[
        _edge('triage', 'draft', 'worth_drafting'),
        _edge('triage', 'log_nothing', 'nothing'),
        _edge('draft', 'measure'),
        _edge('log_nothing', 'measure'),
    ])
    record = wf.m.create_workflow(doc)
    run = wf.m.start_run(record['id'])
    _complete(wf, run, 'triage',
              summary='```wf:result\n{"outcome": "worth_drafting"}\n```')
    run = wf.m.get_run(run['id'])
    # log_nothing's only parent edge (triage --nothing-->) is dead -> skipped
    # immediately, before draft even finishes.
    assert run['steps']['log_nothing']['status'] == 'skipped'
    assert run['steps']['measure']['status'] == 'pending'  # draft parent still open

    _complete(wf, run, 'draft', summary='drafted')
    run = wf.m.get_run(run['id'])
    # measure's join is satisfied: draft completed (taken), log_nothing
    # skipped -- every parent terminal, at least one completed -- so measure
    # is dispatched in this same advance.
    assert run['steps']['measure']['status'] == 'running'

    _complete(wf, run, 'measure', summary='measured')
    run = wf.m.get_run(run['id'])
    assert run['status'] == 'completed'


def test_node_whose_parents_all_skip_is_itself_skipped(wf):
    doc = _doc('cascade-skip', [
        _agent('triage', outcomes=['worth_drafting', 'nothing']),
        _agent('draft'),
        _agent('log_nothing'),
        _agent('after_log'),
    ], edges=[
        _edge('triage', 'draft', 'worth_drafting'),
        _edge('triage', 'log_nothing', 'nothing'),
        _edge('log_nothing', 'after_log'),
    ])
    record = wf.m.create_workflow(doc)
    run = wf.m.start_run(record['id'])
    _complete(wf, run, 'triage',
              summary='```wf:result\n{"outcome": "worth_drafting"}\n```')
    run = wf.m.get_run(run['id'])
    assert run['steps']['log_nothing']['status'] == 'skipped'
    assert run['steps']['after_log']['status'] == 'skipped'  # cascaded in one pass

    _complete(wf, run, 'draft', summary='drafted')
    run = wf.m.get_run(run['id'])
    assert run['status'] == 'completed'
    assert len(wf.dispatch.calls) == 2  # triage, draft -- never log_nothing/after_log


# ── R2-D3: cycle refusal (save-time + run-time layers; the third, at-connect,
#    is a canvas concern out of scope for this backend-only pass) ───────────

def test_cycle_rejected_at_save(wf):
    doc = _doc('cyclic', [_agent('a'), _agent('b')],
              edges=[_edge('a', 'b'), _edge('b', 'a')])
    errors = wf.m.validate_workflow(doc)
    assert any('cycle' in e for e in errors)
    with pytest.raises(ValueError):
        wf.m.create_workflow(doc)


def test_cycle_rejected_at_run_time_on_a_hand_edited_store_file(wf):
    """A cycle written directly to the store file (bypassing create/update's
    validate_workflow call) must still be refused -- at run start, loudly,
    not silently hang the runner."""
    cyclic_record = {
        'id': 'wf-cyclic1', 'name': 'cyclic', 'description': '', 'enabled': True,
        'trigger': {'type': 'manual'}, 'format': 2,
        'nodes': [_agent('a'), _agent('b')],
        'edges': [_edge('a', 'b'), _edge('b', 'a')],
        'created': 't', 'updated': 't',
    }
    wf.m.WORKFLOWS_PATH.parent.mkdir(parents=True, exist_ok=True)
    wf.m.WORKFLOWS_PATH.write_text(json.dumps([cyclic_record]), encoding='utf-8')
    assert wf.m.get_workflow('wf-cyclic1') is not None  # really stored, unvalidated
    with pytest.raises(ValueError):
        wf.m.start_run('wf-cyclic1')
    assert wf.dispatch.calls == []


# ── R2-D5: dominator rule ─────────────────────────────────────────────────────

def test_dominator_rule_accepts_a_sole_ancestor(wf):
    doc = _doc('ok-dom', [
        _agent('triage'),
        _agent('draft', prompt='use {{steps.triage.output}}'),
    ], edges=[_edge('triage', 'draft')])
    assert wf.m.validate_workflow(doc) == []


def test_dominator_rule_rejects_a_non_dominating_ancestor(wf):
    """Once 'draft' gains a second parent that doesn't route through
    'triage', triage no longer dominates draft -- the reference must be
    refused, not silently allowed to resolve on some runs and not others."""
    doc = _doc('bad-dom', [
        _agent('triage'), _agent('other'),
        _agent('draft', prompt='use {{steps.triage.output}}'),
    ], edges=[_edge('triage', 'draft'), _edge('other', 'draft')])
    errors = wf.m.validate_workflow(doc)
    assert any('does not dominate' in e for e in errors)


def test_dominator_rule_rejects_self_reference(wf):
    doc = _doc('self-ref', [_agent('a', prompt='use {{steps.a.output}}')])
    errors = wf.m.validate_workflow(doc)
    assert any('cannot reference its own output' in e for e in errors)


# ── approval gate ─────────────────────────────────────────────────────────────

def test_approval_gate_parks_and_decision_routes_like_a_branch(wf):
    doc = _doc('gated', [
        _approval('gate', options=['release', 'push back']),
        _agent('publish'),
    ], edges=[_edge('gate', 'publish', 'release')])
    record = wf.m.create_workflow(doc)
    run = wf.m.start_run(record['id'])
    run = wf.m.get_run(run['id'])
    assert run['status'] == 'waiting'
    assert run['steps']['gate']['status'] == 'waiting'

    run = wf.m.resolve_decision(run['id'], 'release')
    assert run['status'] == 'running'  # publish dispatched, awaiting its own completion
    assert run['steps']['publish']['status'] == 'running'
    assert wf.dispatch.calls[-1]['project_id'] == 'p1'

    _complete(wf, run, 'publish', summary='published')
    run = wf.m.get_run(run['id'])
    assert run['status'] == 'completed'


def test_approval_gate_push_back_branch_skips_the_release_branch(wf):
    doc = _doc('gated-pb', [
        _approval('gate', options=['release', 'push back']),
        _agent('publish'),
        _agent('rework'),
    ], edges=[_edge('gate', 'publish', 'release'), _edge('gate', 'rework', 'push back')])
    record = wf.m.create_workflow(doc)
    run = wf.m.start_run(record['id'])
    run = wf.m.resolve_decision(run['id'], 'push back')
    assert run['steps']['publish']['status'] == 'skipped'
    assert run['steps']['rework']['status'] == 'running'


def test_approval_gate_rejects_choice_outside_options(wf):
    doc = _doc('gated2', [_approval('gate', options=['release'])])
    record = wf.m.create_workflow(doc)
    run = wf.m.start_run(record['id'])
    with pytest.raises(ValueError):
        wf.m.resolve_decision(run['id'], 'not-an-option')


def test_approval_options_reject_reserved_otherwise(wf):
    doc = _doc('bad-opt', [_approval('gate', options=['otherwise'])])
    errors = wf.m.validate_workflow(doc)
    assert any('reserved' in e for e in errors)


# ── one-live-run-per-workflow ─────────────────────────────────────────────────

def test_one_live_run_per_workflow(wf):
    doc = _doc('busy', [_agent('only')])
    record = wf.m.create_workflow(doc)
    wf.m.start_run(record['id'])
    with pytest.raises(RuntimeError):
        wf.m.start_run(record['id'])


def test_run_now_route_returns_409_when_busy(wf):
    doc = wf.m.create_workflow(_doc('busy2', [_agent('only')]))
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
    doc = _doc('with-action', [
        _action('note', 'backlog_create', config={'project_id': 'p1', 'text': 'hi'}),
        _agent('after'),
    ], edges=[_edge('note', 'after')])
    record = wf.m.create_workflow(doc)
    run = wf.m.start_run(record['id'])
    run = wf.m.get_run(run['id'])
    assert calls and calls[0][0] == 'POST' and calls[0][1] == '/api/project/p1/backlog'
    assert run['steps']['note']['status'] == 'completed'
    assert run['steps']['after']['status'] == 'running'  # advanced past the action, dispatched
    assert run['frontier'] == []


def test_unknown_action_rejected_at_validation(wf):
    doc = _doc('bad-action', [_action('x', 'send_email')])
    errors = wf.m.validate_workflow(doc)
    assert any('must be one of' in e for e in errors)


# ── restart adoption (fail-closed) ───────────────────────────────────────────

def test_adoption_advances_a_confirmed_completed_child(wf):
    doc = wf.m.create_workflow(_doc('adopt-ok', [_agent('first'), _agent('second')],
                                    edges=[_edge('first', 'second')]))
    run = wf.m.start_run(doc['id'])
    sid = run['steps']['first']['session_id']
    wf.agent_logs['p1'] = [{'session_id': sid, 'status': 'completed', 'summary': 'FIRST DONE'}]

    wf.m.adopt_on_startup()
    run = wf.m.get_run(run['id'])
    assert run['steps']['second']['status'] == 'running'  # dispatched in the same advance
    assert run['steps']['first']['output'] == 'FIRST DONE'


def test_adoption_marks_interrupted_when_unconfirmed(wf):
    doc = wf.m.create_workflow(_doc('adopt-bad', [_agent('only')]))
    run = wf.m.start_run(doc['id'])
    sid = run['steps']['only']['session_id']
    # Orphaned pending row: reconcile flips it to 'interrupted' at boot, which
    # means "we don't know what happened" -- NOT a confirmation of success.
    wf.agent_logs['p1'] = [{'session_id': sid, 'status': 'interrupted', 'summary': ''}]

    wf.m.adopt_on_startup()
    run = wf.m.get_run(run['id'])
    assert run['status'] == 'interrupted'


def test_adoption_leaves_waiting_runs_untouched(wf):
    doc = wf.m.create_workflow(_doc('adopt-wait', [_approval('gate', options=['go'])]))
    run = wf.m.start_run(doc['id'])
    run = wf.m.get_run(run['id'])
    assert run['status'] == 'waiting'
    wf.m.adopt_on_startup()
    run = wf.m.get_run(run['id'])
    assert run['status'] == 'waiting'  # untouched


def test_adoption_on_a_diamond_graph_advances_past_the_confirmed_root(wf):
    """Restart adoption 'on a graph', not just a linear chain: two roots feed
    a join. Only one can be `running` at restart time (R2-D4, serial
    execution) -- adoption confirms that one and lets the frontier
    recompute, which correctly still shows the other still-pending root."""
    doc = wf.m.create_workflow(_doc('adopt-diamond', [
        _agent('a'), _agent('b'), _agent('join'),
    ], edges=[_edge('a', 'join'), _edge('b', 'join')]))
    run = wf.m.start_run(doc['id'])
    running_name = next(n for n, st in run['steps'].items() if st['status'] == 'running')
    sid = run['steps'][running_name]['session_id']
    wf.agent_logs['p1'] = [{'session_id': sid, 'status': 'completed', 'summary': 'done'}]

    wf.m.adopt_on_startup()
    run = wf.m.get_run(run['id'])
    assert run['steps'][running_name]['status'] == 'completed'
    other = 'a' if running_name == 'b' else 'b'
    assert run['steps'][other]['status'] == 'running'  # dispatched by the post-adoption advance
    assert run['steps']['join']['status'] == 'pending'


# ── route-level: agent-caller refusal on CRUD + decision ─────────────────────

def test_get_workflows_open_to_everyone(wf):
    resp = wf.client.get('/api/workflows')  # no Origin header at all
    assert resp.status_code == 200


def test_create_workflow_refused_without_origin_header(wf):
    """Simulates an agent's Bash/curl call: no Origin header, no way to
    self-report around the gate (unlike agent_dispatch's source/client)."""
    resp = wf.client.post('/api/workflows', json=_doc('x', [_agent('only')]))
    assert resp.status_code == 403
    assert wf.m.list_workflows() == []  # nothing was created


def test_create_workflow_succeeds_with_origin_header(wf):
    resp = wf.client.post('/api/workflows', headers=UI_HEADERS, json=_doc('x', [_agent('only')]))
    assert resp.status_code == 200
    assert len(wf.m.list_workflows()) == 1


def test_update_and_delete_workflow_refused_without_origin_header(wf):
    doc = wf.m.create_workflow(_doc('x', [_agent('only')]))
    r1 = wf.client.put(f"/api/workflows/{doc['id']}", json={'name': 'y'})
    assert r1.status_code == 403
    r2 = wf.client.delete(f"/api/workflows/{doc['id']}")
    assert r2.status_code == 403
    assert wf.m.get_workflow(doc['id'])['name'] == 'x'  # unchanged


def test_decision_refused_without_origin_header(wf):
    doc = wf.m.create_workflow(_doc('gated', [_approval('gate', options=['go'])]))
    run = wf.m.start_run(doc['id'])
    resp = wf.client.post(f"/api/workflow-runs/{run['id']}/decision", json={'choice': 'go'})
    assert resp.status_code == 403
    run = wf.m.get_run(run['id'])
    assert run['status'] == 'waiting'  # the gate was NOT auto-answered


def test_decision_succeeds_with_origin_header(wf):
    doc = wf.m.create_workflow(_doc('gated', [_approval('gate', options=['go'])]))
    run = wf.m.start_run(doc['id'])
    resp = wf.client.post(f"/api/workflow-runs/{run['id']}/decision",
                          headers=UI_HEADERS, json={'choice': 'go'})
    assert resp.status_code == 200
    assert resp.get_json()['run']['status'] == 'completed'


def test_run_endpoint_not_gated_for_agent_callers(wf):
    """Running an already-authored, human-approved workflow is not a
    capability expansion -- only authoring/approving is gated."""
    doc = wf.m.create_workflow(_doc('runnable', [_agent('only')]))
    resp = wf.client.post(f"/api/workflows/{doc['id']}/run")  # no Origin header
    assert resp.status_code == 200
