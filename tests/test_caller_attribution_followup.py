"""Backlog 40260b57 follow-up (Fenn round-2 P2s + startup warning).

1. The managed-tree fallback (no system-wide socket table: macOS without root)
   must stop at the server PID like the main walk. Otherwise a human request is
   fenced whenever the server itself descends from a managed session.
2. A tracked entry must not reintroduce an exited PID: a reused browser/proxy
   PID would inherit an old session's fence.
3. Boot warns loudly when psutil is missing (attribution degrades to Origin
   trust, silently, otherwise).
"""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mc import caller_attribution as ca  # noqa: E402
from tests.test_caller_attribution import (  # noqa: E402
    CHAIN, CURL_PID, OWN_PID, PEER_PORT, SERVER_PORT, SESSION_PID, SHELL_PID,
    FakePsutil, _Access, _attr, _conn, _isolated_process_tracker,  # noqa: F401
)


# ── P2: fallback stops at the server PID ───────────────────────────────────

def _denied_psutil(children, tree_conns, parents):
    return FakePsutil(system_wide_error=_Access('needs root'),
                      children=children, tree_conns=tree_conns, parents=parents)


def test_fallback_hit_inside_the_server_subtree_is_not_attributed():
    # Server (999) was started from the agent's shell, so it sits in the
    # session tree. The human's webview (7000) is a child of the server.
    ps = _denied_psutil(
        children={SESSION_PID: [SHELL_PID, OWN_PID, 7000]},
        tree_conns={7000: [_conn(PEER_PORT, SERVER_PORT, 7000)]},
        parents={7000: [OWN_PID, SHELL_PID, SESSION_PID, 1],
                 OWN_PID: [SHELL_PID, SESSION_PID, 1]})
    a = _attr(ps)
    assert a.status == ca.UNATTRIBUTED
    assert 'server' in a.detail


def test_fallback_still_attributes_a_session_the_server_launched():
    # claude(4000) is a child OF the server; curl under it must still count.
    ps = _denied_psutil(
        children={SESSION_PID: [SHELL_PID, CURL_PID]},
        tree_conns={CURL_PID: [_conn(PEER_PORT, SERVER_PORT, CURL_PID)]},
        parents={CURL_PID: [SHELL_PID, SESSION_PID, OWN_PID, 1],
                 SHELL_PID: [SESSION_PID, OWN_PID, 1], SESSION_PID: [OWN_PID, 1]})
    a = _attr(ps)
    assert (a.status, a.session_id, a.pid) == (ca.ATTRIBUTED, 'sess-1', SESSION_PID)


def test_fallback_hit_whose_chain_cannot_be_read_is_unavailable_not_attributed():
    ps = _denied_psutil(
        children={SESSION_PID: [CURL_PID]},
        tree_conns={CURL_PID: [_conn(PEER_PORT, SERVER_PORT, CURL_PID)]},
        parents=CHAIN)
    base = ps.Process

    def process(pid):
        p = base(pid)
        if pid == CURL_PID:               # the chain walk, not the tree search
            def denied():
                raise _Access('denied')
            p.parents = denied
        return p
    ps.Process = process
    assert _attr(ps).status == ca.UNAVAILABLE


# ── P2: PID reuse via tracked entries ──────────────────────────────────────

class _Clock:
    """psutil stand-in answering only Process(pid).create_time()."""

    def __init__(self, times):
        self._times = times

    def Process(self, pid):
        if pid not in self._times:
            raise ca_gone(pid)
        t = self._times[pid]
        return SimpleNamespace(pid=pid, create_time=lambda: t)


class ca_gone(Exception):
    pass


def _exited(pid):
    return SimpleNamespace(pid=pid, poll=lambda: 0)


def _running(pid):
    return SimpleNamespace(pid=pid, poll=lambda: None)


def test_exited_session_and_exited_tracker_do_not_reintroduce_the_pid():
    # Fenn's fixture: session and tracker both hold an exited Popen for 4000,
    # tracker create_time=1, and a new unrelated process now owns the PID.
    roots = ca.managed_roots(
        {'old': {'proc': _exited(4000), 'session_id': 'old'}},
        {4000: {'session_id': 'old', 'proc': _exited(4000), 'create_time': 1.0}},
        psutil_mod=_Clock({4000: 5000.0}))
    assert roots == {}


def test_tracker_entry_with_live_handle_is_kept_without_psutil():
    roots = ca.managed_roots({}, {13: {'session_id': 'a', 'proc': _running(13)}})
    assert roots == {13: 'a'}


def test_handleless_tracker_entry_needs_a_matching_creation_time():
    entry = {'session_id': 'a', 'create_time': 1000.0}
    assert ca.managed_roots({}, {7: dict(entry)}, psutil_mod=_Clock({7: 1001.5})) == {7: 'a'}
    # same PID, different process started later: not ours
    assert ca.managed_roots({}, {7: dict(entry)}, psutil_mod=_Clock({7: 9000.0})) == {}


def test_handleless_tracker_entry_is_dropped_when_identity_cannot_be_confirmed():
    clock = _Clock({7: 1000.0})
    assert ca.managed_roots({}, {7: {'session_id': 'a'}}, psutil_mod=clock) == {}            # no create_time
    assert ca.managed_roots({}, {8: {'session_id': 'a', 'create_time': 1000.0}},
                            psutil_mod=clock) == {}                                           # process gone
    assert ca.managed_roots({}, {7: {'session_id': 'a', 'create_time': 1000.0}},
                            psutil_mod=SimpleNamespace()) == {}                               # psutil unusable


def test_independently_live_job_survives_its_sessions_exit():
    roots = ca.managed_roots(
        {'s': {'proc': _exited(11), 'session_id': 's'}},
        {13: {'session_id': 's', 'proc': _running(13)}})
    assert roots == {13: 's'}


def test_reused_pid_cannot_attribute_a_human_request(monkeypatch):
    # End to end through attribute_caller: the browser got PID 4000 after the
    # old session died; its request must stay unattributed.
    roots = ca.managed_roots(
        {}, {4000: {'session_id': 'old', 'proc': _exited(4000), 'create_time': 1.0}},
        psutil_mod=_Clock({4000: 5000.0}))
    ps = FakePsutil(conns=[_conn(PEER_PORT, SERVER_PORT, 4000)], parents={4000: [1]})
    assert _attr(ps, roots=roots).status == ca.UNATTRIBUTED


# ── startup warning ────────────────────────────────────────────────────────

def test_startup_warns_loudly_when_psutil_is_missing(monkeypatch):
    lines = []
    monkeypatch.setattr(ca, '_load_psutil', lambda: None)
    monkeypatch.setattr(ca, '_log', lambda msg, **kw: lines.append(msg))
    assert ca.warn_if_degraded() is False
    assert len(lines) == 1
    assert 'WARNING' in lines[0] and 'DEGRADED' in lines[0] and 'psutil' in lines[0]


def test_startup_is_quiet_when_psutil_is_present(monkeypatch):
    lines = []
    monkeypatch.setattr(ca, '_load_psutil', lambda: FakePsutil())
    monkeypatch.setattr(ca, '_log', lambda msg, **kw: lines.append(msg))
    assert ca.warn_if_degraded() is True
    assert lines == []
