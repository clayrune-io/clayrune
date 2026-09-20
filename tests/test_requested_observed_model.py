"""INIT telemetry must never rewrite the requested continuation engine."""
import io
import json
from types import SimpleNamespace

import pytest

from mc.agent_runtime import QwenRuntime, SessionHandle, _mode_a_reader


@pytest.mark.parametrize('requested', ['chosen-model', '', 'alias'])
@pytest.mark.parametrize('observed', ['observed-other', '', None])
def test_qwen_init_keeps_requested_model(requested, observed):
    proc = SimpleNamespace(stdout=io.StringIO(json.dumps({
        'type': 'system', 'subtype': 'init', 'session_id': 'native',
        'model': observed}) + '\n'), wait=lambda: 0)
    session = {'proc': proc, 'log_lines': [], 'status': 'running',
               'agent_model': requested, 'model': requested, 'pinned_model': requested}
    handle = SessionHandle('test', 'qwen', 'A', '', 'test', session)
    runtime = QwenRuntime()
    _mode_a_reader(proc, handle, runtime)
    assert runtime.session_model(handle) == requested
    assert session['model'] == requested
    assert session['pinned_model'] == requested
    assert session.get('observed_model') == (observed or None)


def test_init_observation_is_available_to_callback_without_changing_request():
    proc = SimpleNamespace(stdout=io.StringIO(json.dumps({
        'type': 'system', 'subtype': 'init', 'session_id': 'native',
        'model': 'resolved-model'}) + '\n'), wait=lambda: 0)
    session = {'proc': proc, 'log_lines': [], 'status': 'running', 'agent_model': 'alias'}
    captured = []
    handle = SessionHandle('test', 'qwen', 'A', '', 'test', session,
                           meta={'callbacks': {'on_init': lambda ev, s: captured.append(
                               (s['agent_model'], s['observed_model']))}})
    _mode_a_reader(proc, handle, QwenRuntime())
    assert captured == [('alias', 'resolved-model')]
