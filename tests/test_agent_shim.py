"""MC-1150 P1: the agent supervisor shim, its trampoline and client.

Real processes throughout: nothing here mocks the thing being proven, which is
that a detached shim (and the CLI under it) outlives the server. The section-2
survival matrix of docs/AGENT_SURVIVE_RESTART_DESIGN.md is reproduced as
`test_survival_matrix_*`; the one cell that differs from the measured table is
Windows `robust` x `jobclose_nobreak`, where Dave's decision (refuse adoption
when breakaway is denied, no WMI path) replaces the table's "survives".

Process hygiene: every process a test starts is recorded and, at teardown,
killed by (pid, create-time) identity. Nothing is killed by image name and
nothing on the server's port is touched.

Run:  .venv/Scripts/python.exe -m pytest tests/test_agent_shim.py -q
"""
from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from mc import agent_shim as shim  # noqa: E402
from mc import agent_shim_client as C  # noqa: E402

STANDIN = str(REPO / 'tests' / 'agent_shim_standin.py')
PY_BASE = getattr(sys, '_base_executable', sys.executable)
WIN = sys.platform == 'win32'
POSIX = not WIN

try:
    import psutil
except ImportError:                       # pragma: no cover
    psutil = None

needs_psutil = pytest.mark.skipif(psutil is None, reason='psutil not installed')
windows_only = pytest.mark.skipif(not WIN, reason='Windows job objects / taskkill')
linux_only = pytest.mark.skipif(not sys.platform.startswith('linux'), reason='/proc + WSL/Linux')


# ───────────────────────────── helpers ──────────────────────────────────────
def wait_for(pred, timeout=15.0, step=0.05):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        v = pred()
        if v:
            return v
        time.sleep(step)
    return None


def alive_ct(pid, ct):
    """Independent (psutil) liveness + identity check; stdlib twin without psutil."""
    if psutil is None:
        return shim.same_process(pid, ct)
    try:
        p = psutil.Process(pid)
        return abs(p.create_time() - ct) < 2 and p.status() != psutil.STATUS_ZOMBIE
    except Exception:
        return False


def log_text(sdir):
    try:
        return (Path(sdir) / 'out.log').read_text(encoding='utf-8', errors='replace')
    except OSError:
        return ''


def nlines(sdir):
    return len(log_text(sdir).splitlines())


class Tracker:
    """Everything a test starts, killed at teardown by pid + create-time."""

    def __init__(self):
        self.procs = []                    # (pid, create_time)

    def add(self, pid, ct):
        if pid and ct:
            self.procs.append((pid, ct))

    def add_manifest(self, sdir):
        m = C.read_json(Path(sdir) / 'shim.json') or {}
        self.add(m.get('shim_pid'), m.get('shim_ct'))
        self.add(m.get('cli_pid'), m.get('cli_ct'))

    def reap(self):
        if psutil is None:
            for pid, ct in self.procs:
                if shim.same_process(pid, ct):
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except OSError:
                        pass
            return
        for pid, ct in self.procs:
            try:
                p = psutil.Process(pid)
                if abs(p.create_time() - ct) < 2:
                    p.kill()
            except Exception:
                pass


@pytest.fixture
def tracker():
    t = Tracker()
    yield t
    t.reap()


def child_argv(*flags):
    return [PY_BASE, STANDIN, 'child', *flags]


def start(tracker, tmp_path, name, *flags, mode='oneshot', prompt=None, cfg=None,
          tick_s=0.1, owner_pid=None, env=None, logs=None):
    res = C.launch(tmp_path, name, argv=child_argv(*flags), cwd=None, mode=mode,
                   prompt=prompt, cfg=cfg, tick_s=tick_s, owner_pid=owner_pid, env=env,
                   log=(logs.append if logs is not None else None))
    assert res.ok, res.error
    tracker.add_manifest(C.session_dir(tmp_path, name))
    return res


def sleeper(tracker):
    """A live process this test owns (used as a stand-in owner pid)."""
    p = subprocess.Popen([PY_BASE, '-c', 'import time; time.sleep(120)'])
    tracker.add(p.pid, shim.proc_create_time(p.pid))
    return p


def raw_hello(port, token, pid, boot='b'):
    s = socket.create_connection(('127.0.0.1', port), timeout=5)
    s.settimeout(5)
    rf = s.makefile('rb')
    s.sendall(f'HELLO {token} {pid} {boot}\n'.encode())
    return s, rf, rf.readline().decode().split()


# ───────────────────────────── config / flags ───────────────────────────────
def test_config_defaults_match_decided_values():
    assert C.enabled({}) is False                       # default OFF (Q6)
    assert C.enabled({'agent_survive_restart_enabled': True}) is True
    t = C.timers_from_config({})
    assert (t['grace_s'], t['exit_grace_s'], t['orphan_max_s']) == (20 * 60, 60 * 60, 12 * 3600)
    t = C.timers_from_config({'shim_orphan_grace_min': 1, 'shim_exit_grace_min': 2,
                              'shim_orphan_max_hours': 3})
    assert (t['grace_s'], t['exit_grace_s'], t['orphan_max_s']) == (60, 120, 10800)
    assert C.timers_from_config({'shim_orphan_grace_min': 'junk'})['grace_s'] == 20 * 60


