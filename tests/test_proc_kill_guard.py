"""Guard tests for mc/proc_kill.py (backlog 7cc8f7bc).

Bug: on Linux/macOS an agent CLI child shared the server's process group, so
``_kill_pid(pid, tree=True)`` -> ``os.killpg(os.getpgid(pid), 9)`` SIGKILLed the
server along with the agent (Stop button). Two layers pinned here:

* the guard — no killpg ever targets ``os.getpgrp()`` (unit, any platform)
* the root fix — agent/CLI Popen sites carry ``**POPEN_NEW_SESSION`` (static)
* a REAL POSIX run through the real ``agent_routes._kill_pid`` helper
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc import proc_kill  # noqa: E402

SERVER_PGRP = 4000
ME = 4000


@pytest.fixture
def posix(monkeypatch):
    """Pretend to be POSIX with a fake process table; record killpg/kill."""
    monkeypatch.setattr(proc_kill, '_IS_WIN', False)
    calls = {'killpg': [], 'kill': []}
    pgids = {}
    monkeypatch.setattr(os, 'getpgrp', lambda: SERVER_PGRP, raising=False)
    monkeypatch.setattr(os, 'getpid', lambda: ME)
    monkeypatch.setattr(os, 'getpgid', lambda pid: pgids[pid], raising=False)
    monkeypatch.setattr(os, 'killpg', lambda g, s: calls['killpg'].append((g, s)),
                        raising=False)
    monkeypatch.setattr(os, 'kill', lambda p, s: calls['kill'].append((p, s)))
    # 1 init -> 3999 launcher -> 4000 server -> 4100 agent -> 4101 node -> 4102 mcp
    #                                        \-> 4200 unrelated sibling
    table = {3999: 1, 4000: 3999, 4100: 4000, 4101: 4100, 4102: 4101, 4200: 4000}
    monkeypatch.setattr(proc_kill, '_ppid_map', lambda: dict(table))
    return calls, pgids


def test_never_killpg_the_servers_own_group(posix):
    calls, pgids = posix
    pgids[4100] = SERVER_PGRP  # legacy child: shares the server's group
    assert proc_kill.kill_tree(4100) is True
    assert calls['killpg'] == []                       # the bright line
    assert sorted(p for p, _ in calls['kill']) == [4100, 4101, 4102]
    assert 4000 not in [p for p, _ in calls['kill']]   # server survives
    assert 4200 not in [p for p, _ in calls['kill']]   # sibling survives


def test_safe_killpg_refuses_servers_group(posix):
    calls, pgids = posix
    pgids[4100] = SERVER_PGRP
    assert proc_kill.safe_killpg(4100, 28) is False
    assert calls['killpg'] == [] and calls['kill'] == []


def test_refuses_a_group_the_target_does_not_lead(posix):
    calls, pgids = posix
    pgids[4100] = 5555  # someone else's group, may hold unowned siblings
    assert proc_kill.kill_tree(4100) is True
    assert calls['killpg'] == []
    assert sorted(p for p, _ in calls['kill']) == [4100, 4101, 4102]


def test_own_group_leader_gets_one_killpg(posix):
    calls, pgids = posix
    pgids[4100] = 4100  # start_new_session child
    assert proc_kill.kill_tree(4100) is True
    assert calls['killpg'] == [(4100, 9)] and calls['kill'] == []


@pytest.mark.parametrize('victim', [ME, 3999])  # the server itself / its launcher
def test_refuses_self_and_ancestors(posix, victim):
    calls, pgids = posix
    pgids[victim] = SERVER_PGRP
    assert proc_kill.kill_tree(victim) is False
    assert calls['killpg'] == [] and calls['kill'] == []


def test_windows_is_a_noop(monkeypatch):
    monkeypatch.setattr(proc_kill, '_IS_WIN', True)
    assert proc_kill.kill_tree(4100) is False
    assert proc_kill.safe_killpg(4100) is False


def test_no_raw_killpg_outside_the_guard():
    offenders = []
    for path in list((PROJECT_ROOT / 'mc').rglob('*.py')) + [PROJECT_ROOT / 'server.py']:
        if path.name == 'proc_kill.py':
            continue
        for n, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
            if re.search(r'os\.killpg\(', line) and not line.lstrip().startswith(('#', '"', "'")):
                offenders.append(f'{path.relative_to(PROJECT_ROOT)}:{n}')
    assert offenders == []


def test_agent_spawn_sites_use_a_new_session():
    """Every Popen carrying the Windows startupinfo (the agent/CLI spawn shape)
    in the runtime + dispatch modules must also spread POPEN_NEW_SESSION."""
    missing = []
    for rel in ('mc/agent_runtime.py', 'mc/blueprints/agent_routes.py', 'server.py'):
        lines = (PROJECT_ROOT / rel).read_text(encoding='utf-8').splitlines()
        for i, line in enumerate(lines):
            if 'subprocess.Popen(' not in line or line.lstrip().startswith('#'):
                continue
            block = '\n'.join(lines[i:i + 25])
            end = block.find('startupinfo=_STARTUPINFO')
            if end == -1:
                continue  # not an agent-shape spawn (opener, terminal, etc.)
            call = block[:end + 80]
            if 'POPEN_NEW_SESSION' not in call:
                missing.append(f'{rel}:{i + 1}')
    assert missing == []


# ── real POSIX run ───────────────────────────────────────────────────────────

_CHILD = textwrap.dedent('''
    import subprocess, sys, time
    g = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    print(g.pid, flush=True)
    time.sleep(120)
''')


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    # a zombie is dead for our purposes (reaped by init / the parent's wait)
    try:
        with open(f'/proc/{pid}/stat') as f:
            return f.read().rsplit(')', 1)[1].split()[0] != 'Z'
    except OSError:
        return True


def _gone(*pids, timeout=5.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if not any(_alive(p) for p in pids):
            return True
        time.sleep(0.1)
    return False


@pytest.mark.skipif(sys.platform == 'win32', reason='POSIX process groups')
@pytest.mark.parametrize('new_session', [True, False],
                         ids=['start_new_session', 'legacy_shared_group'])
def test_real_tree_kill_leaves_the_caller_alive(new_session):
    from mc.blueprints import agent_routes
    kw = proc_kill.POPEN_NEW_SESSION if new_session else {}
    child = subprocess.Popen([sys.executable, '-c', _CHILD],
                             stdout=subprocess.PIPE, text=True, **kw)
    try:
        grandchild = int(child.stdout.readline())
        assert (os.getpgid(child.pid) == child.pid) is new_session
        if not new_session:
            # the exact shape of the bug: child shares THIS process's group
            assert os.getpgid(child.pid) == os.getpgrp()
        assert agent_routes._kill_pid(child.pid, tree=True) is True
        child.wait(timeout=5)
        assert _gone(child.pid, grandchild), 'child/grandchild survived tree-kill'
    finally:
        try:
            child.kill()
        except OSError:
            pass
    assert _alive(os.getpid())  # reaching this line IS the assertion


# ── agent_runtime._kill_pid (backlog 0941c443) ───────────────────────────────

@pytest.mark.skipif(sys.platform == 'win32', reason='POSIX process groups')
@pytest.mark.parametrize('new_session', [True, False],
                         ids=['start_new_session', 'legacy_shared_group'])
def test_runtime_kill_pid_reaps_the_grandchild_and_spares_the_caller(new_session):
    """The respawn/teardown helper used to os.kill(pid, 9) the CLI only, so its
    node/MCP grandchildren survived. Real processes, real helper."""
    from mc import agent_runtime
    kw = proc_kill.POPEN_NEW_SESSION if new_session else {}
    child = subprocess.Popen([sys.executable, '-c', _CHILD],
                             stdout=subprocess.PIPE, text=True, **kw)
    try:
        grandchild = int(child.stdout.readline())
        agent_runtime._kill_pid(child.pid)
        child.wait(timeout=5)
        assert _gone(child.pid, grandchild), 'grandchild survived _kill_pid'
    finally:
        try:
            child.kill()
        except OSError:
            pass
    assert _alive(os.getpid())  # reaching this line IS the assertion


def test_runtime_kill_pid_routes_posix_through_kill_tree(monkeypatch):
    from mc import agent_runtime
    monkeypatch.setattr(agent_runtime.sys, 'platform', 'linux')
    seen = []
    monkeypatch.setattr(agent_runtime._proc_kill, 'kill_tree',
                        lambda pid, *a: seen.append(pid) or True)
    raw = []
    monkeypatch.setattr(agent_runtime.os, 'kill', lambda p, s: raw.append((p, s)))
    agent_runtime._kill_pid(4100)
    assert seen == [4100] and raw == []  # no second, bare kill after a tree kill


def test_runtime_kill_pid_falls_back_to_bare_kill(monkeypatch):
    from mc import agent_runtime
    monkeypatch.setattr(agent_runtime.sys, 'platform', 'linux')
    monkeypatch.setattr(agent_runtime._proc_kill, 'kill_tree', lambda pid, *a: False)
    raw = []
    monkeypatch.setattr(agent_runtime.os, 'kill', lambda p, s: raw.append((p, s)))
    agent_runtime._kill_pid(4100)
    assert raw == [(4100, 9)]


def test_runtime_kill_pid_windows_still_taskkill_tree(monkeypatch):
    from mc import agent_runtime
    monkeypatch.setattr(agent_runtime.sys, 'platform', 'win32')
    ran = []
    monkeypatch.setattr(agent_runtime.subprocess, 'run',
                        lambda cmd, **kw: ran.append(cmd))
    monkeypatch.setattr(agent_runtime._proc_kill, 'kill_tree',
                        lambda *a: pytest.fail('kill_tree must not run on Windows'))
    agent_runtime._kill_pid(4100)
    assert ran == [['taskkill', '/F', '/T', '/PID', '4100']]
