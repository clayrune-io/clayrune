"""HR-8b run-role projection (mc/hivemind_run_roles.py) and its route glue.

The projection is pure, so most cases feed plain dicts shaped like the state
HR-5 persists (`mc/hivemind_integration_cycle.py`). The route cases write real
manifest / workstream / integration files under a temp hivemind dir (the
integration file goes through the cycle's own digest writer) and call the
blueprint with its data-root repointed; no process, git or reviewer starts.
"""
from __future__ import annotations

import copy
import json

import pytest
from flask import Flask

from mc import hivemind_integration_cycle as cycle, hivemind_run_roles as rr

SHA = {k: c * 40 for k, c in zip('abcdef', 'abcdef')}


def manifest(roster=True, status='completed', reviewer='global:code-reviewer'):
    cfg = {'roster_version': 1, 'reviewer_character': reviewer} if roster else {}
    return {'id': 'hm_1', 'project_id': 'p', 'status': status, 'config': cfg}


def ws(n, *, attempt=1, session=True, status='completed', deps=(), character='global:builder',
       provider='codex'):
    return {'id': f'ws_{n}', 'title': f'Unit {n}', 'status': status, 'character': character,
            'dependencies': list(deps), 'assignment_attempt': attempt,
            'worktree_session_id': f'hm_w{n}' if session else None,
            'worktree_branch': f'clayrune/agent/hm_w{n}' if session else None,
            'smokes': [],
            'resolved_assignment': {'character_ref': character, 'session_id': f'hm_w{n}',
                                    'provider': provider, 'model': 'm', 'effort': 'high'}}


FIELDS = ('id', 'status', 'worktree_session_id', 'worktree_branch', 'assignment_attempt',
          'resolved_assignment', 'smokes', 'dependencies', 'title', 'description')


def snap(w):
    return {k: copy.deepcopy(w.get(k)) for k in FIELDS}


def pins(n, merged='c', pre='a', tip='b'):
    return {'project_id': 'p', 'project_generation': 7, 'hivemind_id': 'hm_1',
            'workstream_id': f'ws_{n}', 'integration_attempt': 1, 'workstream_attempt': 1,
            'pre_merge_sha': SHA[pre] , 'incoming_branch': f'clayrune/agent/hm_w{n}',
            'incoming_tip': SHA[tip], 'merged_sha': SHA[merged], 'merged_tree': SHA['d'],
            'contributing_sessions': [f'hm_w{n}']}


def review(state='pass', verdict='pass', reason='', session='rv_1'):
    result = ({'verdict': verdict, 'report_path': 'reports/r.md', 'recorded_at': 'T1',
               'attribution': 'attributed'} if verdict else None)
    return {'candidate_key': 'k', 'reviewer_session': session,
            'reviewer_character': 'global:code-reviewer',
            'resolved_assignment': {'provider': 'claude', 'model': 'opus', 'effort': 'high'},
            'state': state, 'hold_reason': reason, 'deadline_at': 123.5, 'result': result,
            'can_record': state == 'pending', 'stop_error': ''}


def merged(n, key='key', valid=True):
    return {'ws_id': f'ws_{n}', 'branch': f'clayrune/agent/hm_w{n}', 'commit': SHA['c'],
            'pre_merge_sha': SHA['a'], 'incoming_tip': SHA['b'], 'merged_tree': SHA['d'],
            'smokes_run': True, 'smokes': [{'name': 'a.mjs', 'ok': True}], 'note': '',
            'candidate_key': key, 'review': review() if valid else None}


def integ(rows, **over):
    st = {'review_cycle_version': 1, 'hivemind_id': 'hm_1', 'status': 'running', 'cursor': 0,
          'queue': [snap(w) for w in rows], 'merged': [], 'skipped': [], 'failed': None,
          'current': None, 'updated_at': 'T0', 'reason': ''}
    st.update(over)
    return st


def outcomes(p):
    return {w['id']: w['integration']['outcome'] for w in p['workstreams']}


# ── legacy runs keep their shape ────────────────────────────────────────────

