"""Backlog 40260b57: POST .../agent/dispatch must not trust a self-declared source.

Fenn (MC-1037 round 2) showed an agent's own `curl` could pose as the UI --
`{"source":"ui"}`, an `Origin` header, or `{"client":"mobile"}` -- and get a
child with trigger_type 'manual', i.e. unfenced. `mc/caller_attribution.py`
now maps the loopback peer port to its PID and walks the parent chain to a
managed session process; an attributed caller is never 'manual'.

Unit tests drive the module with a fake psutil (no real sockets/processes);
the route tests drive the real `agent_dispatch` handler with
`_dispatch_agent_internal` stubbed, so the assertion is on the exact
`trigger_type` the child would be launched with.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mc import caller_attribution as ca  # noqa: E402
from tests.test_agent_routes import client  # noqa: E402,F401  (Flask test client rig)

@pytest.fixture(autouse=True)
def _isolated_process_tracker():
    """tracked_processes leaks between tests and feeds managed_roots; snapshot,
    clear and restore it IN PLACE (never rebind)."""
    from mc import state as mc_state
    snap = dict(mc_state.tracked_processes)
    mc_state.tracked_processes.clear()
    try:
        yield
    finally:
        mc_state.tracked_processes.clear()
        mc_state.tracked_processes.update(snap)


SERVER_PORT = 5199
PEER_PORT = 51234
SESSION_PID = 4000
SHELL_PID = 4100
CURL_PID = 4200
OWN_PID = 999


class _Access(Exception):
    pass


class _Gone(Exception):
    pass


def _conn(laddr_port, raddr_port, pid):
    return SimpleNamespace(laddr=SimpleNamespace(port=laddr_port),
                           raddr=SimpleNamespace(port=raddr_port), pid=pid)


class FakePsutil:
    """Just the psutil surface caller_attribution touches."""

    def __init__(self, conns=(), parents=None, tree_conns=None, children=None,
                 system_wide_error=None):
        self._conns = list(conns)
        self._parents = parents or {}          # pid -> [parent pids, nearest first]
        self._tree_conns = tree_conns or {}    # pid -> [conn]
        self._children = children or {}        # pid -> [child pids, any depth]
        self._error = system_wide_error
        outer = self

        class Process:
            def __init__(self, pid):
                if pid not in outer._known():
                    raise _Gone(pid)
                self.pid = pid

            def parents(self):
                return [SimpleNamespace(pid=p) for p in outer._parents.get(self.pid, [])]

            def children(self, recursive=False):
                return [Process(p) for p in outer._children.get(self.pid, [])]

            def net_connections(self, kind='tcp'):
                v = outer._tree_conns.get(self.pid, [])
                if isinstance(v, Exception):
                    raise v
                return v

        self.Process = Process

    def _known(self):
        known = set(self._parents) | set(self._children) | set(self._tree_conns)
        for plist in self._parents.values():
            known.update(plist)
        for clist in self._children.values():
            known.update(clist)
        return known

    def net_connections(self, kind='tcp'):
        if self._error:
            raise self._error
        return self._conns


ROOTS = {SESSION_PID: 'sess-1'}
# claude(4000) -> bash(4100) -> curl(4200)
CHAIN = {CURL_PID: [SHELL_PID, SESSION_PID, 1], SHELL_PID: [SESSION_PID, 1], SESSION_PID: [1]}


def _attr(psutil_mod, roots=ROOTS, addr='127.0.0.1', port=PEER_PORT):
    return ca.attribute_caller(addr, port, SERVER_PORT, roots,
                               own_pid=OWN_PID, psutil_mod=psutil_mod)


def test_curl_under_a_session_is_attributed_to_that_session():
    ps = FakePsutil(conns=[_conn(PEER_PORT, SERVER_PORT, CURL_PID),
                           _conn(SERVER_PORT, PEER_PORT, OWN_PID)], parents=CHAIN)
    a = _attr(ps)
    assert (a.status, a.session_id, a.pid) == (ca.ATTRIBUTED, 'sess-1', SESSION_PID)


def test_ipv4_mapped_loopback_peer_is_still_loopback():
    ps = FakePsutil(conns=[_conn(PEER_PORT, SERVER_PORT, CURL_PID)], parents=CHAIN)
    assert _attr(ps, addr='::ffff:127.0.0.1').status == ca.ATTRIBUTED


def test_caller_with_no_managed_ancestor_is_unattributed():
    ps = FakePsutil(conns=[_conn(PEER_PORT, SERVER_PORT, 7000)], parents={7000: [6000, 1]})
    assert _attr(ps).status == ca.UNATTRIBUTED


def test_caller_that_descends_from_the_server_stops_there():
    # Server started from an agent's shell: webview -> server(999) -> bash -> claude.
    # The human's UI must not inherit the session via the server's own ancestry.
    ps = FakePsutil(conns=[_conn(PEER_PORT, SERVER_PORT, 7000)],
                    parents={7000: [OWN_PID, SHELL_PID, SESSION_PID, 1]})
    assert _attr(ps).status == ca.UNATTRIBUTED


def test_background_job_root_attributes_to_its_session():
    ps = FakePsutil(conns=[_conn(PEER_PORT, SERVER_PORT, 5100)],
                    parents={5100: [5000, OWN_PID]})
    a = _attr(ps, roots={5000: 'sess-job'})
    assert (a.status, a.session_id) == (ca.ATTRIBUTED, 'sess-job')


def test_non_loopback_peer_is_unattributed_without_touching_psutil():
    a = _attr(None, addr='192.168.1.20')
    assert a.status == ca.UNATTRIBUTED


def test_no_managed_sessions_means_unattributed():
    assert _attr(None, roots={}).status == ca.UNATTRIBUTED


def test_missing_peer_port_is_unavailable():
    assert _attr(FakePsutil(), port=None).status == ca.UNAVAILABLE


def test_psutil_missing_is_unavailable(monkeypatch):
    monkeypatch.setattr(ca, '_load_psutil', lambda: None)
    a = ca.attribute_caller('127.0.0.1', PEER_PORT, SERVER_PORT, ROOTS, own_pid=OWN_PID)
    assert a.status == ca.UNAVAILABLE and 'psutil' in a.detail


def test_owner_vanished_mid_lookup_is_unavailable():
    ps = FakePsutil(conns=[_conn(PEER_PORT, SERVER_PORT, 7777)], parents={})
    assert _attr(ps).status == ca.UNAVAILABLE


def test_pid_hidden_in_system_table_falls_back_to_the_session_trees():
    # Linux/macOS without privilege: the socket row exists but names no PID.
    ps = FakePsutil(conns=[_conn(PEER_PORT, SERVER_PORT, None)],
                    children={SESSION_PID: [SHELL_PID, CURL_PID]},
                    tree_conns={CURL_PID: [_conn(PEER_PORT, SERVER_PORT, CURL_PID)]},
                    parents=CHAIN)
    a = _attr(ps)
    assert (a.status, a.session_id) == (ca.ATTRIBUTED, 'sess-1')


def test_macos_style_access_denied_uses_the_session_trees():
    ps = FakePsutil(system_wide_error=_Access('needs root'),
                    children={SESSION_PID: [SHELL_PID, CURL_PID]},
                    tree_conns={CURL_PID: [_conn(PEER_PORT, SERVER_PORT, CURL_PID)]},
                    parents=CHAIN)
    a = _attr(ps)
    assert (a.status, a.pid) == (ca.ATTRIBUTED, SESSION_PID)


def test_tree_search_that_finds_nothing_is_unattributed_when_complete():
    ps = FakePsutil(system_wide_error=_Access('needs root'),
                    children={SESSION_PID: [SHELL_PID]},
                    tree_conns={SHELL_PID: [], SESSION_PID: []}, parents=CHAIN)
    assert _attr(ps).status == ca.UNATTRIBUTED


def test_tree_search_with_an_uninspectable_process_is_unavailable_not_unattributed():
    ps = FakePsutil(system_wide_error=_Access('needs root'),
                    children={SESSION_PID: [SHELL_PID]},
                    tree_conns={SHELL_PID: _Access('denied'), SESSION_PID: []}, parents=CHAIN)
    assert _attr(ps).status == ca.UNAVAILABLE


def test_attribute_caller_never_raises():
    class Boom:
        def net_connections(self, kind='tcp'):
            raise RuntimeError('x')
        def Process(self, pid):
            raise RuntimeError('y')
    assert _attr(Boom()).status in (ca.UNAVAILABLE, ca.UNATTRIBUTED)


def test_managed_roots_covers_sessions_and_tracked_jobs_but_not_exited_procs():
    live = SimpleNamespace(pid=11, poll=lambda: None)
    dead = SimpleNamespace(pid=12, poll=lambda: 0)
    roots = ca.managed_roots(
        {'a': {'proc': live, 'session_id': 'a'}, 'b': {'proc': dead, 'session_id': 'b'},
         'c': {'session_id': 'c'}},
        {13: {'session_id': 'a', 'type': 'agent_job'}, 14: {'session_id': ''}})
    assert roots == {11: 'a', 13: 'a'}


# ── the real route ──────────────────────────────────────────────────────────

def _stub_dispatch(monkeypatch):
    from mc.blueprints import agent_routes as ar
    captured = {}
    monkeypatch.setattr(ar, '_dispatch_agent_internal',
                        lambda *a, **kw: captured.update(kw) or 'sid-x')
    return captured


def _register_session(monkeypatch, pid=SESSION_PID):
    from mc import state as mc_state
    mc_state.agent_sessions['sess-1'] = {
        'proc': SimpleNamespace(pid=pid, poll=lambda: None),
        'session_id': 'sess-1', 'project_id': 'p1', 'trigger_type': 'manual'}


def _agent_caller(monkeypatch):
    """Make the next request look like it came from curl under session sess-1."""
    _register_session(monkeypatch)
    ps = FakePsutil(conns=[_conn(PEER_PORT, SERVER_PORT, CURL_PID)], parents=CHAIN)
    monkeypatch.setattr(ca, '_load_psutil', lambda: ps)
    monkeypatch.setattr(ca.os, 'getpid', lambda: OWN_PID)


PEER = {'REMOTE_PORT': PEER_PORT, 'SERVER_PORT': str(SERVER_PORT)}

SPOOFS = [
    pytest.param({'json': {'task': 't', 'source': 'ui'}, 'headers': {}}, id='body-source-ui'),
    pytest.param({'json': {'task': 't'}, 'headers': {'Origin': 'http://localhost:5199'}},
                 id='origin-header'),
    pytest.param({'json': {'task': 't', 'client': 'mobile'}, 'headers': {}},
                 id='client-mobile'),
]


@pytest.mark.parametrize('req', SPOOFS)
def test_attributed_caller_cannot_spoof_the_ui(client, monkeypatch, req):
    captured = _stub_dispatch(monkeypatch)
    _agent_caller(monkeypatch)
    resp = client.post('/api/project/p1/agent/dispatch', json=req['json'],
                       headers=req['headers'], environ_overrides=PEER)
    assert resp.status_code == 200
    assert captured['trigger_type'] == 'dispatch'
    assert captured['source'] == 'agent'


@pytest.mark.parametrize('req', SPOOFS)
def test_same_requests_from_a_non_session_caller_stay_manual(client, monkeypatch, req):
    """Control: the same three requests from a caller that is NOT under a
    session keep their hinted trigger_type, so the real UI is unaffected."""
    captured = _stub_dispatch(monkeypatch)
    _register_session(monkeypatch)
    ps = FakePsutil(conns=[_conn(PEER_PORT, SERVER_PORT, 7000)], parents={7000: [1]})
    monkeypatch.setattr(ca, '_load_psutil', lambda: ps)
    resp = client.post('/api/project/p1/agent/dispatch', json=req['json'],
                       headers=req['headers'], environ_overrides=PEER)
    assert resp.status_code == 200
    assert captured['trigger_type'] == 'manual'


def test_unattributed_ui_request_with_origin_stays_manual(client, monkeypatch):
    captured = _stub_dispatch(monkeypatch)
    _register_session(monkeypatch)
    # Browser process: not under any session.
    ps = FakePsutil(conns=[_conn(PEER_PORT, SERVER_PORT, 7000)], parents={7000: [6000, 1]})
    monkeypatch.setattr(ca, '_load_psutil', lambda: ps)
    resp = client.post('/api/project/p1/agent/dispatch', json={'task': 't', 'source': 'ui'},
                       headers={'Origin': 'http://localhost:5199'}, environ_overrides=PEER)
    assert resp.status_code == 200
    assert captured['trigger_type'] == 'manual'
    assert captured['source'] == 'ui'


def test_lookup_failure_without_origin_fails_closed_to_dispatch(client, monkeypatch):
    captured = _stub_dispatch(monkeypatch)
    _register_session(monkeypatch)
    monkeypatch.setattr(ca, '_load_psutil', lambda: None)      # psutil unavailable
    resp = client.post('/api/project/p1/agent/dispatch', json={'task': 't', 'source': 'ui'},
                       environ_overrides=PEER)
    assert resp.status_code == 200
    assert captured['trigger_type'] == 'dispatch'
    assert captured['source'] == 'agent'


def test_lookup_failure_with_origin_keeps_todays_behaviour(client, monkeypatch):
    captured = _stub_dispatch(monkeypatch)
    _register_session(monkeypatch)
    monkeypatch.setattr(ca, '_load_psutil', lambda: None)
    resp = client.post('/api/project/p1/agent/dispatch', json={'task': 't'},
                       headers={'Origin': 'http://localhost:5199'}, environ_overrides=PEER)
    assert resp.status_code == 200
    assert captured['trigger_type'] == 'manual'


def test_lookup_failure_is_logged(client, monkeypatch):
    _stub_dispatch(monkeypatch)
    _register_session(monkeypatch)
    monkeypatch.setattr(ca, '_load_psutil', lambda: None)
    logged = []
    monkeypatch.setattr(ca, '_log', lambda msg, *a, **k: logged.append(msg))
    client.post('/api/project/p1/agent/dispatch', json={'task': 't'}, environ_overrides=PEER)
    assert any('attribution unavailable' in m for m in logged)


# ── real sockets, real processes, real psutil ───────────────────────────────
# The fakes above pin the logic; this pins the facts it rests on: werkzeug
# hands the handler REMOTE_PORT, psutil maps that port to the curl-equivalent
# process, and its parent chain really reaches the process we call a session.

_CLIENT = ("import urllib.request,sys;"
           "print(urllib.request.urlopen('http://127.0.0.1:%d/who' % int(sys.argv[1]),"
           "timeout=20).read().decode())")


@pytest.mark.skipif(ca._load_psutil() is None, reason='psutil not installed')
def test_real_process_tree_is_attributed_and_a_sibling_is_not():
    import json
    import subprocess
    import threading
    from werkzeug.serving import make_server

    seen = []
    roots = {}

    def app(environ, start_response):
        a = ca.attribute_caller(environ['REMOTE_ADDR'], environ.get('REMOTE_PORT'),
                                environ['SERVER_PORT'], dict(roots))
        seen.append(a)
        body = json.dumps({'status': a.status}).encode()
        start_response('200 OK', [('Content-Type', 'application/json')])
        return [body]

    srv = make_server('127.0.0.1', 0, app, threaded=True)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    # The "session": a process that runs the client as its own child.
    session = subprocess.Popen(
        [sys.executable, '-c',
         "import subprocess,sys;subprocess.run([sys.executable,'-c',sys.argv[1],sys.argv[2]])",
         _CLIENT, str(port)],
        stdout=subprocess.PIPE, text=True)
    roots[session.pid] = 'sess-real'
    try:
        session.communicate(timeout=60)
        under_session = seen[-1]
        # Sibling: same client, launched straight from this process, not under the session.
        subprocess.run([sys.executable, '-c', _CLIENT, str(port)],
                       stdout=subprocess.PIPE, text=True, timeout=60, check=True)
        sibling = seen[-1]
    finally:
        srv.shutdown()
        session.kill()
    assert (under_session.status, under_session.session_id) == (ca.ATTRIBUTED, 'sess-real')
    assert sibling.status == ca.UNATTRIBUTED