def test_shims_root_is_a_sibling_of_data_projects(tmp_path):
    assert C.shims_root(tmp_path / 'data' / 'projects') == tmp_path / 'data' / 'agent_shims'


# ───────────────────── exit.json rc / oneshot lifecycle ─────────────────────
def test_exit_json_records_cli_return_code(tracker, tmp_path):
    res = start(tracker, tmp_path, 's1', '--lines', '5', '--rc', '7')
    proc = res.proc
    lines = list(proc.stdout)                          # ends on its own: exit.json + drained
    assert lines == [f'line {i}\n' for i in range(5)]
    info = json.loads((C.session_dir(tmp_path, 's1') / 'exit.json').read_text())
    assert info['rc'] == 7 and info['reason'] == 'exit' and info['signal'] is None
    assert proc.wait(timeout=10) == 7 and proc.poll() == 7
    m = proc.manifest
    # a oneshot shim has nothing to collect from, so it follows the CLI out
    assert wait_for(lambda: not shim.same_process(m['shim_pid'], m['shim_ct']), 10)


def test_prompt_is_delivered_then_deleted(tracker, tmp_path):
    res = start(tracker, tmp_path, 's2', '--cat', prompt='héllo prompt\n'.encode('utf-8'))
    assert ''.join(res.proc.stdout) == 'got:héllo prompt\n'
    assert not (C.session_dir(tmp_path, 's2') / 'prompt.txt').exists()
    assert res.proc.wait(timeout=10) == 0


def test_oneshot_stdin_write_raises_broken_pipe(tracker, tmp_path):
    res = start(tracker, tmp_path, 's3', '--lines', '1')
    with pytest.raises(BrokenPipeError):
        res.proc.stdin.write('x')


def test_spec_and_files_never_hold_env_values(tracker, tmp_path, monkeypatch):
    marker = 'MC_SHIM_MARKER_' + os.urandom(6).hex()
    secret = 'sekret-' + os.urandom(8).hex()
    env = dict(os.environ)
    env[marker] = secret
    res = start(tracker, tmp_path, 's4', '--env', marker, '--lines', '1', env=env)
    out = ''.join(res.proc.stdout)
    assert f'{marker}={secret}' in out                 # the value DID reach the CLI
    sdir = C.session_dir(tmp_path, 's4')
    spec = json.loads((sdir / 'spec.json').read_text())
    assert marker in spec['env_names']                 # name recorded
    for f in sdir.iterdir():
        if f.name == 'out.log':
            continue                                   # the CLI itself printed it there
        assert secret not in f.read_text(errors='replace'), f'secret on disk in {f.name}'


# ───────────────────── SEND seq idempotence / reconnect ─────────────────────
def test_send_sequence_is_idempotent_across_reconnect(tracker, tmp_path):
    res = start(tracker, tmp_path, 'i1', mode='interactive')
    sdir = C.session_dir(tmp_path, 'i1')
    cl = C.ShimClient(sdir)
    hello = cl.connect()
    assert hello.last_seq == 0 and hello.cli_alive
    assert cl.send(b'one\n') == 1
    assert wait_for(lambda: 'echo:one' in log_text(sdir))

    # a resend of an already-applied seq (same owner pid reconnecting) is ACKed, not rewritten
    m = C.read_json(sdir / 'shim.json')
    s, rf, rep = raw_hello(m['port'], m['token'], os.getpid())
    assert rep[:2] == ['OK', '1']
    s.sendall(b'SEND 1 4\nDUP\n')
    assert rf.readline().decode().split() == ['ACK', '1']
    # a gap is rejected, never applied
    s.sendall(b'SEND 5 4\nGAP\n')
    assert rf.readline().decode().split() == ['ERR', 'seq_gap', '2']
    # a writer whose connection dies right after seq 2 landed (ACK never read)
    s.sendall(b'SEND 2 4\ntwo\n')
    s.close()
    assert wait_for(lambda: 'echo:two' in log_text(sdir))

    # a fresh client reconnects and continues from the shim's last_seq
    cl.close()
    cl = C.ShimClient(sdir)
    assert cl.connect().last_seq == 2
    assert cl.send(b'three\n') == 3
    assert wait_for(lambda: 'echo:three' in log_text(sdir))

    # lost-ACK for the SAME client: the line is applied, the ACK read fails; the
    # retry sees last_seq >= seq on the new HELLO and does not write it twice
    real = cl._readline
    calls = {'n': 0}

    def flaky():
        calls['n'] += 1
        if calls['n'] == 1:
            raise ConnectionError('ack lost')
        return real()
    cl._readline = flaky
    assert cl.send(b'four\n') == 4
    cl._readline = real
    assert wait_for(lambda: 'echo:four' in log_text(sdir))
    time.sleep(0.3)
    text = log_text(sdir)
    assert text.count('echo:one') == 1 and text.count('echo:two') == 1
    assert text.count('echo:three') == 1 and text.count('echo:four') == 1
    assert 'DUP' not in text and 'GAP' not in text
    cl.close()
    res.proc.close()


