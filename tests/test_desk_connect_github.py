"""Repository staging uses the existing installer, with no model tools or implicit installs."""
import json
from pathlib import Path
import pytest
from flask import Flask
from mc import mcp_installer as installer
from mc.desk_connect import github_connection as github, github_activation as activation
from mc.desk_connect import custom_connection_service as service
from mc.desk_connect.mcp_errors import ActivationError
from mc.blueprints import desk_connect_custom_routes as routes

SHA='a'*40
URL='https://github.com/ZubeidHendricks/youtube-mcp-server'
REAL_SCAN=installer.security_scan

@pytest.fixture
def staged(tmp_path,monkeypatch):
    monkeypatch.setattr(github.store,'path',lambda:tmp_path/'approvals.json')
    monkeypatch.setattr(installer,'INSTALLS_ROOT',tmp_path/'installs')
    commands=[]
    def run(argv,cwd=None,**kw):
        commands.append(argv)
        if 'clone' in argv:
            folder=Path(argv[-1]);folder.mkdir(parents=True)
            (folder/'server.js').write_text('/* inert fixture */')
            (folder/'package.json').write_text(json.dumps({'scripts':{'postinstall':'echo fixture'}}))
            (folder/'README.md').write_text('```json\n'+json.dumps({'mcpServers':{'fixture':{'command':'node','args':['server.js'],'env':{'API_TOKEN':'your_token'}}}})+'\n```')
            return 0,'',''
        if argv[-1]=='HEAD':
            return 0,SHA,''
        return 0,'main',''
    monkeypatch.setattr(installer,'_run',run)
    monkeypatch.setattr(installer,'_extract_via_claude',lambda *a:pytest.fail('tool-enabled fallback'))
    monkeypatch.setattr(installer,'_resolve_npm',lambda:'npm')
    scans=[]
    def scan(directory,sha,*,toolless=False):
        scans.append(toolless);return {'available':False,'reason':'not scanned'}
    monkeypatch.setattr(installer,'security_scan',scan)
    monkeypatch.setattr(service,'visible_vault_names',lambda *a:{'youtube.key'})
    monkeypatch.setattr(activation,'conflict',lambda *a:None)
    service._forget_all_for_tests()
    github._stages.clear()
    answer=github.stage({'url':URL})
    return answer,commands,scans

def prepare(answer,**overrides):
    return service.prepare({'stage_id':answer['stage_id'],'command':'node','args':['server.js'],
                            'scope':'project','project_id':'p',**overrides},lambda p:{'id':p,'name':'Project','path':'/project'})

def test_classify_stage_pin_and_metadata(staged):
    answer,commands,scans=staged
    assert answer['sha']==SHA and answer['command']=='node'
    assert answer['credentials']==[{'env':'API_TOKEN'}]
    assert scans==[True]
    assert commands[0][0]=='git' and 'clone' in commands[0]
    assert 'protocol.file.allow=never' in commands[0]
    assert commands[0][-2]=='https://github.com/ZubeidHendricks/youtube-mcp-server.git'
    assert not any('install' in c or 'audit' in c for c in commands)

def test_second_stage_never_replaces_first(staged):
    first,_,_=staged
    before=github._stages[first['stage_id']]['install_dir']
    second=github.stage({'url':URL})
    assert before!=github._stages[second['stage_id']]['install_dir'] and Path(before).is_dir()

def test_existing_preview_directory_is_never_deleted(staged):
    answer,_,_=staged
    directory=Path(github._stages[answer['stage_id']]['install_dir'])
    with pytest.raises(RuntimeError,match='already exists'):
        installer.stage_clone(URL,'ZubeidHendricks','youtube-mcp-server',staging_id=answer['stage_id'])
    assert (directory/'server.js').is_file()

def test_repository_root_junction_refuses_file_check(staged,monkeypatch):
    directory=Path(github._stages[staged[0]['stage_id']]['install_dir'])
    monkeypatch.setattr(Path,'is_junction',lambda path:path==directory,raising=False)
    with pytest.raises(ActivationError,match='unsafe'):
        github.manifest.inventory(str(directory))