def test_legacy_manifest_projects_nothing():
    rows = [ws(1, character='')]
    assert rr.project(manifest(roster=False), rows, None) is None
    assert rr.summarize(manifest(roster=False), rows, None) is None
    assert rr.project(None, rows, None) is None


# ── requested character + effective engine ─────────────────────────────────

def test_requested_character_and_effective_engine_per_workstream():
    rows = [ws(1, provider='codex'), ws(2, character='global:writer', provider='gemini')]
    rows[1]['resolved_assignment'] = None   # not yet launched
    p = rr.project(manifest(status='active'), rows, None)
    assert p['stage'] == 'workers_running' or p['stage'] == 'workers_complete'
    a, b = p['workstreams']
    assert a['requested_character'] == 'global:builder'
    assert a['effective_engine'] == {'provider': 'codex', 'model': 'm', 'effort': 'high',
                                     'session_id': 'hm_w1'}
    assert b['requested_character'] == 'global:writer'
    assert b['effective_engine'] is None
    assert p['reviewer']['requested_character'] == 'global:code-reviewer'


def test_workers_complete_without_integration_is_not_accepted():
    p = rr.project(manifest(status='completed'), [ws(1), ws(2)], None)
    assert p['stage'] == 'workers_complete'
    assert p['accepted'] is False
    assert set(outcomes(p).values()) == {'not_started'}


def test_workers_failed_and_running_stages():
    assert rr.project(manifest(), [ws(1), ws(2, status='failed')], None)['stage'] == 'workers_failed'
    assert rr.project(manifest(), [ws(1), ws(2, status='active')], None)['stage'] == 'workers_running'


# ── provisional vs accepted ────────────────────────────────────────────────

def test_review_pending_projects_provisional_not_accepted():
    rows = [ws(1), ws(2)]
    st = integ(rows, status='awaiting_review', cursor=0, current={
        'phase': 'reviewing', 'candidate': pins(1), 'merge': merged(1, valid=False),
        'reviewer': review(state='pending', verdict=None, reason='awaiting designated reviewer verdict')})
    p = rr.project(manifest(), rows, st)
    assert p['stage'] == 'review_pending' and p['accepted'] is False
    assert outcomes(p) == {'ws_1': 'provisional', 'ws_2': 'queued'}
    (r,) = p['reviews']
    assert r['state'] == 'provisional' and r['phase'] == 'reviewing'
    assert r['reviewed_commit'] == SHA['c'] and r['pre_merge_sha'] == SHA['a']
    assert r['incoming_tip'] == SHA['b'] and r['incoming_branch'] == 'clayrune/agent/hm_w1'
    assert r['checks'] == {'named': [{'name': 'a.mjs', 'ok': True}], 'ran': True, 'note': ''}
    assert r['reviewer'] == {'session_id': 'rv_1', 'character': 'global:code-reviewer',
                             'requested_character': 'global:code-reviewer', 'resolved_character': 'global:code-reviewer',
                             'substituted': False, 'substitution_step': '', 'substitution_reason': '',
                             'engine': {'provider': 'claude', 'model': 'opus', 'effort': 'high'}}
    assert r['review_state'] == 'pending' and r['verdict'] == ''
    assert r['hold_reason'] == 'awaiting designated reviewer verdict'
    assert r['deadline_at'] == 123.5
    assert p['reviewer']['count'] == 1 and p['reviewer']['session_ids'] == ['rv_1']
    assert p['counts']['provisional'] == 1 and p['counts']['accepted'] == 0


def test_smoking_phase_before_checks_exist_has_no_checks_yet():
    rows = [ws(1)]
    merge = {k: v for k, v in merged(1, valid=False).items() if k not in ('smokes', 'smokes_run', 'note')}
    st = integ(rows, current={'phase': 'smoking', 'candidate': pins(1), 'merge': merge})
    p = rr.project(manifest(status='active'), rows, st)
    assert outcomes(p) == {'ws_1': 'provisional'}
    assert p['reviews'][0]['checks'] is None and p['reviews'][0]['reviewer'] is None


