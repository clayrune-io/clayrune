"""MC-1082: merge-review lineage must not depend on `notify_session`.

Before: the spawner recorded at POST .../agent/dispatch was whatever the caller
typed as `notify_session`. A builder that dispatched its own reviewer without
that field (or naming some other session) left no lineage, and the reviewer
passed `mc/review_lineage.py` as independent.

Now the recorded spawner is the OS-attributed caller (`mc/dispatch_lineage.py`).
These tests drive the real `agent_dispatch` handler and the real
`_dispatch_agent_internal` (fake Popen), then the real merge-review route.
"""
from __future__ import annotations

import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mc import caller_attribution as ca, dispatch_lineage as dl  # noqa: E402
import mc.agent_worktree as w  # noqa: E402
import mc.merge_review_gate as gate  # noqa: E402
from mc.blueprints import merge_review_routes as rr, project_routes  # noqa: E402
from mc.state import agent_sessions  # noqa: E402
# Fixtures: the dispatch rig (fake Popen, real handler) and the throwaway repo.
from tests.test_dispatch_spawn_save_race import env  # noqa: E402,F401
from tests.test_merge_review_gate import (  # noqa: E402,F401
    _agent, _register, _store, project, repo)

DISPATCH = '/api/project/p1/agent/dispatch'
_REAL_POPEN = subprocess.Popen   # captured at import, before any rig fakes them
_REAL_THREAD = threading.Thread


@pytest.fixture
def dispatch(env, monkeypatch):
    """POST a dispatch. The rig fakes Popen and Thread on the global
    subprocess/threading modules, which also breaks the git calls these tests
    make, so the fakes are swapped in for the request only."""
    fakes = (subprocess.Popen, threading.Thread)
    reals = (_REAL_POPEN, _REAL_THREAD)

    def use(popen, thread):
        monkeypatch.setattr(subprocess, 'Popen', popen)
        monkeypatch.setattr(threading, 'Thread', thread)

    use(*reals)

    def post(**body):
        use(*fakes)
        try:
            return env['client'].post(DISPATCH, json=body)
        finally:
            use(*reals)
    return post


@pytest.fixture
def caller(monkeypatch):
    """Who the OS says is calling. Mutate `who` between requests."""
    who = {'status': ca.UNATTRIBUTED, 'sid': ''}
    monkeypatch.setattr(
        ca, 'attribute_caller',
        lambda *a, **k: ca.Attribution(who['status'], who['sid'], detail='test'))
    return who


def _as_session(who, sid):
    who.update(status=ca.ATTRIBUTED, sid=sid)


def _as_human(who):
    who.update(status=ca.UNATTRIBUTED, sid='')


@pytest.fixture
def review_client(project, monkeypatch):
    monkeypatch.setattr(project_routes, 'load_project',
                        lambda pid: project if pid == 'testproj' else None)
    app = Flask(__name__)
    app.register_blueprint(rr.bp)
    with app.test_client() as c:
        yield c


def _review(review_client, owner, sha):
    return review_client.post(f'/api/project/testproj/agent/{owner}/review',
                              json={'sha': sha, 'verdict': 'pass'})


# ── the three required cases ────────────────────────────────────────────────

def test_reviewer_dispatched_by_the_owner_without_notify_session_is_refused(
        project, env, dispatch, caller, review_client):
    """The bypass. The builder dispatches a reviewer and says nothing about
    notify_session; the OS still knows who called."""
    _, tip = _agent(project, 'own1')
    agent_sessions['own1'] = {'session_id': 'own1', 'project_id': 'p1'}
    _as_session(caller, 'own1')
    r = dispatch(task='review branch own1')
    assert r.status_code == 200, r.get_json()
    reviewer = r.get_json()['session_id']
    assert env['sessions'][reviewer]['_notify_session'] == ''      # no notify_session sent
    assert env['sessions'][reviewer]['_spawned_by'] == 'own1'

    _as_session(caller, reviewer)                                   # the reviewer now calls
    resp = _review(review_client, 'own1', tip)
    assert resp.status_code == 403, resp.get_json()
    assert 'descends from the branch owner' in resp.get_json()['error']
    assert gate.find('testproj', w.branch_name('own1'), tip) is None
    assert w.merge_back(project, 'own1')[0] == 'awaiting_review'