def test_client_send_survives_a_dropped_connection(tracker, tmp_path):
    start(tracker, tmp_path, 'i2', mode='interactive')
    sdir = C.session_dir(tmp_path, 'i2')
    cl = C.ShimClient(sdir)
    cl.connect()
    assert cl.send(b'a\n') == 1
    cl._sock.close()                                   # abrupt local drop; shim sees EOF
    assert cl.send(b'b\n') == 2                        # reconnects transparently
    assert wait_for(lambda: 'echo:b' in log_text(sdir))
    assert log_text(sdir).count('echo:a') == 1 and log_text(sdir).count('echo:b') == 1


def test_close_stdin_ends_a_mode_b_style_cli(tracker, tmp_path):
    res = start(tracker, tmp_path, 'i3', mode='interactive')
    res.proc.stdin.write('hi\n')
    assert wait_for(lambda: 'echo:hi' in log_text(C.session_dir(tmp_path, 'i3')))
    res.proc.stdin.close()
    assert res.proc.wait(timeout=10) == 0
    with pytest.raises(BrokenPipeError):
        res.proc.stdin.write('late')
    # collected: the owner connection closes after exit, so the shim finishes
    res.proc.close()
    m = res.proc.manifest
    assert wait_for(lambda: not shim.same_process(m['shim_pid'], m['shim_ct']), 10)


# ───────────────────────── token / owner fence ──────────────────────────────
def test_token_rejection_and_no_state_change(tracker, tmp_path):
    start(tracker, tmp_path, 't1', mode='interactive')
    sdir = C.session_dir(tmp_path, 't1')
    m = C.read_json(sdir / 'shim.json')
    owner_before = m['owner_pid']
    s, rf, rep = raw_hello(m['port'], 'f' * 32, os.getpid())
    assert rep == ['ERR', 'token']
    assert rf.readline() == b''                        # connection closed
    s.close()
    s, rf, rep = raw_hello(m['port'], '', os.getpid())   # empty token -> malformed
    assert rep[:2] == ['ERR', 'protocol'] or rep == ['ERR', 'token']
    s.close()
    # a good client still gets in, and the manifest never named the intruder
    assert C.ShimClient(sdir).connect().cli_alive
    assert C.read_json(sdir / 'shim.json')['owner_pid'] in (owner_before, os.getpid())


@needs_psutil
def test_owner_fence_refuses_live_second_owner_and_displaces_dead_one(tracker, tmp_path):
    owner = sleeper(tracker)
    start(tracker, tmp_path, 'f1', mode='interactive', owner_pid=owner.pid)
    sdir = C.session_dir(tmp_path, 'f1')
    # the spawner (a LIVE owner) is still there: a second server must be refused
    with pytest.raises(C.ShimRefused):
        C.ShimClient(sdir).connect()
    m = C.read_json(sdir / 'shim.json')
    s, rf, rep = raw_hello(m['port'], m['token'], os.getpid())
    assert rep == ['REFUSED', str(owner.pid)]
    s.close()
    # the owner dies (a pid this test started): the next server displaces it
    owner.kill()
    owner.wait(timeout=10)
    cl = C.ShimClient(sdir)
    assert wait_for(lambda: _try_connect(cl), 10)
    assert cl.send(b'x\n') == 1
    # and now the first server's replacement of itself is a no-op reconnect, but
    # a different LIVE pid is refused again
    other = sleeper(tracker)
    s, rf, rep = raw_hello(m['port'], m['token'], other.pid)
    assert rep == ['REFUSED', str(os.getpid())]
    s.close()
    cl.close()


def _try_connect(cl):
    try:
        cl.connect()
        return True
    except C.ShimRefused:
        return False


# ───────────────────────── PID-reuse guard ──────────────────────────────────
def test_pid_reuse_guard_requires_matching_create_time():
    me = os.getpid()
    ct = shim.proc_create_time(me)
    assert ct is not None
    assert shim.same_process(me, ct)
    assert not shim.same_process(me, ct - 3600)        # same pid, different incarnation
    assert not shim.same_process(me, None)             # unknown identity never matches
    assert not shim.same_process(2 ** 22 + 12345, ct)  # no such pid
    assert shim.proc_create_time(0) is None and shim.proc_create_time(-5) is None


def test_inspect_and_connect_reject_a_reused_pid(tmp_path):
    me = os.getpid()
    ct = shim.proc_create_time(me)
    d = tmp_path / 'reuse'
    d.mkdir()
    (d / 'shim.json').write_text(json.dumps({
        'schema': shim.SCHEMA, 'shim_pid': me, 'shim_ct': ct - 5000,
        'cli_pid': me, 'cli_ct': ct, 'port': 1, 'token': 'x' * 32}))
    st = C.inspect(d)
    assert st is not None and st.shim_alive is False and st.cli_alive is True
    with pytest.raises(C.ShimUnavailable):
        C.ShimClient(d).connect()                      # refuses before touching the socket


def test_inspect_ignores_a_manifest_from_another_schema(tmp_path):
    d = tmp_path / 'schema'
    d.mkdir()
    (d / 'shim.json').write_text(json.dumps({'schema': 999, 'shim_pid': os.getpid()}))
    assert C.inspect(d) is None


