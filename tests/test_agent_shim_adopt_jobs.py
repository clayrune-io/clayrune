"""MC-1161: a session that owns an open Clayrune job is never adoptable.

`agent_jobs._jobs` is in-memory: the job's subprocess handle and the completion
callback that wakes its session die with the server, and shim adoption does not
carry them. So with agent_survive_restart_enabled on, an idle session parked on
a job used to be listed adoptable (`_holds_work` counted jobs) and spared, while
its job was orphaned. It must stay in `active_sessions` (blocking) instead.

Run:  .venv/Scripts/python.exe -m pytest tests/test_agent_shim_adopt_jobs.py -q
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from mc import agent_jobs as aj  # noqa: E402
from mc import agent_shim_adopt as A  # noqa: E402

from tests.test_agent_shim_adopt import (  # noqa: E402,F401
    ON, OFF, _clean_registry, _live_session, tracker)


@pytest.fixture
def jobs(monkeypatch):
    table = {}
    monkeypatch.setattr(aj, '_jobs', table)
    return table


def _open_job(table, sid, job_id='j1', status='running'):
    table[job_id] = {'job_id': job_id, 'session_id': sid, 'project_id': 'p',
                     'command': 'pytest -q', 'status': status, 'started_at': time.time()}


def _blockers(*sids):
    return {'active_sessions': [{'session_id': s, 'project_id': 'p'} for s in sids],
            'active_hiveminds': []}


def test_idle_session_with_open_job_is_not_adoptable_and_stays_blocking(tracker, tmp_path, jobs):
    # An idle session never appears in `active_sessions` from the base payload;
    # the partition's second loop used to promote it to adoptable via _holds_work.
    s = _live_session(tracker, tmp_path, 'jidle', status='idle')
    _open_job(jobs, 'jidle')
    assert A._holds_work(s) is True                       # the job is still "work"...
    assert A.is_adoptable_session(s, cfg=ON) is False     # ...but never adoptable
    assert A.should_spare(s, cfg=ON) is False
    out = A.partition_blockers(_blockers(), {'jidle': s}, cfg=ON)
    assert out['adoptable_sessions'] == []


def test_running_session_with_open_job_stays_in_active_sessions(tracker, tmp_path, jobs):
    s = _live_session(tracker, tmp_path, 'jrun', status='running')
    assert A.is_adoptable_session(s, cfg=ON) is True      # control: adoptable without the job
    _open_job(jobs, 'jrun')
    assert A.is_adoptable_session(s, cfg=ON) is False
    assert A.should_spare(s, cfg=ON) is False
    out = A.partition_blockers(_blockers('jrun'), {'jrun': s}, cfg=ON)
    assert [r['session_id'] for r in out['active_sessions']] == ['jrun']
    assert out['adoptable_sessions'] == []


def test_job_on_another_session_does_not_block_this_one(tracker, tmp_path, jobs):
    s = _live_session(tracker, tmp_path, 'jmine', status='running')
    _open_job(jobs, 'someone_else')
    assert A.is_adoptable_session(s, cfg=ON) is True
    out = A.partition_blockers(_blockers('jmine'), {'jmine': s}, cfg=ON)
    assert [r['session_id'] for r in out['adoptable_sessions']] == ['jmine']


def test_finished_job_does_not_block_adoption(tracker, tmp_path, jobs):
    s = _live_session(tracker, tmp_path, 'jdone', status='running')
    _open_job(jobs, 'jdone', status='completed')
    assert A.is_adoptable_session(s, cfg=ON) is True


def test_no_job_leaves_partition_unchanged(tracker, tmp_path, jobs):
    good = _live_session(tracker, tmp_path, 'ng1')
    plain = {'session_id': 'ngp', 'status': 'running', 'project_id': 'p', 'proc': object()}
    out = A.partition_blockers(_blockers('ng1', 'ngp'), {'ng1': good, 'ngp': plain}, cfg=ON)
    assert [r['session_id'] for r in out['adoptable_sessions']] == ['ng1']
    assert [r['session_id'] for r in out['active_sessions']] == ['ngp']


def test_unreadable_job_table_fails_closed(tracker, tmp_path, jobs, monkeypatch):
    s = _live_session(tracker, tmp_path, 'jerr', status='running')

    def boom(_sid):
        raise RuntimeError('job table unreadable')
    monkeypatch.setattr(aj, 'open_job_count', boom)
    assert A.is_adoptable_session(s, cfg=ON) is False


def test_flag_off_is_untouched_by_jobs(tracker, tmp_path, jobs):
    s = _live_session(tracker, tmp_path, 'joff', status='running')
    _open_job(jobs, 'joff')
    assert A.is_adoptable_session(s, cfg=OFF) is False    # flag off: never adoptable, as before
