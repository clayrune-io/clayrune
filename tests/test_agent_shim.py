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
        try:
            body = f.read_text(errors='replace')
        except (FileNotFoundError, PermissionError):
            # listed while the shim's atomic write was in flight: the `.<name>.tmp<pid>`
            # was renamed over its target (or is mid-replace) between iterdir and open.
            # The target file is checked on its own if it was listed; a vanished tmp
            # held the same bytes the replaced target now holds.
            assert f.name.startswith('.') and '.tmp' in f.name, f'{f.name} vanished'
            continue
        assert secret not in body, f'secret on disk in {f.name}'


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


def _read_json_retry(path):
    """Parsed JSON, or None while the file is absent / mid-replace (Windows reader
    race: PermissionError, FileNotFoundError, or a torn read)."""
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None


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
        # the stand-in writes server.json via temp + os.replace; on Windows a reader that
        # opens it mid-replace gets PermissionError, so retry until it parses.
        sj = wait_for(lambda: _read_json_retry(d / 'server.json'), 30)
        res['server'] = sj
        # a WMI-created shim is no one's child: track it by pid the moment it is known,
        # so a cell that fails before shim.json matches cannot leak it.
        if sj and sj.get('wmi_pid'):
            tr.add(sj['wmi_pid'], shim.proc_create_time(sj['wmi_pid']))
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
        tr.add_manifest(d)                 # whatever shim.json names, matched or not
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


# ───────────────── P1 fix round (Fenn review of 02593a43) ───────────────────
class _StubConn:
    """Just enough socket for Shim._reply / _claim_owner."""

    def __init__(self):
        self.replies = []

    def sendall(self, b):
        self.replies.append(b.decode().strip())

    def shutdown(self, how):
        pass


class _StubRF:
    def __init__(self, data, gate=None):
        self._data, self._gate = data, gate

    def read(self, n):
        if self._gate is not None:
            assert self._gate.wait(10)
        return self._data[:n]


class _StubStdin:
    def __init__(self, gate=None):
        self.writes, self._gate = [], gate
        self.entered = threading.Event()

    def write(self, data):
        self.entered.set()
        if self._gate is not None:
            assert self._gate.wait(10)
        self.writes.append(bytes(data))

    def flush(self):
        pass


def _stub_shim(tmp_path, stdin):
    from types import SimpleNamespace
    sh = shim.Shim(str(tmp_path))
    sh.cli = SimpleNamespace(pid=os.getpid(), stdin=stdin)
    return sh


def test_expiry_does_not_kill_a_pid_the_shim_already_reaped(tracker, tmp_path, monkeypatch):
    """Defect 1: the wait thread has reaped the CLI but is stalled writing
    exit.json, so `_exited` is still unset and the main loop reaches orphan_max.
    The old code tree-killed the (by now reusable) pid anyway."""
    sdir = C.session_dir(tmp_path, 'x1')
    spec = C.build_spec(argv=child_argv('--lines', '3', '--interval', '0.1'), cwd=None,
                        mode='oneshot', session_id='x1', owner_pid=0,
                        timers={'grace_s': 999, 'exit_grace_s': 999, 'orphan_max_s': 0.8, 'tick_s': 0.05})
    C.write_spec(sdir, spec)
    stalled, release, kills = threading.Event(), threading.Event(), []
    real_write = shim.write_json_atomic

    def slow_write(path, obj):
        if os.path.basename(path) == 'exit.json':
            stalled.set()
            assert release.wait(20)
        return real_write(path, obj)

    monkeypatch.setattr(shim, 'write_json_atomic', slow_write)
    monkeypatch.setattr(shim, 'kill_tree', lambda pid: kills.append(pid))
    sh = shim.Shim(str(sdir))
    t = threading.Thread(target=sh.run, daemon=True)
    t.start()
    try:
        assert stalled.wait(15), 'CLI never exited'          # reaped, exit.json write parked
        assert wait_for(lambda: 'orphan_max reached' in (sdir / 'shim.log').read_text(), 10)
        time.sleep(0.3)                                       # let the loop act on the expiry verdict
        assert kills == [], f'expiry killed pid {kills} after the CLI was reaped'
    finally:
        release.set()
        t.join(15)
    assert (C.read_json(sdir / 'exit.json') or {}).get('reason') == 'exit'   # not mislabelled 'expired'


