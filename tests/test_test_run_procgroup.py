"""MC-1179 TL1: a timeout or cancel ends the unit's whole process group.

Every process here is one a test spawned: a unit started by the launcher under
test, and a grandchild that unit starts. Each grandchild sleeps a bounded few
seconds, so nothing outlives a failed run for long.
"""
from __future__ import annotations

import shutil
import sys
import threading
import time

import pytest

from tests.test_test_run_launcher import api, family, invoke, launcher  # noqa: F401  (api is a fixture)

# Starts a grandchild that sleeps, then writes a marker. The unit itself prints
# a line and then waits far longer than any timeout used below.
GRANDCHILD = ("import subprocess, sys, time\n"
              "print('before-timeout', flush=True)\n"
              "subprocess.Popen([sys.executable, '-c', \"import sys, time; time.sleep(2.5); "
              "open(sys.argv[1], 'w').write('survived')\", sys.argv[1]])\n"
              "time.sleep(60)\n")
LATE = 3.5  # seconds after the launcher returns; the marker would be written at ~2.5 from start


def wait_for_marker(marker, seconds=LATE):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if marker.exists():
            return True
        time.sleep(0.05)
    return marker.exists()


def test_timeout_ends_grandchild_and_keeps_output(api):
    script, marker = api.tmp / 'unit.py', api.tmp / 'marker'
    script.write_text(GRANDCHILD, encoding='utf-8')
    done = invoke(api, '--timeout-seconds', '0.7', '--', sys.executable, str(script), str(marker))
    assert done.returncode != 0
    assert b'before-timeout' in done.stdout
    assert b'output_drain_incomplete' not in done.stderr, done.stderr.decode()
    assert not wait_for_marker(marker), 'grandchild survived the timeout'
    parent, children = family(api, 1)
    assert parent['state'] == children[0]['state'] == 'timeout'


@pytest.mark.skipif(shutil.which('bash') is None, reason='bash not installed')
def test_timeout_shell_unit_ends_grandchild(api):
    # The reported case: a shell unit whose grandchild sleeps, then writes a marker.
    marker = api.tmp / 'marker'
    child = api.tmp / 'child.py'
    child.write_text("import sys, time; print('shell-before', flush=True); time.sleep(2.5); "
                     "open(sys.argv[1], 'w').write('survived')", encoding='utf-8')
    command = f'"{sys.executable}" "{child.as_posix()}" "{marker.as_posix()}"; echo done'
    done = invoke(api, '--timeout-seconds', '0.8', '--shell', 'bash', '--command', command)
    assert done.returncode != 0
    assert b'shell-before' in done.stdout
    assert not wait_for_marker(marker), 'shell grandchild survived the timeout'


def test_cancel_ends_grandchild_and_keeps_output(api, monkeypatch, capfdbinary):
    script, marker = api.tmp / 'unit.py', api.tmp / 'marker'
    script.write_text(GRANDCHILD, encoding='utf-8')
    original = launcher.execute

    def cancel_soon(spec, client, project, cancelled, telemetry, key):
        threading.Timer(0.8, cancelled.set).start()
        return original(spec, client, project, cancelled, telemetry, key)

    monkeypatch.setattr(launcher, 'execute', cancel_soon)
    args, units, stop = launcher.parse(['--project', 'fixture', '--port', str(api.port), '--',
                                        sys.executable, str(script), str(marker)])
    code = launcher.run(args, units, stop)
    captured = capfdbinary.readouterr()
    assert code != 0
    assert b'before-timeout' in captured.out
    assert b'output_drain_incomplete' not in captured.err
    assert not wait_for_marker(marker), 'grandchild survived the cancel'
    parent, children = family(api, 1)
    assert parent['state'] == children[0]['state'] == 'cancelled'


# A unit that starts one child with CREATE_BREAKAWAY_FROM_JOB (what the agent shim
# launch does) and one ordinary child, each writing a marker after a short sleep.
# It records whether the breakaway spawn was allowed, then waits past the timeout.
BREAKAWAY_UNIT = ("import subprocess, sys, time\n"
                  "marker = sys.argv[1]\n"
                  "code = \"import sys, time; time.sleep(3); open(sys.argv[1], 'w').write('survived')\"\n"
                  "try:\n"
                  "    subprocess.Popen([sys.executable, '-c', code, marker + '.breakaway'],\n"
                  "                     creationflags=subprocess.CREATE_BREAKAWAY_FROM_JOB)\n"
                  "    status = 'ok'\n"
                  "except OSError as exc:\n"
                  "    status = 'refused: %r' % (exc,)\n"
                  "subprocess.Popen([sys.executable, '-c', code, marker + '.normal'])\n"
                  "open(marker + '.status', 'w').write(status)\n"
                  "time.sleep(60)\n")