def test_merging_phase_is_in_progress_not_provisional():
    rows = [ws(1)]
    st = integ(rows, current={'phase': 'merging', 'ws_id': 'ws_1'})
    p = rr.project(manifest(status='active'), rows, st)
    assert outcomes(p) == {'ws_1': 'in_progress'} and p['reviews'] == []
    assert p['stage'] == 'integrating'


def test_completed_with_exact_reviews_is_the_only_accepted():
    rows = [ws(1), ws(2)]
    st = integ(rows, status='completed', cursor=2, merged=[merged(1, 'k1'), merged(2, 'k2')])
    p = rr.project(manifest(), rows, st)
    assert p['stage'] == 'accepted' and p['accepted'] is True
    assert outcomes(p) == {'ws_1': 'accepted', 'ws_2': 'accepted'}
    assert [r['state'] for r in p['reviews']] == ['accepted', 'accepted']
    assert p['reviews'][0]['verdict'] == 'pass' and p['reviews'][0]['report_path'] == 'reports/r.md'
    assert p['reviews'][0]['candidate_key'] == 'k1'
    assert rr.summarize(manifest(), rows, st) == {
        'stage': 'accepted', 'accepted': True,
        'requested_characters': ['global:builder'], 'reviewer_character': 'global:code-reviewer'}


@pytest.mark.parametrize('breakage', ['no_review', 'review_not_pass', 'no_key', 'cursor_short',
                                      'stale_attempt', 'ws_deleted', 'unreviewed_extra_ws'])
def test_completed_without_exact_evidence_is_never_green(breakage):
    rows = [ws(1), ws(2)]
    st = integ(rows, status='completed', cursor=2, merged=[merged(1, 'k1'), merged(2, 'k2')])
    live = copy.deepcopy(rows)
    if breakage == 'no_review':
        st['merged'][1]['review'] = None
    elif breakage == 'review_not_pass':
        st['merged'][1]['review'] = review(state='changes_requested', verdict='changes_requested')
    elif breakage == 'no_key':
        st['merged'][1]['candidate_key'] = ''
    elif breakage == 'cursor_short':
        st['cursor'] = 1
    elif breakage == 'stale_attempt':
        live[1]['assignment_attempt'] = 2
    elif breakage == 'ws_deleted':
        live = live[:1]
    elif breakage == 'unreviewed_extra_ws':
        live.append(ws(3))
    p = rr.project(manifest(), live, st)
    assert p['accepted'] is False
    assert p['stage'] == 'unverified'
    assert p['unverified_reasons']


def test_stale_workstream_flagged_even_while_review_pending():
    rows = [ws(1)]
    st = integ(rows, status='awaiting_review', current={
        'phase': 'reviewing', 'candidate': pins(1), 'merge': merged(1, valid=False),
        'reviewer': review(state='pending', verdict=None)})
    live = [ws(1, attempt=2)]
    p = rr.project(manifest(), live, st)
    w = p['workstreams'][0]['integration']
    assert w['stale'] is True and w['outcome'] == 'unverified'


# ── holds and reports ──────────────────────────────────────────────────────

def test_changes_requested_hold_keeps_provisional_commit():
    rows = [ws(1), ws(2)]
    st = integ(rows, status='held', reason='review requested changes; provisional candidate held',
               current={'phase': 'reviewing', 'candidate': pins(1), 'merge': merged(1, valid=False),
                        'reviewer': review(state='changes_requested', verdict='changes_requested',
                                           reason='review requested changes; provisional candidate held')})
    p = rr.project(manifest(), rows, st)
    assert p['stage'] == 'held' and p['accepted'] is False
    assert p['hold'] == {'kind': 'changes_requested', 'ws_id': 'ws_1', 'checks': [],
                         'reason': 'review requested changes; provisional candidate held'}
    assert outcomes(p) == {'ws_1': 'provisional', 'ws_2': 'queued'}
    assert p['reviews'][0]['verdict'] == 'changes_requested'
    assert p['reviews'][0]['report_path'] == 'reports/r.md'


