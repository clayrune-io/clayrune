"""Saved draft projection returns setup fields, never approval or arbitrary state."""
import pytest
from flask import Flask
from mc.desk_connect import saved_mcp_draft as drafts
from mc.desk_connect.mcp_errors import ActivationError
from mc.blueprints import desk_saved_mcp_draft_routes as routes


@pytest.mark.parametrize('kind',['npm','github','remote'])
def test_allowlisted_projection(monkeypatch,kind):
    op={'ecosystem':kind,'server_name':'example','scope':{'kind':'project','project_id':'p1'},
        'package':'example-mcp','version':'1.2.3','entry':'dist/index.js','args':['--quiet'],
        'command':'node','url':'https://mcp.example.com/mcp','protocol':'sse',
        'auth':{'type':'oauth','issuer':'https://example.com','scopes':['read']},
        'credentials':[{'env':'EXAMPLE_TOKEN','vault':'example.token','value':'must-not-project'}],
        'install_steps':[{'script':'never-project'}],'fingerprint':'never-project','approved':True}
    monkeypatch.setattr(drafts.store,'get',lambda *args:{'operation':op,'project_id':'p1'})
    out=drafts.read({'scope':'project','project_id':'p1','server_name':'example'})
    assert out['credentials']==[{'env':'EXAMPLE_TOKEN','vault':'example.token'}]
    assert out['scope']=='project' and out['project_id']=='p1' and out['ecosystem']==kind
    assert 'never-project' not in str(out) and 'must-not-project' not in str(out)
    if kind=='remote': assert out['issuer']=='https://example.com' and out['auth']=='oauth'
    else: assert out['args']==['--quiet'] and out['entry']=='dist/index.js'


@pytest.mark.parametrize('raw',[None,{}, {'scope':'bad','server_name':'example'},
    {'scope':'global','server_name':[]}, {'scope':'project','server_name':'example','project_id':[]},
    {'scope':'global','server_name':'example','operation':{}}])
def test_bad_selection(raw):
    with pytest.raises(ActivationError): drafts.read(raw)


def test_missing_and_unattended_do_not_fall_back(monkeypatch):
    monkeypatch.setattr(drafts.store,'get',lambda *args:None)
    app=Flask(__name__);app.register_blueprint(routes.bp);client=app.test_client()
    monkeypatch.setattr(routes,'is_unattended_caller',lambda:False)
    assert client.post('/api/desk/connect/custom/saved-draft',json={'scope':'global','server_name':'absent'}).status_code==404
    monkeypatch.setattr(routes,'is_unattended_caller',lambda:True)
    assert client.post('/api/desk/connect/custom/saved-draft',json={}).status_code==403
