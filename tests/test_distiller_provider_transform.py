"""Provider context for the cross-session Distiller."""


def test_authoritative_provider_comes_from_exact_agent_log_session(monkeypatch):
    from mc import distiller

    monkeypatch.setattr(distiller, '_load_agent_log', lambda project_id: [
        {'session_id': 'other', 'provider': 'claude'},
        {'session_id': 'target', 'provider': 'codex'},
    ])
    assert distiller._authoritative_session_provider('p', 'target') == 'codex'


def test_provider_model_and_transform_do_not_inject_claude_tier(monkeypatch):
    from mc import distiller

    calls = []
    monkeypatch.setattr(
        distiller, '_resolve_model',
        lambda key, provider, default='': '' if provider == 'codex' else default)
    monkeypatch.setattr(
        distiller, '_text_transform',
        lambda provider, model, instruction, body, **kwargs:
            calls.append((provider, model, instruction, body, kwargs)) or 'ok')
    project = {'project_path': 'C:/fixture'}
    model = distiller._provider_model('codex')

    assert model == ''
    assert distiller._model_call(
        'codex', project, model, 'extract', 'transcript') == 'ok'
    assert calls[0][0:2] == ('codex', '')


def test_missing_provider_context_fails_closed_before_model_call(monkeypatch):
    from mc import distiller

    called = []
    monkeypatch.setattr(distiller, '_load_agent_log', lambda project_id: [])
    monkeypatch.setattr(distiller, '_text_transform', lambda *a, **k: called.append(a))

    assert distiller._authoritative_session_provider('p', 'missing') is None
    assert called == []