def test_reapproval_lists_changed_arguments(staged,monkeypatch):
    first=prepare(staged[0])
    op=service.check_submission(first['request_id'],first['fingerprint'])['op']
    monkeypatch.setattr(github.store,'get',lambda *a:{'operation':op,'fingerprint':first['fingerprint']})
    second=prepare(staged[0],args=['different.js'])
    assert second['reask'] and second['replaces']==first['fingerprint']
    assert any(c['field']=='arguments' for c in second['changes'])

def test_approval_pins_command_credentials_and_steps_off(staged):
    answer,_,_=staged
    card=prepare(answer,credentials=[{'env':'API_TOKEN','vault':'youtube.key'}])
    assert card['package']['version']==SHA
    assert card['scripts'] and not any(s['approved'] for s in card['scripts'])
    rec=service.check_submission(card['request_id'],card['fingerprint'])
    assert rec['op']['install_steps']==[] and rec['op']['credentials'][0]['vault']=='youtube.key'
    assert 'your_token' not in json.dumps(card)

def test_steps_change_approval(staged):
    answer,_,_=staged
    first=prepare(answer);second=prepare(answer,approve_scripts=['script:postinstall'])
    assert first['fingerprint']!=second['fingerprint']
    with pytest.raises(ActivationError):
        service.check_submission(second['request_id'],first['fingerprint'])

def test_manual_command_without_extraction(staged,monkeypatch):
    monkeypatch.setattr(installer,'extract_config',lambda *a,**k:{'servers':{}})
    result=github.stage({'url':URL})
    assert result['manual_command'] and result['command']==''
    with pytest.raises(ActivationError,match='start command'):
        prepare(result,command='')
    assert Path(prepare(result,command='node')['command']['starts']['command']).stem=='node'

@pytest.mark.parametrize('command',['npx','npm','uvx','pipx'])
def test_registry_launchers_do_not_bypass_commit_pin(staged,command):
    with pytest.raises(ActivationError,match='saved repository'):
        prepare(staged[0],command=command)

@pytest.mark.parametrize('url',['file:///tmp/repo.git','git@github.com:a/b.git','https://elsewhere.test/a.git','--upload-pack=evil'])
def test_non_github_inputs_refused(url):
    with pytest.raises(ActivationError):
        github.stage({'url':url})

def test_changed_source_refuses_review(staged):
    answer,_,_=staged
    directory=github._stages[answer['stage_id']]['install_dir']
    (Path(directory)/'server.js').write_text('changed')
    with pytest.raises(ActivationError,match='changed'):
        prepare(answer)

def test_passcode_and_unattended_gate(staged,monkeypatch):
    app=Flask(__name__);app.register_blueprint(routes.bp)
    client=app.test_client()
    monkeypatch.setattr(routes,'is_unattended_caller',lambda:True)
    assert client.post('/api/desk/connect/custom/github/stage',json={'url':URL}).status_code==403
    monkeypatch.setattr(routes,'is_unattended_caller',lambda:False)
    card=prepare(staged[0])
    monkeypatch.setattr(routes,'_require_human_passcode',lambda d:({'error':'human proof required'},403))
    monkeypatch.setattr(service,'commit',lambda *a:pytest.fail('must not write'))
    assert client.post('/api/desk/connect/custom/commit',json={'request_id':card['request_id'],'fingerprint':card['fingerprint']}).status_code==403

def test_toolless_security_scan_uses_only_transform(staged,monkeypatch):
    from mc import agent_runtime
    # Exercise the real function, not the fixture replacement.
    monkeypatch.setattr(installer,'_run',lambda *a,**kw:pytest.fail('tool-enabled CLI'))
    called=[]
    monkeypatch.setattr(agent_runtime,'run_text_transform',lambda *a,**kw:called.append(kw) or '{"summary":"fixture"}')
    directory=github._stages[staged[0]['stage_id']]['install_dir']
    assert REAL_SCAN(directory,SHA,toolless=True)['available']
    assert 'stdin_text' in called[0]