def test_expiry_still_kills_a_live_cli(tracker, tmp_path, monkeypatch):
    """The guard must not neuter expiry: a live, unreaped CLI is still killed."""
    sh = shim.Shim(str(tmp_path))
    p = subprocess.Popen([PY_BASE, '-c', 'import time; time.sleep(60)'])
    tracker.add(p.pid, shim.proc_create_time(p.pid))
    sh.cli, sh.cli_ct = p, shim.proc_create_time(p.pid)
    kills = []
    monkeypatch.setattr(shim, 'kill_tree', lambda pid: kills.append(pid))
    assert sh._expire_cli() is True and kills == [p.pid] and sh._exit_reason == 'expired'
    sh2 = shim.Shim(str(tmp_path))                            # identity mismatch: reused pid
    sh2.cli, sh2.cli_ct = p, shim.proc_create_time(p.pid) + 3600
    assert sh2._expire_cli() is False and kills == [p.pid]
    p.kill()
    p.wait()


def test_failed_hello_after_launch_tears_down_the_started_cli(tracker, tmp_path, monkeypatch):
    """Defect 2: launch() returned ok=False with the shim and CLI still running,
    so the documented fallback started a second CLI."""
    def refuse(self):
        raise C.ShimUnavailable('handshake failed: injected')

    monkeypatch.setattr(C.ShimClient, 'connect', refuse)
    res = C.launch(tmp_path, 'h1', argv=child_argv(), cwd=None, mode='interactive', tick_s=0.1)
    sdir = C.session_dir(tmp_path, 'h1')
    m = C.read_json(sdir / 'shim.json')
    tracker.add_manifest(sdir)                                 # safety net only
    assert m and m['cli_pid'] and m['shim_pid']
    assert res.ok is False and res.cli_running is False and 'injected' in res.error
    assert not alive_ct(m['cli_pid'], m['cli_ct']), 'CLI left running after ok=False'
    assert not alive_ct(m['shim_pid'], m['shim_ct']), 'shim left running after ok=False'


def test_failed_teardown_is_not_reported_as_nothing_started(tracker, tmp_path, monkeypatch):
    def refuse(self):
        raise C.ShimUnavailable('handshake failed: injected')

    monkeypatch.setattr(C.ShimClient, 'connect', refuse)
    monkeypatch.setattr(C, '_teardown_started', lambda m, timeout=5.0, log=None: False)
    res = C.launch(tmp_path, 'h2', argv=child_argv(), cwd=None, mode='interactive', tick_s=0.1)
    tracker.add_manifest(C.session_dir(tmp_path, 'h2'))
    assert res.ok is False and res.cli_running is True and 'teardown incomplete' in res.error


def test_teardown_with_unavailable_liveness_lookup_is_not_reported_gone(tracker, tmp_path, monkeypatch):
    """Fenn round 2: a failed live identity lookup was counted as 'gone', so
    launch() returned cli_running=False while the shim and CLI were still alive
    and the caller took the fallback. Unknown liveness must read as running."""
    def refuse(self):
        raise C.ShimUnavailable('handshake failed: injected')

    monkeypatch.setattr(C.ShimClient, 'connect', refuse)
    real = shim.proc_create_time
    seen = {}
    orig_teardown = C._teardown_started
    monkeypatch.setattr(C, '_TEARDOWN_RETRY_DEADLINE_S', 0.0)  # no background retry in this test

    def teardown_with_lookup_down(m, timeout=5.0, log=None):
        seen['m'] = m
        monkeypatch.setattr(shim, 'proc_create_time', lambda pid: None)   # lookup unavailable
        monkeypatch.setattr(shim, '_win_create_time_from_handle', lambda k, h: None)   # ...incl. the held-handle reader
        return orig_teardown(m, timeout=0.3, log=log)

    monkeypatch.setattr(C, '_teardown_started', teardown_with_lookup_down)
    res = C.launch(tmp_path, 'h3', argv=child_argv(), cwd=None, mode='interactive', tick_s=0.1)
    m = seen['m']
    tracker.add_manifest(C.session_dir(tmp_path, 'h3'))        # safety net: kills them after
    monkeypatch.setattr(shim, 'proc_create_time', real)
    monkeypatch.undo()
    assert alive_ct(m['cli_pid'], m['cli_ct']) and alive_ct(m['shim_pid'], m['shim_ct'])
    assert res.ok is False and res.cli_running is True and 'teardown incomplete' in res.error


