"""Safety refusals must reach HTTP callers as errors, not ok:true."""
import pytest

from test_native_followup_context_routes import route_env, endpoint  # noqa: F401
from test_builtins_resume_context import spawns  # noqa: F401
from mc import guardrail_hooks as gh, state


@pytest.fixture(params=['gemini', 'qwen', 'opencode', 'goose', 'aider', 'kiro'])
def engine(request):
    return request.param


def test_followup_and_interrupt_return_visible_refusal(route_env, endpoint, monkeypatch, spawns):
    import server
    e = route_env
    monkeypatch.setitem(state.CONFIG, 'fence_unattended_enabled', True)
    e.session['trigger_type'] = 'schedule'
    monkeypatch.setattr(gh, 'run_fence_self_test', lambda *a, **kw: (False, 'test guard cannot load'))
    # These providers have real dispatch/follow-up checks, but no real CLI
    # or model turn is needed to exercise the HTTP/session error contract.
    response = server.app.test_client().post(
        f'/api/project/{e.project["id"]}/agent/{endpoint}',
        json={'session_id': e.session['session_id'], 'message': 'continue'})
    assert response.status_code == 409, response.get_data(as_text=True)
    assert response.json['ok'] is False
    assert 'refused while fencing is enabled' in response.json['error']
    assert e.session['status'] == 'error'
    assert not spawns