def test_smoke_failure_is_rolled_back_failed_not_provisional():
    rows = [ws(1), ws(2)]
    failed = {'kind': 'smoke', 'smoke': 'a.mjs', 'detail': 'boom', 'rolled_back_to': SHA['a'],
              'smokes': [{'name': 'a.mjs', 'ok': False}], 'ws_id': 'ws_1'}
    st = integ(rows, status='held', reason='candidate smoke failed', failed=failed,
               current={'phase': 'smoking', 'candidate': pins(1), 'merge': merged(1, valid=False)})
    p = rr.project(manifest(), rows, st)
    assert outcomes(p) == {'ws_1': 'failed', 'ws_2': 'queued'}
    assert p['reviews'] == []
    assert p['hold']['kind'] == 'smoke_failed' and p['hold']['ws_id'] == 'ws_1'
    assert p['hold']['checks'] == [{'name': 'a.mjs', 'ok': False}]
    assert p['counts']['failed'] == 1


@pytest.mark.parametrize('rstate,kind', [('timed_out', 'review_timed_out'),
                                         ('cancelled', 'review_cancelled'),
                                         ('held', 'review_held')])
def test_reviewer_lifecycle_holds(rstate, kind):
    rows = [ws(1)]
    st = integ(rows, status='held', reason='',
               current={'phase': 'reviewing', 'candidate': pins(1), 'merge': merged(1, valid=False),
                        'reviewer': review(state=rstate, verdict=None, reason='deadline expired')})
    p = rr.project(manifest(), rows, st)
    assert p['hold']['kind'] == kind
    assert p['hold']['reason'] == 'deadline expired'     # falls back to the reviewer's reason
    assert p['accepted'] is False


@pytest.mark.parametrize('fkind,kind', [('conflict', 'merge_conflict'), ('dirty', 'worker_dirty'),
                                        ('missing_branch', 'missing_branch'),
                                        ('standards', 'standards')])
def test_merge_refusal_kinds(fkind, kind):
    rows = [ws(1)]
    st = integ(rows, status='held', reason='candidate merge refused',
               failed={'kind': fkind, 'detail': 'x', 'ws_id': 'ws_1'},
               current={'phase': 'merging', 'ws_id': 'ws_1'})
    p = rr.project(manifest(), rows, st)
    assert p['hold']['kind'] == kind and outcomes(p) == {'ws_1': 'failed'}


def test_held_dependent_followup_exposes_dependencies_and_reason():
    rows = [ws(1), ws(2, deps=['ws_1'])]
    st = integ(rows, status='held', cursor=1, merged=[merged(1, 'k1')],
               reason='dependent code follow-up held for Dave (Q6)')
    p = rr.project(manifest(), rows, st)
    assert p['stage'] == 'held' and p['accepted'] is False
    assert outcomes(p) == {'ws_1': 'accepted', 'ws_2': 'queued'}
    assert p['workstreams'][1]['dependencies'] == ['ws_1']
    assert p['hold']['reason'].startswith('dependent code follow-up')


def test_unreadable_integration_stub_is_held_not_green():
    rows = [ws(1)]
    p = rr.project(manifest(), rows,
                   {'status': 'held', 'reason': 'integration state unreadable; reconciliation required'})
    assert p['stage'] == 'held' and p['accepted'] is False
    assert p['hold']['kind'] == 'other' and 'unreadable' in p['hold']['reason']


# ── explicit non-review states ─────────────────────────────────────────────

def test_analysis_only_run_is_explicit_and_not_accepted():
    rows = [ws(1, session=False), ws(2, session=False)]
    st = integ(rows, status='skipped', reason='non-git analysis-only run; no code review')
    p = rr.project(manifest(), rows, st)
    assert p['stage'] == 'analysis_only' and p['accepted'] is False
    assert set(outcomes(p).values()) == {'not_applicable'}


def test_completed_with_only_noop_branches_is_no_changes_not_accepted():
    rows = [ws(1), ws(2)]
    st = integ(rows, status='completed', cursor=2,
               skipped=[{'ws_id': 'ws_1', 'reason': 'x'}, {'ws_id': 'ws_2', 'reason': 'x'}])
    p = rr.project(manifest(), rows, st)
    assert p['stage'] == 'no_changes' and p['accepted'] is False
    assert outcomes(p) == {'ws_1': 'skipped', 'ws_2': 'skipped'}