def test_teardown_retry_cleans_up_once_the_liveness_lookup_recovers(tracker, tmp_path, monkeypatch):
    """Fenn round 3 (1): teardown that ends on 'unknown' skipped the kill (right)
    but never retried, so after the lookup recovered both processes lived on.
    The bounded retry must kill them once they read 'ours' -- and only then."""
    def refuse(self):
        raise C.ShimUnavailable('handshake failed: injected')

    monkeypatch.setattr(C.ShimClient, 'connect', refuse)
    monkeypatch.setattr(C, '_TEARDOWN_RETRY_INTERVAL_S', 0.1)
    monkeypatch.setattr(C, '_TEARDOWN_RETRY_DEADLINE_S', 20.0)
    real, seen, orig_teardown = shim.proc_create_time, {}, C._teardown_started
    real_h = shim._win_create_time_from_handle

    def teardown_with_lookup_down(m, timeout=5.0, log=None):
        seen['m'] = m
        monkeypatch.setattr(shim, 'proc_create_time', lambda pid: None)   # lookup unavailable
        monkeypatch.setattr(shim, '_win_create_time_from_handle', lambda k, h: None)   # ...incl. the held-handle reader
        return orig_teardown(m, timeout=0.3, log=log)

    monkeypatch.setattr(C, '_teardown_started', teardown_with_lookup_down)
    res = C.launch(tmp_path, 'h4', argv=child_argv(), cwd=None, mode='interactive', tick_s=0.1)
    m = seen['m']
    tracker.add_manifest(C.session_dir(tmp_path, 'h4'))        # safety net only
    assert res.cli_running is True and 'teardown incomplete' in res.error
    time.sleep(0.5)                                            # several retry passes, lookup still down
    assert alive_ct(m['cli_pid'], m['cli_ct']) and alive_ct(m['shim_pid'], m['shim_ct']),         'retry killed a process whose liveness was unknown'
    monkeypatch.setattr(shim, 'proc_create_time', real)        # lookup recovers
    monkeypatch.setattr(shim, '_win_create_time_from_handle', real_h)
    assert wait_for(lambda: not alive_ct(m['cli_pid'], m['cli_ct'])
                    and not alive_ct(m['shim_pid'], m['shim_ct']), 15), 'retry never cleaned up'


def test_teardown_retry_logs_survivors_at_the_deadline(tracker, monkeypatch):
    """Two live children whose liveness lookup is down ('unknown'): the retry
    must not signal them, and must log both at the deadline."""
    a, b = sleeper(tracker), sleeper(tracker)
    pairs = [(a.pid, shim.proc_create_time(a.pid)), (b.pid, shim.proc_create_time(b.pid))]
    monkeypatch.setattr(shim, 'proc_create_time', lambda pid: None)       # lookup down
    monkeypatch.setattr(shim, '_win_create_time_from_handle', lambda k, h: None)
    lines = []
    C._teardown_retry(pairs, 0.05, 0.2, lines.append)
    monkeypatch.undo()
    assert a.poll() is None and b.poll() is None, 'retry killed a process whose liveness was unknown'
    assert len(lines) == 1
    assert f'pid={a.pid} state=unknown' in lines[0] and f'pid={b.pid} state=unknown' in lines[0]
    a.kill(); b.kill(); a.wait(); b.wait()