# ───────────────────────── tail hold-back ───────────────────────────────────
def test_tail_holds_back_a_partial_line_until_its_newline(tmp_path):
    p = tmp_path / 'out.log'
    p.write_bytes(b'')
    done = threading.Event()
    tail = C.LogTail(p, finished=done.is_set, poll_s=0.01)
    got = []
    t = threading.Thread(target=lambda: got.extend(tail), daemon=True)
    t.start()
    with open(p, 'ab') as f:
        f.write(b'complete\npart')
        f.flush()
        assert wait_for(lambda: got == ['complete\n'])
        time.sleep(0.15)
        assert got == ['complete\n']                   # 'part' is withheld
        f.write(b'ial\n')
    assert wait_for(lambda: got == ['complete\n', 'partial\n'])
    assert tail.offset == len(b'complete\npartial\n')
    done.set()
    t.join(5)
    assert not t.is_alive()


def test_tail_flushes_an_unterminated_last_line_only_after_finish(tmp_path):
    p = tmp_path / 'out.log'
    p.write_bytes(b'one\ntail-no-newline')
    done = threading.Event()
    tail = C.LogTail(p, finished=done.is_set, poll_s=0.01)
    got = []
    t = threading.Thread(target=lambda: got.extend(tail), daemon=True)
    t.start()
    assert wait_for(lambda: got == ['one\n'])
    time.sleep(0.1)
    assert got == ['one\n']
    done.set()
    t.join(5)
    assert got == ['one\n', 'tail-no-newline']


def test_tail_decodes_a_multibyte_char_split_across_reads_and_resumes_at_offset(tmp_path):
    p = tmp_path / 'out.log'
    p.write_bytes(b'ab\n\xc3')
    done = threading.Event()
    tail = C.LogTail(p, finished=done.is_set, poll_s=0.01)
    got = []
    t = threading.Thread(target=lambda: got.extend(tail), daemon=True)
    t.start()
    assert wait_for(lambda: got == ['ab\n'])
    with open(p, 'ab') as f:
        f.write(b'\xa9\nlast\n')
    assert wait_for(lambda: got == ['ab\n', 'é\n', 'last\n'])
    done.set()
    t.join(5)
    # a restarted server resumes from the checkpointed offset, no repeats
    off = len(b'ab\n')
    again = C.LogTail(p, offset=off, finished=lambda: True, poll_s=0.01)
    assert list(again) == ['é\n', 'last\n']


# ───────────────────────── orphan timers (injected clock) ───────────────────
class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _policy(clock, **kw):
    return shim.LifetimePolicy(grace_s=20, exit_grace_s=60, orphan_max_s=120, clock=clock, **kw)


def _tick(p, **kw):
    base = dict(owner_attached=False, cli_exited=False, collected=False, log_size=0, tree_busy=False)
    base.update(kw)
    return p.tick(**base)


def test_orphan_timers_attached_owner_resets_everything():
    clk = FakeClock()
    p = _policy(clk)
    for _ in range(50):
        clk.t += 100
        assert _tick(p, owner_attached=True) == shim.A_NONE
    assert _tick(p) == shim.A_NONE                     # orphan clock starts only now
    clk.t += 119
    assert _tick(p, tree_busy=True) == shim.A_NONE     # busy tree: not idle, not yet expired
    clk.t += 5
    assert _tick(p, owner_attached=True) == shim.A_NONE
    clk.t += 119
    assert _tick(p, tree_busy=True) == shim.A_NONE     # reattached in between: clock restarted


def test_orphan_timers_idle_test_needs_silence_and_no_cpu_and_closes_once():
    clk = FakeClock()
    p = _policy(clk)
    assert _tick(p) == shim.A_NONE
    clk.t += 19
    assert _tick(p) == shim.A_NONE                     # before grace
    clk.t += 2                                         # 21s orphaned, 21s silent
    assert _tick(p, tree_busy=True) == shim.A_NONE     # silent 25-min pytest: CPU says busy
    assert _tick(p, tree_busy=None) == shim.A_NONE     # no psutil: unknown is not idle
    assert _tick(p, tree_busy=False, log_size=10) == shim.A_NONE   # log just grew
    clk.t += 21
    assert _tick(p, tree_busy=False, log_size=10) == shim.A_CLOSE_STDIN
    clk.t += 1
    assert _tick(p, tree_busy=False, log_size=10) == shim.A_NONE   # only once


def test_orphan_timers_expire_at_orphan_max_even_when_busy():
    clk = FakeClock()
    p = _policy(clk)
    assert _tick(p, tree_busy=True) == shim.A_NONE
    clk.t += 119
    assert _tick(p, tree_busy=True, log_size=1) == shim.A_NONE
    clk.t += 2
    assert _tick(p, tree_busy=True, log_size=2) == shim.A_EXPIRE


def test_orphan_timers_idle_close_disabled_for_oneshot():
    clk = FakeClock()
    p = _policy(clk, idle_close=False)
    _tick(p)
    clk.t += 60
    assert _tick(p, tree_busy=False) == shim.A_NONE
    clk.t += 70
    assert _tick(p, tree_busy=False) == shim.A_EXPIRE


