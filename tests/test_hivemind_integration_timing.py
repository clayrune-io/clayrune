"""MC-1179 OS33: reviewed integration must wait on ordinary timing, and a hold
must have a release.

Real temp git and the real HR-3/HR-4 lifecycle, fake provider processes (the
`env` fixture of test_hivemind_integration_review). No worker, reviewer or
orchestrator is started; the cycle's `advance` is called directly.
"""
import json
import time
from pathlib import Path

import pytest

from mc import hivemind_integration as git, hivemind_integration_cycle as cycle
from mc import hivemind_integration_wait as wait
from mc import hivemind_reviewer as reviewer, state
from mc.blueprints import hivemind_routes as hm
from mc.landing_serialization import LandingBusy
from mc.merge_review_gate import ReviewRefused
from tests.test_hivemind_integration_review import (  # noqa: F401  (env is a fixture)
    assert_outside_unchanged, cmd, env, submit)
from tests.test_hivemind_roster import agents_dir  # noqa: F401
from tests.test_hivemind_roster_worker import rig  # noqa: F401
from tests.test_hivemind_routes import HUMAN, client  # noqa: F401

URL = '/api/hivemind/roster1/integration/release'


def _worker_in_turn(status='running'):
    state.agent_sessions['hm_worker_b'] = {'session_id': 'hm_worker_b', 'status': status}


def _rewind_wait(minutes):
    """Pretend the wait began `minutes` ago (the state file is the clock)."""
    path = hm._hm_dir('roster1') / 'integration.json'
    st = cycle.read(hm, 'roster1')
    st['waiting_since'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(time.time() - minutes * 60))
    cycle._save(hm, 'roster1', st)
    assert path.exists()


# ── (a) the last worker is still in its turn ─────────────────────────────────

def test_worker_still_in_its_turn_waits_then_integrates(env):
    r = env
    _worker_in_turn('running')
    st = r.tick()
    assert st['status'] == 'running' and st.get('reason') is None   # not held
    assert not Path(hm._hm_dir('roster1') / 'integration.json').read_text().count('"held"')
    assert 'merge ws1' not in r.events
    _worker_in_turn('completed')
    st = r.tick()
    assert st['status'] == 'awaiting_review' and 'waiting_since' not in st
    assert 'merge ws1' in r.events
    assert_outside_unchanged(r)


