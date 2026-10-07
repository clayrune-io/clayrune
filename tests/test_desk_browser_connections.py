"""Generic browser setup grants nothing, even for a known/readable service."""
import pytest
from flask import Flask

from mc import desk, desk_services
from mc.desk_connect import browser_connections as c, registry, type_view
from mc.desk_connect.browser_setup import SetupError


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(desk, 'STORE_PATH', tmp_path / 'desk.json')
    c._done.clear()
    return tmp_path


@pytest.mark.parametrize('url,name', [('https://youtube.com','YouTube'), ('https://example.com','Example'), ('https://notion.so','Notion')])
def test_save_and_reload_reference_only(store, url, name):
    clean = c.clean_draft({'name':name,'url':url,'browser_profile':'example-login'})
    result, duplicate = c.commit('request-1234',clean)
    assert not duplicate and not result['read'] and not result['post'] and not result['executes']
    rows = desk_services.list_services()
    assert rows[0]['kind'] == 'browser_profile' and rows[0]['publish'] is False
    assert rows[0]['browser_connection']['browser_profile'] == 'example-login'
    assert not desk._read_store()['accounts']
    assert c.commit('request-1234',clean) == (result,True)
    assert c.commit('request-5678',clean)[0]['unchanged'] is True


def test_no_replace_or_confused_replay(store):
    clean = c.clean_draft({'name':'Example','url':'https://example.com','browser_profile':'main'})
    c.commit('request-1234',clean)
    changed = {**clean,'browser_profile':'other'}
    with pytest.raises(SetupError): c.commit('request-1234',changed)
    with pytest.raises(SetupError): c.commit('request-5678',changed)
    assert desk_services.list_services()[0]['browser_connection']['browser_profile'] == 'main'


@pytest.mark.parametrize('name',['work.profile','a'*42,'a.'+'b'*62])
def test_long_dotted_profile_commit_and_service_view(store,name):
    clean=c.clean_draft({'name':'Example','url':'https://example.com','browser_profile':name})
    result,_=c.commit('request-1234',clean)
    assert result['browser_profile']==name
    assert desk_services.list_services()[0]['browser_connection']['browser_profile']==name


@pytest.mark.parametrize('name',['bad..profile','a'*65,'../profile',None])
def test_invalid_stored_profile_degrades_service_status(store,monkeypatch,name):
    from mc.desk_connect import browser_check
    logs=[];monkeypatch.setattr(browser_check,'_log',lambda message,**kw:logs.append(message))
    rec={'id':'svc-bad','name':'Example','link':'https://example.com',
         'browser_connection':{'service':None,'browser_profile':name}}
    row=desk_services.v1_service(rec,set())
    assert row['browser_connection']['status']['state']=='unknown'
    assert row['browser_connection']['status']['failure']=='unavailable' and logs


def test_generic_default_uses_shared_configuration(monkeypatch):
    from mc import state
    monkeypatch.setitem(state.CONFIG,'browser_default_profile','custom.main')
    assert c.variant(None)['setup']['default_profile']=='custom.main'


def test_replayed_save_refreshes_verdict_without_rewriting(store,monkeypatch):
    from mc.desk_connect import browser_check
    state=['verified']
    monkeypatch.setattr(browser_check,'status_for',lambda *args:{'state':state[0]})
    clean=c.clean_draft({'name':'Example','url':'https://example.com','browser_profile':'main'})
    result,_=c.commit('request-1234',clean)
    before=desk.STORE_PATH.read_bytes()
    state[0]='sign_in_required'
    replay,duplicate=c.commit('request-1234',clean)
    assert duplicate and replay['status']['state']=='sign_in_required'
    assert result['status']['state']=='verified' and desk.STORE_PATH.read_bytes()==before


@pytest.mark.parametrize('over', [{'url':'http://example.com'}, {'url':'https://localhost'}, {'browser_profile':'../../main'}, {'read':True}, {'signin_check':{'url':'https://evil.com'}}, {'url':'https://x.com'}])
def test_bad_draft_grants_nothing(store,over):
    with pytest.raises(SetupError): c.clean_draft({'name':'Example','url':'https://example.com','browser_profile':'main',**over})
    assert not desk_services.list_services()


def test_every_service_has_a_browser_setup_choice():
    for profile in [None,*registry.registry()['snapshot']['profiles']]:
        choices = type_view.project(profile)['picker']
        assert any(t['id']=='signin' for t in choices)


def test_route_keeps_human_passcode_order(store, monkeypatch):
    from mc.blueprints import desk_connect_browser_profile_routes as r
    calls=[]
    monkeypatch.setattr(r,'is_unattended_caller',lambda:True)
    monkeypatch.setattr(r,'_require_human_passcode',lambda d:calls.append('passcode') or (r.jsonify(error='wrong'),403))
    app=Flask(__name__);app.register_blueprint(r.bp)
    client=app.test_client()
    body={'request_id':'request-1234','draft':{'name':'Example','url':'https://example.com','browser_profile':'main'}}
    assert client.post('/api/desk/connect/browser-profile/commit',json=body).status_code==403
    assert not calls
    monkeypatch.setattr(r,'is_unattended_caller',lambda:False)
    assert client.post('/api/desk/connect/browser-profile/commit',json={}).status_code==400
    assert not calls
    assert client.post('/api/desk/connect/browser-profile/commit',json=body).status_code==403
    assert calls==['passcode'] and not desk_services.list_services()
    monkeypatch.setattr(r,'_require_human_passcode',lambda d:None)
    assert client.post('/api/desk/connect/browser-profile/commit',json=body).status_code==201
    assert client.post('/api/desk/connect/browser-profile/commit',json=body).status_code==200