def _pid_reused_once_dead(monkeypatch, first):
    """Every live identity read taken AFTER `first` has exited looks like a
    different process (a reused PID). Patches both readers: the stdlib one and
    the Windows held-handle one."""
    real_ct, real_h = shim.proc_create_time, shim._win_create_time_from_handle

    def shifted(v):
        return v + 3600 if (v is not None and first.poll() is not None) else v

    monkeypatch.setattr(shim, 'proc_create_time', lambda pid: shifted(real_ct(pid)))
    monkeypatch.setattr(shim, '_win_create_time_from_handle', lambda k, h: shifted(real_h(k, h)))


def test_teardown_does_not_kill_a_shim_pid_reused_during_the_cli_kill(tracker, monkeypatch):
    """Fenn round 4 P1-B: both identities were read once, before either kill; if
    the shim exited and its PID was reused while the CLI kill was blocking, the
    second kill hit the replacement. 'b' stands in for the replacement: live, but
    once 'a' (the CLI) is dead its identity no longer matches the manifest."""
    a, b = sleeper(tracker), sleeper(tracker)
    m = {'cli_pid': a.pid, 'cli_ct': shim.proc_create_time(a.pid),
         'shim_pid': b.pid, 'shim_ct': shim.proc_create_time(b.pid)}
    _pid_reused_once_dead(monkeypatch, a)
    C._teardown_started(m, timeout=1.0)
    a_dead = wait_for(lambda: a.poll() is not None, 10)
    monkeypatch.undo()
    assert a_dead, 'the CLI (identity intact) was not killed'
    assert b.poll() is None, 'teardown killed a process that had reused the shim pid'
    b.kill(); b.wait()


def test_teardown_retry_does_not_kill_a_pid_reused_during_the_pass(tracker, monkeypatch):
    a, b = sleeper(tracker), sleeper(tracker)
    pairs = [(a.pid, shim.proc_create_time(a.pid)), (b.pid, shim.proc_create_time(b.pid))]
    _pid_reused_once_dead(monkeypatch, a)
    C._teardown_retry(pairs, 0.05, 3.0, lambda s: None)
    a_dead = wait_for(lambda: a.poll() is not None, 10)
    monkeypatch.undo()
    assert a_dead
    assert b.poll() is None, 'retry killed a process that had reused the pid'
    b.kill(); b.wait()


def test_kill_tree_if_ours_refuses_wrong_missing_and_unknown_identity(tracker, monkeypatch):
    p = sleeper(tracker)
    ct = shim.proc_create_time(p.pid)
    assert shim.kill_tree_if_ours(p.pid, ct + 3600) is False       # reused pid
    assert shim.kill_tree_if_ours(p.pid, None) is False            # no recorded identity
    assert shim.kill_tree_if_ours(None, ct) is False and shim.kill_tree_if_ours(0, ct) is False
    monkeypatch.setattr(shim, 'proc_create_time', lambda pid: None)   # lookup down = unknown
    monkeypatch.setattr(shim, '_win_create_time_from_handle', lambda k, h: None)
    assert shim.kill_tree_if_ours(p.pid, ct) is False
    monkeypatch.undo()
    assert p.poll() is None
    q = subprocess.Popen([PY_BASE, '-c', 'pass'])
    q.wait()
    assert shim.kill_tree_if_ours(q.pid, ct) is False              # gone
    p.kill(); p.wait()


def test_kill_tree_if_ours_kills_a_real_child_and_its_descendants(tracker):
    """Windows: held handle + taskkill /T. POSIX: pidfd (or the re-checked
    fallback) to the root, killpg for the group the child leads."""
    code = ('import subprocess,sys,time;'
            'g=subprocess.Popen([sys.executable,"-c","import time;time.sleep(120)"]);'
            'print(g.pid,flush=True);time.sleep(120)')
    kw = {'start_new_session': True} if POSIX else {}
    p = subprocess.Popen([PY_BASE, '-c', code], stdout=subprocess.PIPE, text=True, **kw)
    ct = shim.proc_create_time(p.pid)
    tracker.add(p.pid, ct)
    gpid = int(p.stdout.readline())
    gct = shim.proc_create_time(gpid)
    tracker.add(gpid, gct)
    assert shim.kill_tree_if_ours(p.pid, ct) is True
    p.wait(timeout=10)
    assert wait_for(lambda: not alive_ct(gpid, gct), 10), 'descendant survived the tree kill'
    p.stdout.close()


