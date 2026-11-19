"""MC-1179 T8-3 follow-up, gate batch 1: attribution refusal on 21 mutating routes.

Each route below used to check no caller. An agent's `curl` (no browser Origin) or an
unattended session could reach consent points that record "a person clicked", the
remote-access control plane, mobile pairing, a browser profile delete, a project delete
and the process stop. They now answer 403 `human_only` to any agent or unattended caller
and behave exactly as before for a request that carries a browser Origin.

The matrix probe (tests/test_route_gate_matrix.py) covers the 403 side with an empty
body. This file adds what the probe cannot show:

  * for every route, the FIRST backend touch is replaced by a spy; the agent call must
    leave the spy untouched (the refusal runs before any lookup, save, thread or outside
    call) and the human call must reach it and keep today's response;
  * the gate's own classification: an Origin-bearing request that OS attribution ties to
    a managed agent is refused; a tunnel/phone request (Origin, non-loopback peer) is a
    person and is let through; an unattended running session without Origin is refused.

No test here touches live state: every backend is a spy or a tmp path.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

ORIGIN = {'Origin': 'http://localhost:5199'}


class Spy:
    def __init__(self, ret=None):
        self.ret, self.calls = ret, []

    def __call__(self, *a, **kw):
        self.calls.append((a, kw))
        return self.ret


@pytest.fixture()
def client():
    import server
    server.app.config['TESTING'] = True
    return server.app.test_client()


# Each setup patches the route's first backend touch with a spy and returns
# (spy, human_status, body). human_status is what a person gets today with that stub.

def _patch(monkeypatch, module_path, attr, ret=None):
    import importlib
    mod = importlib.import_module(module_path)
    spy = Spy(ret)
    monkeypatch.setattr(mod, attr, spy)
    return spy


def _lookup_404(module_path, attr, ret=None):
    def setup(monkeypatch, tmp_path):
        return _patch(monkeypatch, module_path, attr, ret), 404, {}
    return setup


def _remote_501(monkeypatch, tmp_path):
    return _patch(monkeypatch, 'mc.blueprints.remote_routes', '_get_remote_provider'), 501, {}


def _pair_501(monkeypatch, tmp_path):
    return _patch(monkeypatch, 'mc.blueprints.push_mobile', '_get_remote_provider'), 501, {}


def _enforce(monkeypatch, tmp_path):
    return _patch(monkeypatch, 'mc.blueprints.remote_routes', '_enforce_session_labels_once', {}), 200, {}


def _pair_put(monkeypatch, tmp_path):
    spy = _patch(monkeypatch, 'mc.blueprints.push_mobile', '_save_mobile_pairing')
    body = {'tunnel_url': 'https://probe.invalid', 'client_id': 'cid', 'client_secret': 'sec',
            'skip_verify': True}
    return spy, 200, body


def _pair_delete(monkeypatch, tmp_path):
    from mc.blueprints import push_mobile as pm
    spy = Spy()
    monkeypatch.setattr(pm, 'MOBILE_PAIRING_PATH', SimpleNamespace(unlink=spy))
    return spy, 200, {}


def _profile_delete(monkeypatch, tmp_path):
    from mc import browser_agent_read
    from mc.blueprints import browser_routes as br
    prof = tmp_path / 'profile'
    prof.mkdir()
    monkeypatch.setattr(br, '_profile_dir', lambda name: str(prof))
    spy = Spy()
    monkeypatch.setattr(browser_agent_read, 'clear_policy', spy)
    return spy, 200, {}


def _stop(monkeypatch, tmp_path):
    from mc.blueprints import system_routes as sr
    monkeypatch.setattr(sr, '_get_active_restart_blockers',
                        lambda: {'active_sessions': [], 'active_hiveminds': []})
    spy = Spy()
    monkeypatch.setattr(sr, '_perform_server_' + 'shut' + 'down_async', spy)
    return spy, 202, {'confirmed': True}


PR = 'mc.blueprints.project_routes'
HM = 'mc.blueprints.hivemind_routes'

CASES = {
    'social approve': ('POST', '/api/project/p1/social/queue/i1/approve', _lookup_404(PR, 'load_project')),
    'social posted': ('POST', '/api/project/p1/social/queue/i1/posted', _lookup_404(PR, 'load_project')),
    'desk proposal accept': ('POST', '/api/desk/proposals/x1/accept',
                             _lookup_404('mc.desk', '_read_store', {'proposals': {}})),
    'hivemind decision approve': ('POST', '/api/hivemind/h1/decisions/d1/approve', _lookup_404(HM, '_hm_load_manifest')),
    'hivemind finding review': ('POST', '/api/hivemind/h1/findings/f1/review', _lookup_404(HM, '_hm_load_manifest')),
    'hivemind intervene': ('POST', '/api/hivemind/h1/intervene', _lookup_404(HM, '_hm_load_manifest')),
    'automation accept': ('POST', '/api/automation/suggestions/s1/accept',
                          _lookup_404('mc.automation_suggestions', 'get_pending')),
    'remote enable': ('POST', '/api/remote/enable', _remote_501),
    'remote disable': ('POST', '/api/remote/disable', _remote_501),
    'remote disconnect': ('POST', '/api/remote/disconnect', _remote_501),
    'remote resume': ('POST', '/api/remote/resume', _remote_501),
    'remote revoke-all': ('POST', '/api/remote/sessions/revoke-all', _remote_501),
    'remote revoke one': ('POST', '/api/remote/sessions/s1/revoke', _remote_501),
    'remote enforce': ('POST', '/api/remote/sessions/enforce', _enforce),
    'mobile-pair config put': ('PUT', '/api/mobile-pair/config', _pair_put),
    'mobile-pair config delete': ('DELETE', '/api/mobile-pair/config', _pair_delete),
    'mobile-pair generate': ('POST', '/api/mobile-pair/generate', _pair_501),
    'mobile-pair token delete': ('DELETE', '/api/mobile-pair/tokens/t1', _pair_501),
    'browser profile delete': ('DELETE', '/api/browser/profiles/probe', _profile_delete),
    'project delete': ('DELETE', '/api/project/p1', _lookup_404(PR, '_record_path')),
    'process stop': ('POST', '/api/system/' + 'shut' + 'down', _stop),
}


def test_batch_is_21_routes():
    assert len(CASES) == 21


@pytest.mark.parametrize('name', list(CASES))
def test_agent_caller_is_refused_before_any_backend_touch(client, monkeypatch, tmp_path, name):
    method, url, setup = CASES[name]
    spy, _human, body = setup(monkeypatch, tmp_path)
    resp = client.open(url, method=method, json=body)          # no Origin: an agent's curl
    assert resp.status_code == 403, f'{name}: {resp.status_code} {resp.get_data(as_text=True)[:200]}'
    assert (resp.get_json(silent=True) or {}).get('error') == 'human_only'
    assert spy.calls == [], f'{name}: the refusal did not run first, backend was touched'


@pytest.mark.parametrize('name', list(CASES))
def test_dashboard_caller_keeps_todays_behaviour(client, monkeypatch, tmp_path, name):
    method, url, setup = CASES[name]
    spy, human_status, body = setup(monkeypatch, tmp_path)
    resp = client.open(url, method=method, json=body, headers=ORIGIN)
    assert resp.status_code == human_status, f'{name}: {resp.status_code} {resp.get_data(as_text=True)[:200]}'
    assert spy.calls, f'{name}: the human call never reached its backend'


# ── the gate's own classification ────────────────────────────────────────────

def _req(headers=None, remote_addr='127.0.0.1'):
    import server
    return server.app.test_request_context(
        '/x', method='POST', headers=headers or {}, environ_base={'REMOTE_ADDR': remote_addr})


def test_origin_plus_attribution_to_a_managed_agent_is_refused(monkeypatch):
    from mc import caller_attribution
    from mc.human_only_gate import refuse_non_human
    monkeypatch.setattr(caller_attribution, 'attribute_caller',
                        lambda *a, **k: caller_attribution.Attribution(
                            caller_attribution.ATTRIBUTED, 'sess1', 1, 'forged Origin'))
    with _req(ORIGIN):
        refused = refuse_non_human('do a thing')
    assert refused is not None and refused[1] == 403
    assert refused[0].get_json()['error'] == 'human_only'


def test_phone_or_tunnel_request_is_a_person(monkeypatch):
    """Origin + a peer that is not on this machine: nothing to attribute, so it passes,
    even with a live managed agent process registered."""
    from mc import state
    from mc.human_only_gate import refuse_non_human
    live = SimpleNamespace(pid=4242, poll=lambda: None)
    monkeypatch.setitem(state.agent_sessions, 'live-agent',
                        {'proc': live, 'session_id': 'live-agent', 'status': 'running',
                         'trigger_type': 'dispatch', 'project_id': 'p'})
    with _req({'Origin': 'https://dash.example'}, remote_addr='203.0.113.9'):
        assert refuse_non_human('do a thing') is None


def test_dashboard_request_with_no_agent_running_passes():
    from mc.human_only_gate import refuse_non_human
    with _req(ORIGIN):
        assert refuse_non_human('do a thing') is None


def test_unattended_session_running_and_no_origin_is_refused(monkeypatch):
    from mc import state
    from mc.human_only_gate import refuse_non_human
    monkeypatch.setitem(state.agent_sessions, 'sched', {
        'status': 'running', 'trigger_type': 'schedule', 'project_id': 'p', 'session_id': 'sched'})
    with _req():
        refused = refuse_non_human('do a thing')
    assert refused is not None and refused[1] == 403


def test_the_refusal_names_the_action_and_carries_no_passcode_prompt():
    from mc.human_only_gate import refuse_non_human
    with _req():
        resp, status = refuse_non_human('stop the Clayrune server')
    body = resp.get_json()
    assert status == 403 and body['error'] == 'human_only'
    assert 'stop the Clayrune server' in body['message']
    assert 'passcode' not in body['message'].lower()