def test_save_and_launch_gate_keep_files_and_credentials_bound(staged,monkeypatch,tmp_path):
    from mc.desk_connect import custom_connection_store as store
    from mc import mcp
    monkeypatch.setattr(store,'path',lambda:tmp_path/'approvals.json')
    configs={}
    monkeypatch.setattr(mcp,'_write_project_servers',lambda path,mutate:mutate(configs))
    monkeypatch.setattr(activation.npm,'_read',lambda scope,pid,name,path:configs.get(name))
    monkeypatch.setattr(activation,'_manifest_path',lambda op:tmp_path/'approved-files.json')
    monkeypatch.setattr(activation.scripts,'spawn',lambda *a:pytest.fail('unticked command ran'))
    monkeypatch.setattr(activation.scripts,'run',lambda *a,**k:pytest.fail('unticked script ran'))
    card=prepare(staged[0])
    result,duplicate=service.commit(card['request_id'],card['fingerprint'])
    assert result['state']=='registered' and not duplicate
    assert service.commit(card['request_id'],card['fingerprint'])[1]
    op=store.get('project','p',card['server_name'])['operation']
    assert activation.matches(configs[card['server_name']],op)
    assert not activation.matches({**configs[card['server_name']],'env':{'TOKEN':'bad'}},op)
    (Path(op['directory'])/'server.js').write_text('changed after save')
    assert activation.problem(op)
    monkeypatch.setattr(activation.os,'execvpe',lambda *a:pytest.fail('changed files launched'))
    with pytest.raises(RuntimeError,match='changed'):
        activation.start('project','p',card['server_name'],card['fingerprint'])

def test_selected_script_executes_only_after_commit(staged,monkeypatch,tmp_path):
    from mc.desk_connect import custom_connection_store as store
    from mc import mcp
    monkeypatch.setattr(store,'path',lambda:tmp_path/'approvals.json')
    monkeypatch.setattr(mcp,'_write_project_servers',lambda path,mutate:mutate({}))
    monkeypatch.setattr(activation,'_manifest_path',lambda op:tmp_path/'approved-files.json')
    called=[]
    monkeypatch.setattr(activation.scripts,'run',lambda step,*a,**k:called.append(step))
    card=prepare(staged[0],approve_scripts=['script:postinstall'])
    assert not called
    result,_=service.commit(card['request_id'],card['fingerprint'])
    assert result['state']=='registered' and called[0]['body']=='echo fixture'

def test_changed_repository_refused_before_passcode(staged,monkeypatch):
    app=Flask(__name__);app.register_blueprint(routes.bp)
    monkeypatch.setattr(routes,'is_unattended_caller',lambda:False)
    card=prepare(staged[0])
    directory=github._stages[staged[0]['stage_id']]['install_dir']
    (Path(directory)/'server.js').write_text('changed after review')
    monkeypatch.setattr(routes,'_require_human_passcode',lambda *a:pytest.fail('stale card costs a passcode guess'))
    response=app.test_client().post('/api/desk/connect/custom/commit',json={'request_id':card['request_id'],'fingerprint':card['fingerprint']})
    assert response.status_code==409

def test_unapproved_node_search_path_refuses_before_credentials(staged,monkeypatch):
    from mc.desk_connect import custom_npm_gate, custom_connection_store as store
    card=prepare(staged[0])
    op=service.check_submission(card['request_id'],card['fingerprint'])['op']
    monkeypatch.setattr(store,'get',lambda *a:{'fingerprint':card['fingerprint'],'operation':op,'state':'registered'})
    monkeypatch.setattr(activation,'problem',lambda *a:'')
    monkeypatch.setattr(custom_npm_gate,'ancestor_problem',lambda *a:{'message':'Unapproved node_modules'})
    monkeypatch.setattr(activation.npm,'wrapper_flags',lambda *a:pytest.fail('credentials must not be given'))
    monkeypatch.setattr(activation.os,'execvpe',lambda *a:pytest.fail('unapproved code launched'))
    with pytest.raises(RuntimeError,match='Unapproved node_modules'):
        activation.start('project','p',card['server_name'],card['fingerprint'])

def test_extracted_absolute_arguments_reach_review(staged):
    assert prepare(staged[0],args=staged[0]['args'])['package']['version']==SHA