@pytest.mark.skipif(not hasattr(os, 'pidfd_open'), reason='needs Linux 5.3+ pidfd')
def test_kill_tree_if_ours_uses_pidfd_when_available(tracker, monkeypatch):
    p = sleeper(tracker)
    ct = shim.proc_create_time(p.pid)
    sent, real = [], signal.pidfd_send_signal
    monkeypatch.setattr(signal, 'pidfd_send_signal', lambda fd, sig: (sent.append(sig), real(fd, sig))[1])
    assert shim.kill_tree_if_ours(p.pid, ct) is True and sent == [signal.SIGKILL]
    p.wait(timeout=10)


@pytest.mark.skipif(not POSIX, reason='POSIX fallback path')
def test_kill_tree_if_ours_fallback_without_pidfd_rechecks_before_kill(tracker, monkeypatch):
    monkeypatch.delattr(shim.os, 'pidfd_open', raising=False)
    p = sleeper(tracker)
    ct = shim.proc_create_time(p.pid)
    assert shim.kill_tree_if_ours(p.pid, ct + 3600) is False and p.poll() is None
    assert shim.kill_tree_if_ours(p.pid, ct) is True
    p.wait(timeout=10)


def _pidfd_open_raising(monkeypatch, err):
    """Linux POSIX path on any host: pidfd_open raises `err`, identity says 'ours'."""
    import errno as _errno
    from unittest.mock import MagicMock
    monkeypatch.setattr(shim.sys, 'platform', 'linux')
    monkeypatch.setattr(shim.signal, 'SIGKILL', 9, raising=False)
    monkeypatch.setattr(shim.os, 'pidfd_open', MagicMock(side_effect=OSError(getattr(_errno, err), err)), raising=False)
    monkeypatch.setattr(shim.signal, 'pidfd_send_signal', MagicMock(), raising=False)
    identity = MagicMock(return_value='ours')
    monkeypatch.setattr(shim, 'process_state', identity)
    monkeypatch.setattr(shim.os, 'getpgid', MagicMock(return_value=12345), raising=False)
    monkeypatch.setattr(shim.os, 'getpgrp', MagicMock(return_value=999), raising=False)
    killpg, kill = MagicMock(), MagicMock()
    monkeypatch.setattr(shim.os, 'killpg', killpg, raising=False)
    monkeypatch.setattr(shim.os, 'kill', kill, raising=False)
    return identity, killpg, kill


@pytest.mark.parametrize('err', ['ENOSYS', 'EPERM'])
def test_pidfd_open_unusable_syscall_falls_back_to_the_identity_checked_kill(monkeypatch, err):
    """Fenn round 5 P2: Python exposed pidfd_open on a kernel without the syscall
    (ENOSYS), the helper returned False, and teardown never killed anything."""
    identity, killpg, kill = _pidfd_open_raising(monkeypatch, err)
    assert shim.kill_tree_if_ours(12345, 100.0) is True
    assert identity.call_count == 1 and killpg.call_count == 1 and kill.call_count == 0


@pytest.mark.parametrize('err', ['ESRCH', 'EINVAL'])
def test_pidfd_open_gone_or_invalid_still_refuses(monkeypatch, err):
    identity, killpg, kill = _pidfd_open_raising(monkeypatch, err)
    assert shim.kill_tree_if_ours(12345, 100.0) is False
    assert identity.call_count == 0 and killpg.call_count == 0 and kill.call_count == 0


def test_pidfd_unusable_fallback_still_refuses_a_foreign_process(monkeypatch):
    identity, killpg, kill = _pidfd_open_raising(monkeypatch, 'ENOSYS')
    identity.return_value = 'unknown'
    assert shim.kill_tree_if_ours(12345, 100.0) is False
    assert killpg.call_count == 0 and kill.call_count == 0


