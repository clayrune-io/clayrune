"""MC-1149 phase 1: the rollover state block reports, per background job, its
output file and whether its processes survived the roll, and says what to do
in each case -- instead of "relaunch any that are still needed", which
duplicated survivors and lost the output of the dead.

The process check never spawns a model CLI: the "job" here is a python child
of the test process that holds a temp file open the way the CLI's bash does
with `<task>.output` (measured 2026-11-04, see mc/bg_job_liveness.py). The
child is killed by the PID this test got back from Popen, nothing else.
"""
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc import bg_job_liveness as bl  # noqa: E402

DELIBERATE = ('Command running in background with ID: be3a10n6j. Output is being '
              'written to: C:\\Users\\x\\AppData\\Local\\Temp\\claude\\abc\\tasks\\'
              'be3a10n6j.output. You will be notified when it completes. To check '
              'interim output, use Read on that file path.')
MOVED = ('Command did not complete within its 120s timeout and was moved to the '
         'background (ID: bq3btoz07). Output is being written to: '
         'C:\\Users\\x\\AppData\\Local\\Temp\\claude\\abc\\tasks\\bq3btoz07.output. '
         'You will be notified when it completes. If it is still running after 30m '
         'in the background, it will be stopped and you will be notified.')


# ── parse ────────────────────────────────────────────────────────────────────

def test_parse_deliberate_background_result():
    facts = bl.parse_tool_result(DELIBERATE)
    assert facts == {
        'output_file': 'C:\\Users\\x\\AppData\\Local\\Temp\\claude\\abc\\tasks\\be3a10n6j.output',
        'task_id': 'be3a10n6j', 'moved_on_timeout': False}


def test_parse_moved_on_timeout_result():
    facts = bl.parse_tool_result(MOVED)
    assert facts is not None
    assert facts['output_file'].endswith('tasks\\bq3btoz07.output')
    assert facts['task_id'] == 'bq3btoz07'
    assert facts['moved_on_timeout'] is True


def test_parse_ignores_ordinary_results_and_garbage():
    assert bl.parse_tool_result('ok') is None
    assert bl.parse_tool_result('') is None
    assert bl.parse_tool_result(None) is None
    assert bl.parse_tool_result({'not': 'text'}) is None


# ── holders + liveness against a real child process ─────────────────────────

@pytest.fixture()
def held_file(tmp_path):
    """A child python process holding `out` open, like the job's bash does
    with its stdout. Yields (path, Popen); always kills that one PID."""
    out = tmp_path / 'tasks' / 'bq3btoz07.output'
    out.parent.mkdir()
    code = (f"import time,sys; f=open({str(out)!r},'a'); f.write('go'); f.flush(); "
            "sys.stdout.write('held'); sys.stdout.flush(); time.sleep(60)")
    # The base interpreter, not the venv's python.exe: on Windows that one is
    # a launcher that runs the real python as its own child, so the holder
    # pid would be the grandchild and `proc.kill()` would orphan it.
    exe = getattr(sys, '_base_executable', None) or sys.executable
    proc = subprocess.Popen([exe, '-c', code], stdout=subprocess.PIPE)
    assert proc.stdout is not None
    proc.stdout.read(4)   # 'held': the file is open before we scan
    try:
        yield str(out), proc
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)


def test_holder_scan_finds_the_job_under_the_cli_and_liveness_tracks_it(held_file):
    out, proc = held_file
    holders = bl.holder_pids(out, os.getpid())
    assert holders is not None
    assert [h['pid'] for h in holders] == [proc.pid], holders
    assert bl.liveness(holders) == 'alive'
    proc.kill()
    proc.wait(timeout=10)
    assert bl.liveness(holders) == 'dead'


def test_liveness_rejects_a_recycled_pid(held_file):
    out, proc = held_file
    holders = bl.holder_pids(out, os.getpid())
    assert holders and holders[0]['pid'] == proc.pid
    # Same pid, different process start: must not count as alive.
    holders[0]['create_time'] -= 3600
    assert bl.liveness(holders) == 'dead'


def test_holder_scan_reports_what_it_could_not_determine(tmp_path):
    assert bl.holder_pids(None, os.getpid()) is None, 'no path -> nothing to look for'
    assert bl.holder_pids(str(tmp_path / 'x.output'), None) is None, 'no CLI pid'
    assert bl.holder_pids(str(tmp_path / 'x.output'), 2 ** 22 + 1) is None, 'CLI gone'
    # A scan that ran and found nobody is [] (the job may have finished at once).
    assert bl.holder_pids(str(tmp_path / 'x.output'), os.getpid()) == []
    assert bl.liveness(None) == 'unknown'
    assert bl.liveness([]) == 'unknown'


# ── render ───────────────────────────────────────────────────────────────────

URL = 'http://localhost:5199/api/project/p/agent/s/job'


def _job(**kw):
    base = {'tool': 'Bash', 'input': 'python -m pytest -q', 'tool_use_id': 't1'}
    base.update(kw)
    return base


def test_render_alive_says_do_not_relaunch_and_read_the_file(held_file):
    out, proc = held_file
    holders = bl.holder_pids(out, os.getpid())
    text = bl.render_jobs([_job(output_file=out, task_id='bq3btoz07', holders=holders)],
                          job_url=URL, now=time.time())
    assert 'ALIVE' in text and 'do NOT relaunch' in text and 'Read the output file' in text
    assert f'pid {proc.pid}' in text
    assert out in text and '2 bytes' in text and 'before the roll' in text
    assert '[task bq3btoz07]' in text
    assert 'relaunch any that are still needed' not in text


