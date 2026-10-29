"""Edit targets a saved browser reference by ID, preserving its non-setup data."""
import copy

import pytest
from flask import Flask

from mc import desk
from mc.desk_connect import browser_connections as connections
from mc.blueprints import desk_connect_browser_profile_routes as routes


@pytest.fixture
def saved(tmp_path,monkeypatch):
    monkeypatch.setattr(desk,'STORE_PATH',tmp_path/'desk.json')
    monkeypatch.setattr(routes,'is_unattended_caller',lambda:False)
    monkeypatch.setattr(routes,'_require_human_passcode',lambda b:None if b.get('passcode')=='proof' else (routes.jsonify(error='bad_passcode'),403))
    connections._done.clear()
    clean=connections.clean_draft({'name':'Example','url':'https://example.com','browser_profile':'main'})
    rec,_=connections.commit('initial-request',clean)
    app=Flask(__name__);app.register_blueprint(routes.bp)
    return app.test_client(),rec['connection_id']


@pytest.mark.parametrize('change',[{'name':'Renamed Example'},{'browser_profile':'other-profile'},{'signin_method':'google'}])
def test_saved_browser_edit_updates_same_record(saved,change):
    client,identifier=saved
    before=copy.deepcopy(desk._read_store()['services'][identifier])
    body={'request_id':'edited-request','passcode':'proof','draft':{'name':'Example','url':'https://example.com','browser_profile':'main',**change}}
    out=client.patch(f'/api/desk/connect/browser-profile/{identifier}',json=body)
    assert out.status_code==200, out.get_json()
    store=desk._read_store()
    assert list(store['services'])==[identifier]
    record=store['services'][identifier]
    assert record['name']==change.get('name',before['name'])
    assert record['browser_connection']['browser_profile']==change.get('browser_profile','main')
    assert record['created_at']==before['created_at'] and record['credential']==before['credential']
    assert not store['accounts']


def test_edit_gates_and_missing_id(saved,monkeypatch):
    client,identifier=saved
    body={'request_id':'edited-request','draft':{'name':'Renamed Example','url':'https://example.com','browser_profile':'main'}}
    before=desk.STORE_PATH.read_bytes()
    assert client.patch(f'/api/desk/connect/browser-profile/{identifier}',json=body).status_code==403
    body['passcode']='proof'
    monkeypatch.setattr(routes,'is_unattended_caller',lambda:True)
    assert client.patch(f'/api/desk/connect/browser-profile/{identifier}',json=body).status_code==403
    monkeypatch.setattr(routes,'is_unattended_caller',lambda:False)
    assert client.patch('/api/desk/connect/browser-profile/absent',json=body).status_code==404
    assert desk.STORE_PATH.read_bytes()==before


def test_edit_preserves_other_records_and_rejects_rename_collision(saved):
    client,identifier=saved
    clean=connections.clean_draft({'name':'Other','url':'https://other.example.com','browser_profile':'other'})
    connections.commit('other-request',clean)
    before=desk.STORE_PATH.read_bytes()
    body={'request_id':'edited-request','passcode':'proof',
          'draft':{'name':'OTHER','url':'https://example.com','browser_profile':'main'}}
    assert client.patch(f'/api/desk/connect/browser-profile/{identifier}',json=body).status_code==409
    assert desk.STORE_PATH.read_bytes()==before


def test_same_request_replays_and_changed_request_is_refused(saved):
    client,identifier=saved
    body={'request_id':'edited-request','passcode':'proof',
          'draft':{'name':'Renamed','url':'https://example.com','browser_profile':'other'}}
    first=client.patch(f'/api/desk/connect/browser-profile/{identifier}',json=body)
    assert first.status_code==200
    before=desk.STORE_PATH.read_bytes()
    again=client.patch(f'/api/desk/connect/browser-profile/{identifier}',json=body)
    assert again.get_json()==first.get_json() and desk.STORE_PATH.read_bytes()==before
    body['draft']['name']='Different'
    assert client.patch(f'/api/desk/connect/browser-profile/{identifier}',json=body).status_code==409
    assert desk.STORE_PATH.read_bytes()==before