def test_reviewer_dispatched_by_an_unrelated_session_still_passes(
        project, env, dispatch, caller, review_client):
    _, tip = _agent(project, 'own2')
    agent_sessions['own2'] = {'session_id': 'own2', 'project_id': 'p1'}
    agent_sessions['dave'] = {'session_id': 'dave', 'project_id': 'p1'}
    _as_session(caller, 'dave')
    r = dispatch(task='review branch own2')
    assert r.status_code == 200, r.get_json()
    reviewer = r.get_json()['session_id']
    assert env['sessions'][reviewer]['_spawned_by'] == 'dave'

    _as_session(caller, reviewer)
    resp = _review(review_client, 'own2', tip)
    assert resp.status_code == 200, resp.get_json()
    assert w.merge_back(project, 'own2')[0] == 'clean'


def test_human_dispatch_records_no_caller(env, caller):
    _as_human(caller)
    r = env['client'].post(DISPATCH, json={'task': 'a chat Ron opened'},
                           headers={'Origin': 'http://localhost:5199'})
    assert r.status_code == 200, r.get_json()
    sess = env['sessions'][r.get_json()['session_id']]
    assert sess['_spawned_by'] == ''
    assert sess['_notify_session'] == ''


# ── notify_session can no longer hide or forge the lineage ──────────────────

def test_naming_another_session_in_notify_session_does_not_hide_the_caller(
        project, env, dispatch, caller, review_client):
    _, tip = _agent(project, 'own3')
    agent_sessions['own3'] = {'session_id': 'own3', 'project_id': 'p1'}
    agent_sessions['decoy'] = {'session_id': 'decoy', 'project_id': 'p1'}
    _as_session(caller, 'own3')
    r = dispatch(task='review own3', notify_session='decoy')
    reviewer = r.get_json()['session_id']
    sess = env['sessions'][reviewer]
    assert sess['_spawned_by'] == 'own3'            # lineage: the real caller
    assert sess['_notify_session'] == 'decoy'       # callback target: unchanged

    _as_session(caller, reviewer)
    assert _review(review_client, 'own3', tip).status_code == 403


def test_unattributed_dispatch_naming_notify_session_keeps_todays_behaviour(env, caller):
    _as_human(caller)
    r = env['client'].post(DISPATCH, json={'task': 't', 'notify_session': 'someone'})
    sess = env['sessions'][r.get_json()['session_id']]
    assert sess['_spawned_by'] == 'someone'
    assert sess['_notify_session'] == 'someone'


def test_attributed_caller_that_is_also_notify_session_is_unchanged(env, caller):
    agent_sessions['dave'] = {'session_id': 'dave', 'project_id': 'p1'}
    _as_session(caller, 'dave')
    r = env['client'].post(DISPATCH, json={'task': 't', 'notify_session': 'dave'})
    sess = env['sessions'][r.get_json()['session_id']]
    assert sess['_spawned_by'] == 'dave' and sess['_notify_session'] == 'dave'


# ── the module ──────────────────────────────────────────────────────────────

def _req(addr='127.0.0.1', port=5555):
    return SimpleNamespace(remote_addr=addr, environ={'REMOTE_PORT': port, 'SERVER_PORT': '5199'})


def test_unavailable_lookup_records_no_caller(monkeypatch):
    monkeypatch.setattr(ca, 'attribute_caller',
                        lambda *a, **k: ca.Attribution(ca.UNAVAILABLE, detail='psutil not installed'))
    assert dl.attributed_caller_session(_req(), {}, {}) == ''


def test_lookup_that_raises_records_no_caller_and_does_not_raise(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError('process table gone')
    monkeypatch.setattr(ca, 'attribute_caller', boom)
    assert dl.attributed_caller_session(_req(), {}, {}) == ''


def test_attributed_lookup_returns_the_session(monkeypatch):
    monkeypatch.setattr(ca, 'attribute_caller',
                        lambda *a, **k: ca.Attribution(ca.ATTRIBUTED, ' s1 ', 4000, 'x'))
    assert dl.attributed_caller_session(_req(), {}, {}) == 's1'


def test_spawner_of_record_prefers_the_caller_and_falls_back_to_none():
    assert dl.spawner_of_record('own', '') == 'own'
    assert dl.spawner_of_record('own', 'decoy') == 'own'
    assert dl.spawner_of_record('', 'decoy') is None      # -> notify_session in the dispatcher
    assert dl.spawner_of_record('', '') is None