def test_noop_branch_alongside_reviewed_merge_still_accepts():
    rows = [ws(1), ws(2)]
    st = integ(rows, status='completed', cursor=2, merged=[merged(1, 'k1')],
               skipped=[{'ws_id': 'ws_2', 'reason': 'x'}])
    assert rr.project(manifest(), rows, st)['stage'] == 'accepted'


def test_empty_smoke_list_stays_visible_in_checks():
    rows = [ws(1)]
    m = merged(1, 'k1')
    m.update(smokes=[], smokes_run=False, note='no smokes named; none run')
    st = integ(rows, status='completed', cursor=1, merged=[m])
    p = rr.project(manifest(), rows, st)
    assert p['reviews'][0]['checks'] == {'named': [], 'ran': False, 'note': 'no smokes named; none run'}


# ── robustness ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize('junk', ['text', 7, [], {'status': 3}, {'status': 'completed', 'queue': 'x',
                                  'merged': [None, 5], 'skipped': 'x', 'current': 'x', 'cursor': 'x'}])
def test_malformed_integration_never_raises_and_never_green(junk):
    p = rr.project(manifest(), [ws(1)], junk)
    assert p is not None and p['accepted'] is False


def test_garbage_workstreams_never_raise():
    p = rr.project(manifest(), [None, 'x', {'id': None, 'resolved_assignment': 5}], None)
    assert p is not None and p['accepted'] is False


def test_projection_fails_closed_on_internal_error(monkeypatch):
    monkeypatch.setattr(rr, '_project', lambda *a: (_ for _ in ()).throw(RuntimeError('boom')))
    p = rr.project(manifest(), [ws(1)], None)
    assert p['stage'] == 'unverified' and p['accepted'] is False and 'boom' in p['reason']


def test_long_strings_are_truncated():
    rows = [ws(1)]
    st = integ(rows, status='held', reason='r' * 5000, failed={'kind': 'git', 'ws_id': 'ws_1',
                                                              'detail': 'd' * 5000})
    p = rr.project(manifest(), rows, st)
    assert len(p['reason']) == 600 and len(p['workstreams'][0]['integration']['reason']) == 600


def test_projection_does_not_mutate_inputs():
    rows = [ws(1)]
    st = integ(rows, status='completed', cursor=1, merged=[merged(1, 'k1')])
    m, before = manifest(), (copy.deepcopy(rows), copy.deepcopy(st), copy.deepcopy(manifest()))
    rr.project(m, rows, st)
    assert (rows, st, m) == before


def test_documented_top_level_shape():
    p = rr.project(manifest(), [ws(1)], None)
    assert set(p) == {'schema_version', 'stage', 'accepted', 'manifest_status', 'as_of', 'reason',
                      'unverified_reasons', 'workstreams', 'reviewer', 'reviews', 'hold', 'counts'}
    assert set(p['workstreams'][0]) == {'id', 'title', 'status', 'dependencies', 'requested_character',
                                        'assignment_attempt', 'effective_engine', 'integration',
                                        'resolved_character', 'substituted', 'substitution_step', 'substitution_reason'}
    assert set(p['workstreams'][0]['integration']) == {'outcome', 'stale', 'candidate_key', 'reason'}
    st = integ([ws(1)], status='completed', cursor=1, merged=[merged(1, 'k1')])
    assert set(rr.project(manifest(), [ws(1)], st)['reviews'][0]) == {
        'ws_id', 'state', 'phase', 'candidate_key', 'reviewed_commit', 'pre_merge_sha',
        'incoming_branch', 'incoming_tip', 'merged_tree', 'checks', 'reviewer', 'review_state',
        'verdict', 'hold_reason', 'deadline_at', 'report_path', 'recorded_at'}


# ── runs-route helpers ─────────────────────────────────────────────────────