def test_exit_grace_waits_for_a_collector_then_finishes():
    clk = FakeClock()
    p = _policy(clk)
    assert _tick(p, cli_exited=True) == shim.A_NONE
    clk.t += 59
    assert _tick(p, cli_exited=True) == shim.A_NONE
    clk.t += 2
    assert _tick(p, cli_exited=True) == shim.A_FINISH  # nobody collected: exit_grace elapsed
    p2 = _policy(clk)
    assert _tick(p2, cli_exited=True, collected=True) == shim.A_FINISH


@needs_psutil
def test_real_shim_closes_stdin_when_orphaned_and_idle(tracker, tmp_path):
    gone = subprocess.Popen([PY_BASE, '-c', 'pass'])
    gone.wait()                                        # a dead owner pid we started
    cfg = {'shim_orphan_grace_min': 0.005, 'shim_exit_grace_min': 1, 'shim_orphan_max_hours': 0.05}
    res = start(tracker, tmp_path, 'o1', '--silent', mode='interactive', cfg=cfg,
                owner_pid=gone.pid)
    # psutil reached the base-interpreter shim through extra_sys_path, the CLI is
    # silent and CPU-idle, so after the grace the shim closes its stdin and it exits
    res.proc.close()                                   # drop our connection: nobody owns it now
    info = wait_for(lambda: C.read_json(C.session_dir(tmp_path, 'o1') / 'exit.json'), 20)
    assert info is not None, (C.session_dir(tmp_path, 'o1') / 'shim.log').read_text()
    assert info['reason'] == 'stdin_idle' and info['rc'] == 0
    res.proc.close()


def test_real_shim_expires_a_busy_orphan(tracker, tmp_path):
    gone = subprocess.Popen([PY_BASE, '-c', 'pass'])
    gone.wait()
    cfg = {'shim_orphan_grace_min': 5, 'shim_exit_grace_min': 1, 'shim_orphan_max_hours': 0.0008}
    res = start(tracker, tmp_path, 'o2', mode='interactive', cfg=cfg, owner_pid=gone.pid)
    res.proc.close()
    info = wait_for(lambda: C.read_json(C.session_dir(tmp_path, 'o2') / 'exit.json'), 20)
    assert info is not None
    assert info['reason'] == 'expired'
    m = C.read_json(C.session_dir(tmp_path, 'o2') / 'shim.json')
    assert wait_for(lambda: not shim.same_process(m['cli_pid'], m['cli_ct']), 10)


# ───────────────────────── spawn recipe branch ──────────────────────────────
def test_spawn_recipe_logs_the_branch_it_took(tmp_path):
    seen = []

    class FakeProc:
        def wait(self, timeout=None):
            return 0

    calls = []

    def ok_popen(argv, **kw):
        calls.append(kw)
        return FakeProc()

    def denied_popen(argv, **kw):
        raise PermissionError(5, 'Access is denied')

    r = C.spawn_detached(tmp_path / 'a', popen=ok_popen, platform='win32', log=seen.append)
    assert r.ok and r.branch == C.BRANCH_WIN_BREAKAWAY
    assert calls[-1]['creationflags'] & 0x01000000 and calls[-1]['creationflags'] & 0x00000008
    assert 'branch=win_breakaway ' in seen[-1]

    r = C.spawn_detached(tmp_path / 'b', popen=denied_popen, platform='win32', log=seen.append)
    assert not r.ok and r.branch == C.BRANCH_WIN_REFUSED     # refused, NOT worked around
    assert 'branch=win_breakaway_refused' in seen[-1]

    r = C.spawn_detached(tmp_path / 'c', popen=ok_popen, platform='linux', log=seen.append)
    assert r.ok and r.branch == C.BRANCH_POSIX and calls[-1]['start_new_session'] is True
    assert 'branch=posix_double_fork' in seen[-1]

    r = C.spawn_detached(tmp_path / 'd', popen=lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError('x')),
                         platform='linux', log=seen.append)
    assert not r.ok and r.branch == C.BRANCH_FAILED


def test_real_launch_logs_its_branch(tracker, tmp_path):
    logs = []
    res = start(tracker, tmp_path, 'b1', '--lines', '1', logs=logs)
    expect = C.BRANCH_WIN_BREAKAWAY if WIN else C.BRANCH_POSIX
    assert res.branch == expect
    assert any(f'branch={expect}' in m for m in logs), logs


def test_a_failed_trampoline_spawn_reports_instead_of_hanging(tmp_path):
    seen = []

    class BadProc:
        def wait(self, timeout=None):
            return 1

    d = tmp_path / 'e'
    d.mkdir()
    (d / 'spawn_error.json').write_text(json.dumps({'error': 'boom'}))
    r = C.spawn_detached(d, popen=lambda *a, **k: BadProc(), log=seen.append)
    assert not r.ok and r.error == 'boom'


