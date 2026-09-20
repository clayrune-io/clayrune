"""Claude's server bridge preserves the generic runtime dispatch contract."""


def test_claude_dispatch_hook_forwards_runtime_contract(monkeypatch, tmp_path):
    import server

    seen = {}
    session = {'mode': 'A', 'started_at': 'now'}

    def dispatch(project_id, task, **kwargs):
        seen.update(project_id=project_id, task=task, **kwargs)
        server.agent_sessions['mc-hm'] = session
        return 'mc-hm'

    monkeypatch.setattr(server, '_dispatch_agent_internal', dispatch)
    monkeypatch.setattr(
        server, 'load_project',
        lambda project_id: {'project_path': str(tmp_path)})
    monkeypatch.setitem(server.agent_sessions, 'mc-hm', session)
    callback = lambda *args: None
    supplied = {'preexisting': True}

    handle = server._claude_dispatch_hook(
        project_id='p', project_path=str(tmp_path), task='work',
        mc_session_id='mc-hm', model='sonnet', effort='high', max_turns=5,
        system_prompt='hivemind context', housekeeping=True,
        callbacks={'on_process_exit': callback},
        session_metadata={'hivemind_id': 'hm-1'}, session_dict=supplied,
        project_generation=3, trigger_type='hivemind_orchestrator',
        trigger_id='hm-1')

    assert handle.mc_session_id == 'mc-hm'
    assert seen['model_override'] == 'sonnet'
    assert seen['effort_override'] == 'high'
    assert seen['max_turns_override'] == 5
    assert seen['system_prompt_suffix'] == 'hivemind context'
    assert seen['runtime_callbacks']['on_process_exit'] is callback
    assert seen['session_metadata']['hivemind_id'] == 'hm-1'
    assert seen['session_dict_override'] is supplied
    assert seen['project_generation'] == 3