def test_run_roles_reviewer_is_distinct_from_worker():
    rows = [{'session_id': 'o', 'hivemind_role': 'orchestrator'},
            {'session_id': 'w1'}, {'session_id': 'w2', 'hivemind_role': 'worker'},
            {'session_id': 'r', 'hivemind_role': 'reviewer'}]
    assert rr.role_counts(rows) == {'orchestrator': 1, 'worker': 2, 'reviewer': 1}
    assert [e['session_id'] for e in rr.filter_runs(rows, 'worker')] == ['w1', 'w2']
    assert [e['session_id'] for e in rr.filter_runs(rows, 'reviewer')] == ['r']
    assert [e['session_id'] for e in rr.filter_runs(rows, 'orchestrator')] == ['o']
    assert len(rr.filter_runs(rows, '')) == 4 and len(rr.filter_runs(rows, 'bogus')) == 4


def test_annotate_run_adds_identity_without_touching_the_row():
    row = {'session_id': 'w1', 'character_ref': 'global:builder',
           'resolved_assignment': {'provider': 'codex', 'model': 'm', 'effort': 'e', 'session_id': 'w1'}}
    out = rr.annotate_run(row)
    assert out['run_role'] == 'worker' and out['requested_character'] == 'global:builder'
    assert out['effective_engine'] == {'provider': 'codex', 'model': 'm', 'effort': 'e', 'session_id': 'w1'}
    assert 'run_role' not in row
    bare = rr.annotate_run({'session_id': 'x', 'hivemind_role': 'reviewer'})
    assert bare['run_role'] == 'reviewer' and bare['effective_engine'] is None
    assert bare['requested_character'] == ''


@pytest.mark.parametrize('actual,step', [('global:standin', 'role_fallback'), ('', 'tier_fallback')])
def test_substitutions_reach_detail_summary_reviews_and_runs(actual, step):
    assignment = {'character_ref': actual, 'requested_character': 'global:missing',
                  'substituted': True, 'substitution_step': step,
                  'substitution_reason': 'missing_at_attempt',
                  'provider': 'codex', 'model': 'm', 'effort': '', 'session_id': 's'}
    row = ws(1, character='global:missing')
    row['resolved_assignment'] = assignment
    merge = merged(1)
    merge['review']['resolved_assignment'] = assignment
    st = integ([row], status='completed', cursor=1, merged=[merge])
    full = rr.project(manifest(), [row], st)
    for projected in (full['workstreams'][0], full['reviews'][0]['reviewer'],
                      rr.annotate_run({'character_ref': 'global:missing', 'resolved_assignment': assignment}),
                      *rr.summarize(manifest(), [row], st)['substitutions']):
        for key in ('requested_character', 'substituted', 'substitution_step', 'substitution_reason'):
            assert projected[key] == assignment[key]
        assert projected['resolved_character'] == actual
    # An edit for the next attempt must not relabel what this attempt requested.
    row['character'] = 'global:future-request'
    assert rr.project(manifest(), [row], st)['workstreams'][0]['requested_character'] == 'global:missing'


# ── route glue over real files ─────────────────────────────────────────────

@pytest.fixture
def hm_app(tmp_path, monkeypatch):
    from mc.blueprints import hivemind_routes as hm
    monkeypatch.setattr(hm, 'HIVEMIND_DIR', tmp_path / 'hiveminds')
    (tmp_path / 'hiveminds').mkdir()
    log: list[dict] = []
    monkeypatch.setattr(hm, '_load_agent_log', lambda pid, **_kw: list(log))
    monkeypatch.setattr(hm, '_enrich_run_entries', lambda page: page)
    app = Flask('t')
    app.register_blueprint(hm.bp)
    client = app.test_client()
    client.hm, client.log = hm, log      # type: ignore[attr-defined]
    return client


def seed(client, hid, man, rows, st=None):
    hm = client.hm
    hm._hm_ensure_dirs(hid)
    man = dict(man, id=hid)
    hm._hm_save_manifest(hid, man)
    for w in rows:
        hm._hm_save_workstream(hid, w['id'], w)
    if st is not None:
        cycle._save(hm, hid, dict(st, hivemind_id=hid))