def test_render_dead_points_at_the_job_route_not_run_in_background(held_file):
    out, proc = held_file
    holders = bl.holder_pids(out, os.getpid())
    proc.kill()
    proc.wait(timeout=10)
    text = bl.render_jobs([_job(output_file=out, holders=holders)], job_url=URL)
    assert 'DEAD' in text and 'relaunch it via the job route below' in text
    assert f'POST {URL}' in text


def test_render_unknown_is_honest_about_what_was_not_recorded(tmp_path):
    missing = str(tmp_path / 'gone.output')
    text = bl.render_jobs([
        _job(),                                              # tool_result never seen
        _job(output_file=missing, holders=None),             # no process recorded
        _job(output_file=missing, holders=[], moved_on_timeout=True),
    ], job_url=URL)
    assert 'output file: unknown (tool_result not seen)' in text
    assert 'no process was recorded when it started' in text
    assert 'no process held the output file when it started' in text
    assert '(missing)' in text
    assert 'moved to the background by the CLI timeout' in text
    assert 'ALIVE' not in text and 'DEAD' not in text


def test_render_empty_is_none():
    assert bl.render_jobs([], job_url=URL) == '(none)'


# ── mc.midturn_rollover owns the entries and calls in here ──────────────────

@pytest.fixture()
def rollover(monkeypatch):
    from mc import state
    from mc import midturn_rollover as mr
    monkeypatch.setitem(state.CONFIG, 'midturn_rollover_enabled', True)
    monkeypatch.setitem(state.CONFIG, 'port', 5199)
    monkeypatch.delenv('MC_PORT', raising=False)
    return mr


class _Proc:
    def __init__(self, pid):
        self.pid = pid


def test_rollover_entry_gets_output_file_and_holders_from_the_tool_result(rollover, held_file):
    out, proc = held_file
    session = {'session_id': 'sess1', 'project_id': 'p1', 'proc': _Proc(os.getpid())}
    rollover.note_tool_use(session, {
        'type': 'tool_use', 'id': 'tu1', 'name': 'Bash',
        'input': {'command': 'python -m pytest -q', 'run_in_background': True}})
    rollover.note_bg_job_result(session, 'tu1', 'Bash', DELIBERATE.replace(
        'C:\\Users\\x\\AppData\\Local\\Temp\\claude\\abc\\tasks\\be3a10n6j.output', out))
    (job,) = session['_mt_bg_jobs']
    assert job['tool_use_id'] == 'tu1'
    assert job['output_file'] == out and job['task_id'] == 'be3a10n6j'
    assert [h['pid'] for h in job['holders']] == [proc.pid]


def test_rollover_tracks_a_foreground_call_the_cli_moved_to_the_background(rollover):
    session = {'session_id': 'sess1', 'project_id': 'p1', 'proc': _Proc(424242)}
    # No run_in_background in the input, so note_tool_use recorded nothing.
    rollover.note_tool_use(session, {'type': 'tool_use', 'id': 'tu9', 'name': 'Bash',
                                     'input': {'command': 'grep -r x .'}})
    assert not session.get('_mt_bg_jobs')
    rollover.note_bg_job_result(session, 'tu9', 'Bash', MOVED)
    (job,) = session['_mt_bg_jobs']
    assert job['moved_on_timeout'] is True and job['task_id'] == 'bq3btoz07'
    assert job['holders'] is None, 'fake CLI pid: nothing could be recorded'
    # An ordinary result for an untracked call adds nothing.
    rollover.note_bg_job_result(session, 'tu10', 'Bash', 'ok')
    assert len(session['_mt_bg_jobs']) == 1


def test_state_block_replaces_the_blanket_relaunch_line(rollover, tmp_path, held_file):
    out, proc = held_file
    session = {'session_id': 'sess1', 'project_id': 'p1', 'proc': _Proc(os.getpid()),
               'task': 'run the suite'}
    rollover.note_tool_use(session, {
        'type': 'tool_use', 'id': 'tu1', 'name': 'Bash',
        'input': {'command': 'python -m pytest -q', 'run_in_background': True}})
    rollover.note_bg_job_result(session, 'tu1', 'Bash',
                                f'Command running in background with ID: j1. Output is '
                                f'being written to: {out}. You will be notified.')
    block = rollover.build_state_block(session, str(tmp_path))
    assert 'relaunch any that are still needed' not in block
    assert 'ALIVE' in block and out in block
    assert 'POST http://localhost:5199/api/project/p1/agent/sess1/job' in block
    proc.kill()
    proc.wait(timeout=10)
    block = rollover.build_state_block(session, str(tmp_path))
    assert 'DEAD' in block and 'relaunch it via the job route below' in block


def test_state_block_with_no_jobs_and_when_flag_off(rollover, monkeypatch, tmp_path):
    from mc import state
    session = {'session_id': 's', 'project_id': 'p', 'proc': _Proc(1)}
    assert '--- Background jobs ---\n(none)' in rollover.build_state_block(session, str(tmp_path))
    monkeypatch.setitem(state.CONFIG, 'midturn_rollover_enabled', False)
    rollover.note_bg_job_result(session, 'tu1', 'Bash', DELIBERATE)
    assert not session.get('_mt_bg_jobs'), 'flag off records nothing'