def _timed_out_launch(monkeypatch, write):
    """launch() whose wait_ready times out; `write(sdir, spec)` plants whatever
    the shim left behind."""
    monkeypatch.setattr(C, 'spawn_detached', lambda sdir, **kw: C.SpawnResult(True, C.BRANCH_POSIX))

    def fake_wait_ready(sdir, launch_id, timeout=20.0, poll=0.05):
        write(Path(sdir), C.read_json(Path(sdir) / 'spec.json'))
        return None

    monkeypatch.setattr(C, 'wait_ready', fake_wait_ready)


def test_ready_timeout_with_no_manifest_is_not_reported_as_nothing_started(monkeypatch, tmp_path):
    """c545bf76: a slow shim can start its CLI after the timeout; ok=False with
    cli_running=False told the caller to start a second CLI."""
    _timed_out_launch(monkeypatch, lambda d, spec: None)
    res = C.launch(tmp_path, 't1', argv=child_argv(), cwd=None, mode='interactive')
    assert res.ok is False and res.cli_running is True and 'no manifest' in res.error


@pytest.mark.parametrize('gone, running', [(True, False), (False, True)])
def test_ready_timeout_with_a_late_manifest_runs_the_identity_checked_teardown(monkeypatch, tmp_path, gone, running):
    seen = []

    def late_manifest(d, spec):
        C.write_json_atomic(d / 'shim.json', {'schema': shim.SCHEMA, 'launch_id': spec['launch_id'],
                                              'shim_pid': 4242, 'shim_ct': 1.0, 'cli_pid': 4243, 'cli_ct': 2.0})

    _timed_out_launch(monkeypatch, late_manifest)
    monkeypatch.setattr(C, '_teardown_started', lambda m, timeout=5.0, log=None: (seen.append(m), gone)[1])
    res = C.launch(tmp_path, 't2', argv=child_argv(), cwd=None, mode='interactive')
    assert [m['cli_pid'] for m in seen] == [4243]
    assert res.ok is False and res.cli_running is running
    assert ('teardown incomplete' in res.error) is running


def test_ready_timeout_ignores_a_manifest_from_another_launch(monkeypatch, tmp_path):
    seen = []
    _timed_out_launch(monkeypatch, lambda d, spec: C.write_json_atomic(
        d / 'shim.json', {'schema': shim.SCHEMA, 'launch_id': 'someone-else', 'shim_pid': 1, 'shim_ct': 1.0}))
    monkeypatch.setattr(C, '_teardown_started', lambda m, timeout=5.0, log=None: (seen.append(m), True)[1])
    res = C.launch(tmp_path, 't3', argv=child_argv(), cwd=None, mode='interactive')
    assert seen == [] and res.ok is False and res.cli_running is True


def test_a_shim_reported_spawn_failure_keeps_cli_running_false(monkeypatch, tmp_path):
    """exit.json for THIS launch = the shim itself says it started no CLI."""
    _timed_out_launch(monkeypatch, lambda d, spec: C.write_json_atomic(
        d / 'exit.json', {'schema': shim.SCHEMA, 'launch_id': spec['launch_id'], 'rc': None, 'error': 'boom'}))
    res = C.launch(tmp_path, 't4', argv=child_argv(), cwd=None, mode='interactive')
    assert res.ok is False and res.cli_running is False and res.error == 'boom'


def test_identity_tolerance_is_tight_per_platform():
    assert shim._tolerance_for('win32') == 0.01
    assert shim._tolerance_for('linux') == 0.05
    assert shim._tolerance_for('darwin') == 2.0
    assert shim.IDENTITY_TOLERANCE_S == shim._tolerance_for(sys.platform)
    import inspect
    for fn in (shim.same_process, shim.process_state, shim.kill_tree_if_ours):
        assert inspect.signature(fn).parameters['tol'].default == shim.IDENTITY_TOLERANCE_S


def test_a_pid_reused_shortly_after_the_recorded_process_is_not_ours():
    """Fenn round 4 P1-A: under the old flat 2 s tolerance a process that took the
    PID 1.5 s after the recorded one passed as the same process (Windows/Linux)."""
    me = os.getpid()
    ct = shim.proc_create_time(me)
    ours = shim.IDENTITY_TOLERANCE_S >= 1.5            # only the macOS fallback
    assert shim.same_process(me, ct - 1.5) is ours
    assert shim.process_state(me, ct - 1.5) == ('ours' if ours else 'gone')