def test_detail_route_adds_roles_for_roster_only(hm_app):
    rows = [ws(1)]
    st = integ(rows, status='awaiting_review', current={
        'phase': 'reviewing', 'candidate': pins(1), 'merge': merged(1, valid=False),
        'reviewer': review(state='pending', verdict=None)})
    seed(hm_app, 'hm_r', manifest(), rows, st)
    seed(hm_app, 'hm_l', manifest(roster=False), [ws(1, character='')], None)

    body = hm_app.get('/api/hivemind/hm_r').get_json()
    assert body['roles']['stage'] == 'review_pending' and body['roles']['accepted'] is False
    assert body['roles']['workstreams'][0]['effective_engine']['provider'] == 'codex'
    assert body['integration']['status'] == 'awaiting_review'      # existing key untouched

    legacy = hm_app.get('/api/hivemind/hm_l').get_json()
    assert 'roles' not in legacy
    assert set(legacy) == {'manifest', 'workstreams', 'recent_messages', 'decisions',
                           'open_questions', 'integration'}


def test_list_route_adds_summary_for_roster_only(hm_app):
    seed(hm_app, 'hm_r', manifest(), [ws(1)], None)
    seed(hm_app, 'hm_l', manifest(roster=False), [ws(1, character='')], None)
    by_id = {h['id']: h for h in hm_app.get('/api/hivemind/list').get_json()}
    assert by_id['hm_r']['roles'] == {'stage': 'workers_complete', 'accepted': False,
                                      'requested_characters': ['global:builder'],
                                      'reviewer_character': 'global:code-reviewer'}
    assert 'roles' not in by_id['hm_l']


def test_runs_route_reviewer_filter_counts_and_annotations(hm_app):
    seed(hm_app, 'hm_r', manifest(), [ws(1)], None)
    hm_app.log.extend([
        {'session_id': 'o', 'hivemind_id': 'hm_r', 'hivemind_role': 'orchestrator'},
        {'session_id': 'w', 'hivemind_id': 'hm_r', 'hivemind_ws_id': 'ws_1',
         'character_ref': 'global:builder',
         'resolved_assignment': {'provider': 'codex', 'model': 'm', 'effort': 'e', 'session_id': 'w'}},
        {'session_id': 'r', 'hivemind_id': 'hm_r', 'hivemind_role': 'reviewer'},
        {'session_id': 'other', 'hivemind_id': 'hm_zzz'}])
    allr = hm_app.get('/api/hivemind/hm_r/runs').get_json()
    assert allr['total'] == 3 and allr['role_counts'] == {'orchestrator': 1, 'worker': 1, 'reviewer': 1}
    assert {e['session_id']: e['run_role'] for e in allr['runs']} == {'o': 'orchestrator', 'w': 'worker', 'r': 'reviewer'}
    ids = lambda q: [e['session_id'] for e in hm_app.get(f'/api/hivemind/hm_r/runs?role={q}').get_json()['runs']]
    assert ids('worker') == ['w'] and ids('reviewer') == ['r'] and ids('orchestrator') == ['o']
    w = next(e for e in allr['runs'] if e['session_id'] == 'w')
    assert w['requested_character'] == 'global:builder' and w['effective_engine']['provider'] == 'codex'


def test_runs_route_legacy_shape_and_worker_filter_unchanged(hm_app):
    seed(hm_app, 'hm_l', manifest(roster=False), [ws(1, character='')], None)
    hm_app.log.extend([
        {'session_id': 'o', 'hivemind_id': 'hm_l', 'hivemind_role': 'orchestrator'},
        {'session_id': 'w', 'hivemind_id': 'hm_l'},
        {'session_id': 'r', 'hivemind_id': 'hm_l', 'hivemind_role': 'reviewer'}])
    body = hm_app.get('/api/hivemind/hm_l/runs?role=worker').get_json()
    assert set(body) == {'runs', 'total', 'offset', 'limit'}
    assert [e['session_id'] for e in body['runs']] == ['w', 'r']     # legacy rule: not-orchestrator
    assert all('run_role' not in e for e in body['runs'])
