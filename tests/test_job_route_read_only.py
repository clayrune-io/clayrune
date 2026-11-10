"""MC-1165 r4 reviewer finding P3: a background-job request is READ-ONLY with
respect to session state. For an isolated session whose worktree is gone, the
route used to call `_session_cwd`, which recreates the worktree (or flips the
session to `error` when it cannot) as a side effect of someone asking for a job.
It must refuse with 409 and leave the session exactly as it found it."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
from flask import Flask

import mc.agent_worktree as w
from mc.state import agent_sessions
from test_cold_review_wiring import project, routes, _session  # noqa: F401
from test_merge_review_gate import repo  # noqa: F401


def _call(routes, sid):
    app = Flask(__name__)
    with app.test_request_context(method='POST', json={'command': 'echo hi'}):
        resp = routes.agent_job_start('testproj', sid)
    body, status = resp if isinstance(resp, tuple) else (resp, resp.status_code)
    return status, body.get_json()


@pytest.fixture
def jobs(routes, monkeypatch):
    started = []
    monkeypatch.setattr(routes, '_job_log_dir', lambda: Path('unused-job-logs'))
    monkeypatch.setattr(routes._agent_jobs, 'start_job',
                        lambda **kw: started.append(kw) or {
                            'job_id': 'j1', 'pid': 1, 'proc': None,
                            'shell': 'bash', 'shell_note': ''})
    monkeypatch.setattr(routes, '_register_process', lambda *a, **k: None)
    return started


def test_job_request_for_a_gone_worktree_is_refused_and_touches_nothing(
        routes, project, repo, jobs, monkeypatch):
    gone = repo.parent / 'worktree-that-is-gone'
    session = _session('jobgone')
    session.update(status='completed', log_lines=[], _agent_cwd=str(gone))
    monkeypatch.setitem(agent_sessions, 'jobgone', session)
    created = []
    monkeypatch.setattr(w, 'create', lambda *a, **k: created.append(a) or (False, 'no'))
    before = deepcopy(session)

    status, body = _call(routes, 'jobgone')

    assert status == 409, body
    assert 'job not started' in body['error']
    assert created == [], 'a job request must never recreate a worktree'
    assert session == before, 'a job request must not change session state'
    assert not gone.exists()
    assert jobs == [], 'no job may start in a refused session'


def test_job_request_for_a_live_worktree_still_runs_in_it(routes, project, repo, jobs, monkeypatch):
    from test_merge_review_gate import _agent
    tree, _tip = _agent(project, 'joblive')
    session = _session('joblive')
    session.update(status='completed', log_lines=[], _agent_cwd=str(tree))
    monkeypatch.setitem(agent_sessions, 'joblive', session)
    before = deepcopy(session)

    status, _body = _call(routes, 'joblive')

    assert status == 200
    assert [str(j['cwd']) for j in jobs] == [str(tree)]
    assert session == before