def test_recorded_identity_matches_live_identity_within_the_tolerance(tracker, tmp_path):
    """The tight tolerance is only safe if the shim's stored create times come
    from the same reader as the live check; otherwise our own process reads as
    'gone'. Real shim, real CLI: the manifest values must match a fresh read."""
    res = start(tracker, tmp_path, 'tol1', '--lines', '1000', '--interval', '0.05',
                mode='interactive', owner_pid=os.getpid())
    m = res.proc.manifest
    for pid_k, ct_k in (('shim_pid', 'shim_ct'), ('cli_pid', 'cli_ct')):
        live = shim.proc_create_time(m[pid_k])
        assert live is not None and m[ct_k] is not None
        assert abs(live - m[ct_k]) <= shim.IDENTITY_TOLERANCE_S, (pid_k, live - m[ct_k])
        assert shim.process_state(m[pid_k], m[ct_k]) == 'ours'
    res.proc.kill()


def test_pid_absent_posix_needs_esrch(monkeypatch):
    """Fenn round 3 (2): hidepid=2 hides other users' live processes from /proc,
    so a missing /proc entry must not read as absent; only ESRCH does."""
    import builtins
    monkeypatch.setattr(shim.sys, 'platform', 'linux')
    real_open = builtins.open
    state = {'stat': FileNotFoundError(2, 'hidden')}

    def fake_open(path, *a, **k):
        if str(path).startswith('/proc/'):
            if isinstance(state['stat'], Exception):
                raise state['stat']
            import io
            return io.StringIO(state['stat'])
        return real_open(path, *a, **k)

    monkeypatch.setattr(builtins, 'open', fake_open)

    def kill_raises(exc):
        def _k(pid, sig):
            if exc is not None:
                raise exc
        return _k

    monkeypatch.setattr(shim.os, 'kill', kill_raises(ProcessLookupError()))
    assert shim._pid_absent(4242) is True                                   # ESRCH
    monkeypatch.setattr(shim.os, 'kill', kill_raises(PermissionError()))
    assert shim._pid_absent(4242) is False                                  # EPERM: exists
    monkeypatch.setattr(shim.os, 'kill', kill_raises(None))
    assert shim._pid_absent(4242) is False                                  # hidden /proc entry, signalable
    state['stat'] = '4242 (x) S 1 2 3'
    assert shim._pid_absent(4242) is False                                  # live, readable
    state['stat'] = '4242 (x) Z 1 2 3'
    assert shim._pid_absent(4242) is True                                   # zombie
    monkeypatch.setattr(shim.sys, 'platform', 'darwin')
    monkeypatch.setattr(shim.os, 'kill', kill_raises(ProcessLookupError()))
    assert shim._pid_absent(4242) is True
    monkeypatch.setattr(shim.os, 'kill', kill_raises(PermissionError()))
    assert shim._pid_absent(4242) is False


def test_shimproc_poll_does_not_infer_exit_from_a_failed_lookup(tmp_path, monkeypatch):
    """Fenn round 3 (3): poll() read an unavailable liveness lookup as both
    processes gone and reported rc=-1 for a running CLI."""
    pr = C.ShimProc.__new__(C.ShimProc)
    pr.dir, pr.manifest, pr.returncode, pr.exit_inferred = tmp_path, {}, None, False
    pr.pid, pr.shim_pid, pr._cli_ct, pr._shim_ct = os.getpid(), os.getpid(), 1.0, 1.0
    monkeypatch.setattr(shim, 'proc_create_time', lambda pid: None)      # lookup down, pids alive
    assert pr.poll() is None and pr.exit_inferred is False
    monkeypatch.setattr(shim, 'process_state', lambda pid, ct, tol=2.0: 'gone')
    assert pr.poll() == -1 and pr.exit_inferred is True