@pytest.mark.skipif(sys.platform != 'win32', reason='Job Objects and CREATE_BREAKAWAY_FROM_JOB are Windows-only')
def test_breakaway_child_is_allowed_and_ordinary_child_is_killed_on_timeout(api):
    # MC-2982701a: the launcher's job must permit an explicit breakaway (the shim
    # launch needs it) while every ordinary descendant is still ended on timeout.
    import subprocess
    try:
        probe = subprocess.Popen([sys.executable, '-c', 'pass'], creationflags=subprocess.CREATE_BREAKAWAY_FROM_JOB)
        probe.wait()
    except OSError as exc:
        pytest.skip(f'the job pytest itself runs in refuses breakaway ({exc!r}); the probe, not the launcher, fails')
    script, marker = api.tmp / 'unit.py', api.tmp / 'marker'
    script.write_text(BREAKAWAY_UNIT, encoding='utf-8')
    done = invoke(api, '--timeout-seconds', '1.5', '--', sys.executable, str(script), str(marker))
    assert done.returncode != 0
    status = (api.tmp / 'marker.status')
    assert status.read_text() == 'ok', 'breakaway spawn was refused under the launcher job'
    assert wait_for_marker(api.tmp / 'marker.breakaway', 6), 'breakaway child did not outlive the timeout'
    time.sleep(0.5)  # the ordinary child would have written at the same moment
    assert not (api.tmp / 'marker.normal').exists(), 'ordinary child survived the timeout'
    parent, children = family(api, 1)
    assert parent['state'] == children[0]['state'] == 'timeout'


@pytest.mark.skipif(sys.platform != 'win32', reason='Job Objects and CREATE_BREAKAWAY_FROM_JOB are Windows-only')
@pytest.mark.skipif(shutil.which('bash') is None, reason='bash not installed')
def test_shell_unit_job_stays_strict_and_bash_grandchild_dies_on_timeout(api):
    # MC-2982701a: only argv units may break away. A shell unit's job is strict, so
    # nothing bash starts, even with CREATE_BREAKAWAY_FROM_JOB, outlives a timeout.
    script, marker = api.tmp / 'unit.py', api.tmp / 'marker'
    script.write_text(BREAKAWAY_UNIT, encoding='utf-8')
    command = f'"{sys.executable}" "{script.as_posix()}" "{marker.as_posix()}"; echo done'
    done = invoke(api, '--timeout-seconds', '1.5', '--shell', 'bash', '--command', command)
    assert done.returncode != 0
    assert not wait_for_marker(api.tmp / 'marker.breakaway', 5), 'breakaway child survived a shell unit timeout'
    assert not (api.tmp / 'marker.normal').exists(), 'ordinary child survived the timeout'


def test_normal_exit_is_untouched(api):
    done = invoke(api, '--', sys.executable, '-c', 'print("ok"); raise SystemExit(3)')
    assert done.returncode == 3 and done.stdout.strip() == b'ok' and done.stderr == b''
    parent, children = family(api, 1)
    assert parent['state'] == children[0]['state'] == 'failed'


def test_descendant_that_outlives_a_normal_exit_is_left_running(api):
    # Only a timeout or cancel ends the group; a unit that returns on its own keeps
    # whatever it deliberately left behind.
    marker = api.tmp / 'marker'
    unit = ("import subprocess, sys\n"
            "subprocess.Popen([sys.executable, '-c', \"import sys, time; time.sleep(1.5); "
            "open(sys.argv[1], 'w').write('ok')\", sys.argv[1]], "
            "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n")
    script = api.tmp / 'unit.py'
    script.write_text(unit, encoding='utf-8')
    done = invoke(api, '--', sys.executable, str(script), str(marker))
    assert done.returncode == 0
    assert wait_for_marker(marker, 5), 'a normally exiting unit had its descendant killed'


def test_ungrouped_fallback_still_runs_and_kills_direct_child(api, monkeypatch):
    # If a group cannot be made, the unit still runs and a timeout still ends the child.
    real = launcher.start_group

    def ungrouped(argv, **kw):
        proc, group = real(argv, **kw)
        group.contained = False
        return proc, group

    monkeypatch.setattr(launcher, 'start_group', ungrouped)
    args, units, stop = launcher.parse(['--project', 'fixture', '--port', str(api.port),
                                        '--timeout-seconds', '0.3', '--',
                                        sys.executable, '-c', 'import time; time.sleep(30)'])
    assert launcher.run(args, units, stop) != 0
    parent, children = family(api, 1)
    assert parent['state'] == children[0]['state'] == 'timeout'