# ───────────────────── shim crash: Mode A CLI unaffected ────────────────────
@needs_psutil
def test_shim_crash_leaves_a_oneshot_cli_running_and_tail_still_ends(tracker, tmp_path):
    res = start(tracker, tmp_path, 'c1', '--lines', '30', '--interval', '0.1', '--rc', '3')
    m = res.proc.manifest
    psutil.Process(m['shim_pid']).kill()               # the shim only; our own started pid
    assert wait_for(lambda: not shim.same_process(m['shim_pid'], m['shim_ct']), 10)
    assert shim.same_process(m['cli_pid'], m['cli_ct'])     # stdout was a file: CLI unaffected
    lines = list(res.proc.stdout)                      # ends by PID liveness, no exit.json
    assert lines == [f'line {i}\n' for i in range(30)]
    assert not (C.session_dir(tmp_path, 'c1') / 'exit.json').exists()
    assert res.proc.poll() == -1 and res.proc.exit_inferred


# ───────────────────── frozen-mode entry (app.py flag) ──────────────────────
def test_app_flag_runs_tramp_then_shim_without_booting_the_app(tracker, tmp_path):
    spec = C.build_spec(argv=child_argv('--lines', '3', '--rc', '2'), cwd=None, mode='oneshot',
                        session_id='app1', timers=C.timers_from_config({}, tick_s=0.1))
    d = tmp_path / 'app1'
    C.write_spec(d, spec)
    r = subprocess.run([PY_BASE, str(REPO / 'app.py'), '--clayrune-shim', 'tramp', str(d)],
                       cwd=str(REPO), timeout=60, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    m = C.wait_ready(d, spec['launch_id'], 20)
    assert m is not None
    tracker.add_manifest(d)
    info = wait_for(lambda: C.read_json(d / 'exit.json'), 15)
    assert info and info['rc'] == 2
    # the `run` role is the shim itself, in the foreground
    d2 = tmp_path / 'app2'
    spec2 = C.build_spec(argv=child_argv('--lines', '2', '--rc', '4'), cwd=None, mode='oneshot',
                         session_id='app2', timers=C.timers_from_config({}, tick_s=0.1))
    C.write_spec(d2, spec2)
    r = subprocess.run([PY_BASE, str(REPO / 'app.py'), '--clayrune-shim', 'run', str(d2)],
                       cwd=str(REPO), timeout=60, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert C.read_json(d2 / 'exit.json')['rc'] == 4


def test_run_shim_entry_rejects_bad_arguments():
    from mc.agent_shim_tramp import run_shim_entry
    assert run_shim_entry([]) == 2
    assert run_shim_entry(['bogus', 'x']) == 2
    assert run_shim_entry(['run']) == 2


# ═════════════════ section-2 survival matrix, as an automated test ══════════
# survives = shim AND CLI alive after the server dies, the log keeps growing,
# and a NEW owner (this process) reconnects and gets a stdin round trip.
_WIN_VARIANTS = ('plain', 'detached', 'breakaway', 'double', 'wmi', 'robust')
_WIN_KILLS = ('exit', 'taskkill_tree', 'jobclose', 'jobclose_nobreak')
Y, N = 'survives', 'dies'
# docs/AGENT_SURVIVE_RESTART_DESIGN.md section 2. Two cells are not "survives/dies":
#   breakaway x jobclose_nobreak = the spawn itself fails (access denied);
#   robust x jobclose_nobreak    = REFUSED. The table says WMI rescues it; Dave's
#                                  decision (a) refuses adoption instead. Nothing
#                                  is spawned and the branch is logged.
WIN_EXPECT = {
    ('plain', 'exit'): Y, ('plain', 'taskkill_tree'): N, ('plain', 'jobclose'): N,
    ('plain', 'jobclose_nobreak'): N,
    ('detached', 'exit'): Y, ('detached', 'taskkill_tree'): N, ('detached', 'jobclose'): N,
    ('detached', 'jobclose_nobreak'): N,
    ('breakaway', 'exit'): Y, ('breakaway', 'taskkill_tree'): N, ('breakaway', 'jobclose'): Y,
    ('breakaway', 'jobclose_nobreak'): 'spawn_failed',
    ('double', 'exit'): Y, ('double', 'taskkill_tree'): Y, ('double', 'jobclose'): N,
    ('double', 'jobclose_nobreak'): N,
    ('wmi', 'exit'): Y, ('wmi', 'taskkill_tree'): Y, ('wmi', 'jobclose'): Y,
    ('wmi', 'jobclose_nobreak'): Y,
    ('robust', 'exit'): Y, ('robust', 'taskkill_tree'): Y, ('robust', 'jobclose'): Y,
    ('robust', 'jobclose_nobreak'): 'refused',
}


def _make_job(breakaway_ok, kill_on_close=True):
    import ctypes
    from ctypes import wintypes
    k = ctypes.windll.kernel32
    k.CreateJobObjectW.restype = wintypes.HANDLE
    h = k.CreateJobObjectW(None, None)

    class BASIC(ctypes.Structure):
        _fields_ = [("a", ctypes.c_int64), ("b", ctypes.c_int64), ("LimitFlags", wintypes.DWORD),
                    ("c", ctypes.c_size_t), ("d", ctypes.c_size_t), ("e", wintypes.DWORD),
                    ("f", ctypes.c_size_t), ("g", wintypes.DWORD), ("h", wintypes.DWORD)]

    class IOC(ctypes.Structure):
        _fields_ = [("v", ctypes.c_uint64 * 6)]

    class EXT(ctypes.Structure):
        _fields_ = [("Basic", BASIC), ("Io", IOC), ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]
    e = EXT()
    e.Basic.LimitFlags = ((0x2000 if kill_on_close else 0)         # KILL_ON_JOB_CLOSE
                          | (0x800 if breakaway_ok else 0))        # BREAKAWAY_OK
    k.SetInformationJobObject(h, 9, ctypes.byref(e), ctypes.sizeof(e))
    return h


def _assign_job(job, pid):
    import ctypes
    from ctypes import wintypes
    k = ctypes.windll.kernel32
    k.OpenProcess.restype = wintypes.HANDLE
    hp = k.OpenProcess(0x1F0FFF, False, pid)
    ok = k.AssignProcessToJobObject(job, hp)
    k.CloseHandle(hp)
    return bool(ok)


def _run_cell(root, variant, kill):
    """One cell of the matrix. Returns a dict; kills only the PIDs it started."""
    d = root / f'{variant}-{kill}'
    d.mkdir(parents=True)
    tr = Tracker()
    res = {'variant': variant, 'kill': kill}
    try:
        # Every Windows cell gets its OWN job, so the result does not depend on the job
        # this pytest process happens to inherit (an agent sandbox's job forbids
        # breakaway: WinError 5 on cells that mean "no job"). exit/taskkill cells get
        # a breakaway-OK job that is never closed; jobclose cells close it.
        in_job = WIN
        closes_job = kill in ('jobclose', 'jobclose_nobreak')
        # the venv python.exe is a launcher stub with its own Job; use the real interpreter
        srv_py = PY_BASE if WIN else sys.executable
        srv = subprocess.Popen([srv_py, STANDIN, 'server', str(d), variant,
                                '1' if kill == 'exit' else '0'],
                               **({'creationflags': 0x08000000 | 0x00000200} if WIN
                                  else {'start_new_session': True}))
        srv_ct = shim.proc_create_time(srv.pid)
        tr.add(srv.pid, srv_ct)
        job = None
        if in_job:
            job = _make_job(breakaway_ok=(kill != 'jobclose_nobreak'),
                            kill_on_close=closes_job)
            assert _assign_job(job, srv.pid)
        spec = C.build_spec(argv=child_argv(), cwd=None, mode='interactive', session_id=d.name,
                            owner_pid=srv.pid, timers=C.timers_from_config({}, tick_s=1.0))
        C.write_spec(d, spec)
        (d / 'go').write_text('1')
        if not wait_for(lambda: (d / 'server.json').exists(), 60):
            res['result'] = 'server_timeout'
            return res
        sj = json.loads((d / 'server.json').read_text())
        res['server'] = sj
        res['server_log'] = (d / 'server.log').read_text() if (d / 'server.log').exists() else ''
        if not sj['ok']:
            res['result'] = 'refused' if sj['branch'] == C.BRANCH_WIN_REFUSED else 'spawn_failed'
            res['shim_json_exists'] = (d / 'shim.json').exists()
            return res
        m = wait_for(lambda: (C.read_json(d / 'shim.json') or {}).get('launch_id') == spec['launch_id']
                     and C.read_json(d / 'shim.json'), 60)
        if not m:
            res['result'] = 'spawn_failed'
            return res
        tr.add(m['shim_pid'], m['shim_ct'])
        tr.add(m['cli_pid'], m['cli_ct'])
        time.sleep(1.5)
        before = nlines(d)
        # ── kill the server the way each real path does ──
        if kill == 'exit':
            srv.wait(timeout=30)
        elif kill == 'taskkill_tree':
            if WIN:
                subprocess.run(['taskkill', '/F', '/T', '/PID', str(srv.pid)], capture_output=True)
        elif closes_job:
            import ctypes
            ctypes.windll.kernel32.CloseHandle(job)
        time.sleep(3)
        shim_ok, cli_ok = alive_ct(m['shim_pid'], m['shim_ct']), alive_ct(m['cli_pid'], m['cli_ct'])
        res.update(shim_alive=shim_ok, cli_alive=cli_ok, log_grew=nlines(d) > before)
        if shim_ok and cli_ok:
            cl = C.ShimClient(d, owner_pid=os.getpid())
            try:
                cl.connect()
                cl.send(b'via-new-owner\n')
                res['roundtrip'] = bool(wait_for(lambda: 'echo:via-new-owner' in log_text(d), 10))
            except C.ShimError as e:
                res['roundtrip'] = f'ERR {e}'
            finally:
                cl.close()
        res['result'] = Y if (shim_ok and cli_ok) else N
        return res
    finally:
        tr.reap()


def _run_cell_posix(root, variant, kill):
    d = root / f'{variant}-{kill}'
    d.mkdir(parents=True)
    tr = Tracker()
    res = {'variant': variant, 'kill': kill}
    try:
        srv = subprocess.Popen([sys.executable, STANDIN, 'server', str(d), variant, '0'],
                               start_new_session=True)
        tr.add(srv.pid, shim.proc_create_time(srv.pid))
        spec = C.build_spec(argv=child_argv(), cwd=None, mode='interactive', session_id=d.name,
                            owner_pid=srv.pid, timers=C.timers_from_config({}, tick_s=1.0))
        C.write_spec(d, spec)
        (d / 'go').write_text('1')
        m = wait_for(lambda: (C.read_json(d / 'shim.json') or {}).get('launch_id') == spec['launch_id']
                     and C.read_json(d / 'shim.json'), 60)
        if not m:
            res['result'] = 'spawn_failed'
            return res
        tr.add(m['shim_pid'], m['shim_ct'])
        tr.add(m['cli_pid'], m['cli_ct'])
        time.sleep(1.5)
        before = nlines(d)
        if kill == 'killpg':
            os.killpg(srv.pid, signal.SIGKILL)         # the server's own process group
        else:                                          # 'walk': what proc_kill.kill_tree falls back to
            for pid in _descendants(srv.pid):
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass
            os.kill(srv.pid, signal.SIGKILL)
        srv.wait(timeout=10)
        time.sleep(2.5)
        shim_ok, cli_ok = alive_ct(m['shim_pid'], m['shim_ct']), alive_ct(m['cli_pid'], m['cli_ct'])
        res.update(shim_alive=shim_ok, cli_alive=cli_ok, log_grew=nlines(d) > before)
        if shim_ok and cli_ok:
            cl = C.ShimClient(d, owner_pid=os.getpid())
            try:
                cl.connect()
                cl.send(b'via-new-owner\n')
                res['roundtrip'] = bool(wait_for(lambda: 'echo:via-new-owner' in log_text(d), 10))
            finally:
                cl.close()
        res['result'] = Y if (shim_ok and cli_ok) else N
        return res
    finally:
        tr.reap()


def _descendants(root):
    kids = {}
    for p in os.listdir('/proc'):
        if p.isdigit():
            try:
                pp = int(open(f'/proc/{p}/stat').read().rsplit(')', 1)[1].split()[1])
                kids.setdefault(pp, []).append(int(p))
            except (OSError, ValueError, IndexError):
                pass
    out, st = [], list(kids.get(root, []))
    while st:
        c = st.pop()
        out.append(c)
        st += kids.get(c, [])
    return out


@pytest.fixture(scope='module')
def win_matrix(tmp_path_factory):
    root = tmp_path_factory.mktemp('shim_matrix_win')
    cells = [(v, k) for v in _WIN_VARIANTS for k in _WIN_KILLS]
    with ThreadPoolExecutor(max_workers=6) as ex:
        results = list(ex.map(lambda c: _run_cell(root, *c), cells))
    return {(r['variant'], r['kill']): r for r in results}


@windows_only
@needs_psutil
@pytest.mark.parametrize('variant,kill', [(v, k) for v in _WIN_VARIANTS for k in _WIN_KILLS],
                         ids=lambda x: str(x))
def test_survival_matrix_windows(win_matrix, variant, kill):
    r = win_matrix[(variant, kill)]
    expect = WIN_EXPECT[(variant, kill)]
    assert r['result'] == expect, r
    if expect == Y:
        assert r['log_grew'] and r['roundtrip'] is True, r      # still alive AND still driveable
    if expect == N:
        assert not r['shim_alive'] and not r['cli_alive'], r
    if expect == 'refused':
        # the production recipe took the refuse branch, logged it, and left nothing running
        assert 'branch=win_breakaway_refused' in r['server_log'], r
        assert r['shim_json_exists'] is False, r
    if expect == 'spawn_failed':
        assert 'denied' in (r['server'].get('err') or '').lower(), r


_POSIX_CELLS = [('setsid', 'killpg'), ('setsid', 'walk'), ('robust', 'killpg'), ('robust', 'walk')]
# Measured on WSL2 Ubuntu (design section 2): a setsid child survives killpg of
# the server's group but dies to a descendants walk; the trampoline (double
# fork) survives both.
POSIX_EXPECT = {('setsid', 'killpg'): Y, ('setsid', 'walk'): N,
                ('robust', 'killpg'): Y, ('robust', 'walk'): Y}


@pytest.fixture(scope='module')
def posix_matrix(tmp_path_factory):
    root = tmp_path_factory.mktemp('shim_matrix_posix')
    with ThreadPoolExecutor(max_workers=4) as ex:
        results = list(ex.map(lambda c: _run_cell_posix(root, *c), _POSIX_CELLS))
    return {(r['variant'], r['kill']): r for r in results}


@pytest.mark.skipif(not POSIX, reason='POSIX half of the matrix')
@pytest.mark.parametrize('variant,kill', _POSIX_CELLS, ids=lambda x: str(x))
def test_survival_matrix_posix(posix_matrix, variant, kill):
    r = posix_matrix[(variant, kill)]
    assert r['result'] == POSIX_EXPECT[(variant, kill)], r
    if r['result'] == Y:
        assert r['log_grew'] and r['roundtrip'] is True, r