def test_process_state_is_three_valued():
    me = os.getpid()
    ct = shim.proc_create_time(me)
    assert shim.process_state(me, ct) == 'ours'
    assert shim.process_state(me, ct + 3600) == 'gone'            # pid reused by another process
    assert shim.process_state(me, None) == 'unknown'              # alive, no identity to compare
    assert shim.process_state(None, None) == 'gone'
    p = subprocess.Popen(child_argv())
    p.kill()
    p.wait()
    assert shim.process_state(p.pid, ct) == 'gone'               # positively absent
    orig = shim.proc_create_time
    try:
        shim.proc_create_time = lambda pid: None
        assert shim.process_state(me, ct) == 'unknown'            # lookup down, process alive
    finally:
        shim.proc_create_time = orig


def test_buffered_send_from_a_displaced_owner_is_refused(tmp_path):
    """Defect 3a: old owner's SEND passed the pre-read ownership check, a
    takeover landed, then the SEND reached the write. It must not consume the
    seq; the new owner's payload for the same seq must be delivered."""
    stdin = _StubStdin()
    sh = _stub_shim(tmp_path, stdin)
    old, new = _StubConn(), _StubConn()
    sh.owner = shim._Owner(111, None, 'a', old)
    gate, out = threading.Event(), {}
    t = threading.Thread(target=lambda: out.setdefault(
        'r', sh._do_send(old, _StubRF(b'OLD\n', gate), ['SEND', '1', '4'])))
    t.start()
    assert sh._claim_owner(222, 'b', new) is None             # takeover while the SEND is buffered
    gate.set()
    t.join(10)
    assert out['r'] is False and old.replies == ['ERR not_owner']
    assert stdin.writes == [] and sh.last_seq == 0
    assert sh._do_send(new, _StubRF(b'NEW\n'), ['SEND', '1', '4']) is True
    assert stdin.writes == [b'NEW\n'] and new.replies == ['ACK 1']


def test_send_blocked_in_write_across_a_takeover_does_not_poison_the_seq(tmp_path):
    """Defect 3b: the old owner's write is already in flight (full pipe) when
    the takeover happens. The bytes go out, but last_seq must not advance, or
    the new owner's next SEND reads as a duplicate and is dropped."""
    gate = threading.Event()
    stdin = _StubStdin(gate)
    sh = _stub_shim(tmp_path, stdin)
    old, new = _StubConn(), _StubConn()
    sh.owner = shim._Owner(111, None, 'a', old)
    t_old = threading.Thread(target=sh._do_send, args=(old, _StubRF(b'OLD\n'), ['SEND', '1', '4']))
    t_old.start()
    assert stdin.entered.wait(10)                              # past the recheck, parked in write
    assert sh._claim_owner(222, 'b', new) is None
    t_new = threading.Thread(target=sh._do_send, args=(new, _StubRF(b'NEW\n'), ['SEND', '1', '4']))
    t_new.start()
    gate.set()
    t_old.join(10)
    t_new.join(10)
    assert stdin.writes == [b'OLD\n', b'NEW\n'], stdin.writes
    assert sh.last_seq == 1 and new.replies == ['ACK 1'] and old.replies == ['ERR not_owner']


def test_same_pid_reconnect_mid_write_keeps_the_seq(tmp_path):
    """Guard for 3b: a SAME-owner reconnect (lost ACK) must still advance
    last_seq, or the client's resend would write the payload twice."""
    gate = threading.Event()
    stdin = _StubStdin(gate)
    sh = _stub_shim(tmp_path, stdin)
    c1, c2 = _StubConn(), _StubConn()
    sh.owner = shim._Owner(111, None, 'a', c1)
    t = threading.Thread(target=sh._do_send, args=(c1, _StubRF(b'ONE\n'), ['SEND', '1', '4']))
    t.start()
    assert stdin.entered.wait(10)
    assert sh._claim_owner(111, 'a2', c2) is None
    gate.set()
    t.join(10)
    assert sh.last_seq == 1
    assert sh._do_send(c2, _StubRF(b'ONE\n'), ['SEND', '1', '4']) is True
    assert stdin.writes == [b'ONE\n'] and c2.replies == ['ACK 1']