def test_wait_is_bounded_then_holds(env):
    r = env
    _worker_in_turn('running')
    assert r.tick()['status'] == 'running'
    _rewind_wait(wait.GRACE_SECONDS // 60 + 5)
    st = r.tick()
    assert st['status'] == 'held' and 'still active' in st['reason']
    assert r.runtimes['codex'].calls == []


def test_live_process_on_a_completed_row_also_waits(env):
    class Alive:
        def poll(self):
            return None
    state.agent_sessions['hm_worker_a'] = {'session_id': 'hm_worker_a', 'status': 'completed',
                                           'proc': Alive()}
    assert env.tick()['status'] == 'running'


def test_a_running_native_child_waits_then_holds(env):
    # Was test_merge_smoke_standards_and_native_failures_hold[native]: a running
    # native child held at once; it is a worker still in its turn, so it waits.
    state.agent_sessions['child'] = {'_spawned_by': 'hm_worker_a', 'status': 'running'}
    assert env.tick()['status'] == 'running'
    _rewind_wait(wait.GRACE_SECONDS // 60 + 5)
    assert env.tick()['status'] == 'held'


@pytest.mark.parametrize('status', ['error', 'blocked', 'stopped'])
def test_a_session_that_ended_badly_still_holds(env, status):
    _worker_in_turn(status)
    assert env.tick()['status'] == 'held'


def test_running_child_with_a_candidate_in_flight_still_holds(env):
    r = env
    r.tick()
    state.agent_sessions['child'] = {'_spawned_by': 'hm_worker_a', 'status': 'running'}
    assert r.tick()['status'] == 'held'


# ── (b) the landing lock is busy ─────────────────────────────────────────────

def test_busy_landing_lock_waits_and_is_not_a_standards_failure(env, monkeypatch):
    r = env
    marks = []
    monkeypatch.setattr(cycle, 'mark_hold', lambda *a: marks.append(a))
    real = cycle.landing
    busy = [True]

    def landing(*args, **kwargs):
        if busy[0]:
            raise LandingBusy('landing_busy')
        return real(*args, **kwargs)

    monkeypatch.setattr(cycle, 'landing', landing)
    st = r.tick()
    assert st['status'] == 'running' and not marks
    busy[0] = False
    assert r.tick()['status'] == 'awaiting_review'


def test_busy_landing_lock_gives_up_after_the_grace_period(env, monkeypatch):
    r = env
    monkeypatch.setattr(cycle, 'landing', lambda *a, **k: (_ for _ in ()).throw(LandingBusy('landing_busy')))
    assert r.tick()['status'] == 'running'
    _rewind_wait(wait.GRACE_SECONDS // 60 + 5)
    st = r.tick()
    assert st['status'] == 'held' and 'landing' in st['reason']


# ── (d) the synthesis rewrites the manifest while a candidate is smoked ──────

def test_manifest_timestamp_change_during_smoke_does_not_hold_the_launch(env, monkeypatch):
    r = env
    smoke = git.smoke_candidate

    def smoke_then_synthesis(*args):
        manifest = hm._hm_load_manifest('roster1')
        manifest['updated_at'] = '2099-01-01T00:00:00Z'      # what the synthesis notify route does
        hm._hm_save_manifest('roster1', manifest)
        return smoke(*args)

    monkeypatch.setattr(git, 'smoke_candidate', smoke_then_synthesis)
    st = r.tick()
    assert st['status'] == 'awaiting_review', st.get('reason')
    assert len(r.runtimes['codex'].calls) == 1


def test_launch_refuses_a_real_manifest_change_but_not_a_timestamp(env):
    r = env
    manifest = hm._hm_load_manifest('roster1')
    project = r.client.projects['thm']
    cand = cycle.Candidate('thm', manifest['project_generation'], 'roster1', 'ws1', 1, 1,
                           r.base, 'b', r.base, r.base, r.base, ('hm_worker_a',))
    changed = dict(manifest, config=dict(manifest['config'], worker_max_turns=99))
    with pytest.raises(ReviewRefused, match='stale reviewer launch inputs'):
        reviewer.launch_reviewer(hm, changed, project, cand, review_cwd='.', verdict_url='x')
    assert reviewer._same_launch_inputs(dict(manifest, updated_at='later'), manifest)
    assert not reviewer._same_launch_inputs(changed, manifest)


# ── release ──────────────────────────────────────────────────────────────────

def _hold(r, monkeypatch):
    monkeypatch.setattr(git, '_run_smoke', lambda *a: (False, 'broken test'))
    st = r.tick()
    assert st['status'] == 'held'
    return st


def test_release_archives_the_held_attempt_and_integration_runs_again(env, monkeypatch):
    r = env
    held = _hold(r, monkeypatch)
    wt = Path(held['worktree'])
    assert wt.exists() and cmd(r.repo, 'branch', '--list', held['branch'])
    monkeypatch.setattr(hm, '_hm_start_integration', lambda hid: True)   # no driver thread in a test
    resp = r.client.post(URL, json={}, headers=HUMAN)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body['ok'] and body['released_status'] == 'held'
    assert not (hm._hm_dir('roster1') / 'integration.json').exists()
    assert not wt.exists() and Path(body['archived_worktree']).exists()
    assert not cmd(r.repo, 'branch', '--list', held['branch'])
    assert cmd(r.repo, 'branch', '--list', body['archived_branch'])
    assert (Path(body['archive_dir']) / 'integration.json').exists()
    assert json.loads((Path(body['archive_dir']) / 'release.json').read_text())['from_status'] == 'held'
    # The run can be integrated again: a fresh attempt, not the held one.
    monkeypatch.setattr(git, '_run_smoke', lambda wt_, name: r.events.append('smoke ' + name) or (True, ''))
    st = r.tick()
    assert st['status'] == 'awaiting_review' and st['branch'] == held['branch']
    assert 'smoke a.mjs' in r.events
    assert_outside_unchanged(r)


def test_release_is_human_only(env, monkeypatch):
    r = env
    _hold(r, monkeypatch)
    resp = r.client.post(URL, json={})             # an agent's curl sends no Origin
    assert resp.status_code == 403 and resp.get_json()['error'] == 'human_only'
    assert (hm._hm_dir('roster1') / 'integration.json').exists()


def test_release_refuses_an_attempt_that_is_not_held(env):
    r = env
    st = r.tick()
    assert st['status'] == 'awaiting_review'
    resp = r.client.post(URL, json={}, headers=HUMAN)
    assert resp.status_code == 409 and 'held' in resp.get_json()['error']
    assert (hm._hm_dir('roster1') / 'integration.json').exists()


def test_release_with_no_attempt_is_404(env):
    resp = env.client.post(URL, json={}, headers=HUMAN)
    assert resp.status_code == 404 and 'no integration attempt' in resp.get_json()['error']


def test_release_refuses_while_the_reviewer_is_not_cleaned_up(env, monkeypatch):
    r = env
    held = _hold(r, monkeypatch)
    held['current'] = {'reviewer_cleanup_error': 'could not stop'}
    cycle._save(hm, 'roster1', held)
    resp = r.client.post(URL, json={}, headers=HUMAN)
    assert resp.status_code == 409 and 'reviewer' in resp.get_json()['error']
    assert (hm._hm_dir('roster1') / 'integration.json').exists()


def test_release_leaves_a_legacy_run_alone(env):
    r = env
    r.manifest['config'].pop('roster_version')
    hm._hm_save_manifest('roster1', r.manifest)
    assert r.client.post(URL, json={}, headers=HUMAN).status_code == 409
