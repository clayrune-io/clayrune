"""POSIX wiring of the CDP pipe (mc/browser_cdp_pipe.py `_spawn_posix`).

Chromium reads CDP from fd 3 and writes it to fd 4. Popen closes every fd not in
`pass_fds` AFTER `preexec_fn`, so a dup2 onto 3/4 that is not listed there is
undone before exec and Chromium gets no CDP (measured 2026-10-14, Chromium 151
on Linux: Target.getTargets never answered). These tests spawn a real child that
speaks the pipe protocol on 3/4 instead of Chromium.
"""
import os
import stat
import sys

import pytest

from mc import browser_cdp_pipe as bp

pytestmark = pytest.mark.skipif(sys.platform == 'win32', reason='POSIX fd wiring')

_CHILD = r'''#!{python}
import json, os
buf = b''
while b'\0' not in buf:
    buf += os.read(3, 65536)
msg = json.loads(buf.split(b'\0')[0])
fds = []
for name in os.listdir('/dev/fd'):
    try:
        os.fstat(int(name))
        fds.append(int(name))
    except OSError:
        pass
reply = {{'id': msg['id'], 'result': {{'product': 'fake/1', 'jsVersion': json.dumps(sorted(fds))}}}}
os.write(4, json.dumps(reply).encode() + b'\0')
while os.read(3, 65536):
    pass
'''


def _open_fds():
    return {int(n) for n in os.listdir('/dev/fd')}


@pytest.fixture
def child(tmp_path):
    path = tmp_path / 'fake_chromium'
    path.write_text(_CHILD.format(python=sys.executable))
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


def _spawn(child):
    null = {'stdin': -3, 'stdout': -3, 'stderr': -3}     # subprocess.DEVNULL
    return bp.spawn([child], null)


def test_child_gets_cdp_on_fd3_and_fd4_and_nothing_else(child):
    proc, transport = _spawn(child)
    try:
        v = transport.version()
        assert v['Browser'] == 'fake/1'
        # The child holds stdio plus the two CDP fds, not the parent's other pipe ends.
        import json
        child_fds = set(json.loads(v['V8-Version']))
        assert child_fds == {0, 1, 2, 3, 4}
    finally:
        transport.close()
        proc.wait(10)


def test_spawn_leaves_no_fd_behind_in_the_parent(child):
    before = _open_fds()
    proc, transport = _spawn(child)
    transport.version()
    transport.close()
    proc.wait(10)
    # the reader thread closes its read end once it sees EOF
    import time
    end = time.time() + 5
    while _open_fds() - before and time.time() < end:
        time.sleep(0.05)
    assert _open_fds() - before == set()


def test_spawn_works_with_fds_3_and_4_already_taken(child):
    held = [os.open(os.devnull, os.O_RDONLY) for _ in range(6)]
    try:
        proc, transport = _spawn(child)
        try:
            assert transport.version()['Browser'] == 'fake/1'
        finally:
            transport.close()
            proc.wait(10)
    finally:
        for fd in held:
            os.close(fd)
