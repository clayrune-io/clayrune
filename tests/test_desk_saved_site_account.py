"""Attachment is explicit, idempotent, default-deny and atomic on the Desk store."""
import copy
import pytest
from flask import Flask, jsonify
from mc import desk, desk_accounts
from mc.desk_connect import saved_site_account as sites, permission_policy
from mc.desk_connect.browser_setup import SetupError
from mc.blueprints import desk_saved_site_account_routes as routes

SOURCE={'url':'https://www.instagram.com','browser_profile':'saved-profile','signin_method':'other'}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(desk, 'STORE_PATH', tmp_path / 'desk.json')
    with desk._store_lock:
        store = desk._read_store()
        store['services'] = {'old-instagram': {'id':'old-instagram', 'name':'www.instagram.com',
            'link':'https://www.instagram.com', 'credential':'', 'created_at':'then',
            'browser_connection': {'service':None,'browser_profile':'saved-profile','read':False,'post':False}}}
        desk._write_store(store)
    state = {'unattended':False, 'prompts':0}
    monkeypatch.setattr(routes, 'is_unattended_caller', lambda: state['unattended'])
    def proof(body):
        state['prompts'] += 1
        return None if body.get('passcode') == 'test-passcode' else (jsonify(error='bad_passcode'),403)
    monkeypatch.setattr(routes, '_require_human_passcode', proof)
    app=Flask(__name__); app.register_blueprint(routes.bp)
    return app.test_client(),state


def test_new_attachment_retains_source_and_defaults_off(env):
    before=copy.deepcopy(desk._read_store()['services']['old-instagram'])
    out=sites.attach('old-instagram', {'identity':'@example'})
    store=desk._read_store(); rec=store['accounts'][out['account_id']]
    assert rec['platform']=='instagram' and rec['identity']=='example'
    assert rec['browser_profile']=='saved-profile' and rec['capability']=='assisted'
    assert permission_policy.read_policy(rec)['read'] is False
    assert permission_policy.read_policy(rec)['post'] is False
    assert 'read_via' not in rec and 'connections' not in rec
    assert store['services']['old-instagram']=={**before,'account_id':rec['id']}
    assert sites.attach('old-instagram',{'identity':'@example'})=={'account_id':rec['id'],'unchanged':True}
    assert len(desk._read_store()['accounts'])==1


def test_select_saved_account_keeps_policy_and_metadata(env):
    made=desk_accounts.create_account('instagram','existing')
    desk_accounts.update_account(made['id'],{'browser_profile':'saved-profile'})
    before=copy.deepcopy(desk._read_store()['accounts'][made['id']])
    assert sites.attach('old-instagram',{'id':made['id']})['account_id']==made['id']
    assert desk._read_store()['accounts'][made['id']]==before


@pytest.mark.parametrize('target',[None,{}, {'identity':''},{'identity':'@'}, {'id':'absent'}, {'id':[]},
                                  {'identity':'a','id':'b'}, {'identity':'x','browser_profile':'other'}])
def test_bad_target_does_not_write_or_spend_passcode(env,target):
    client,state=env; before=desk.STORE_PATH.read_bytes()
    out=client.post('/api/desk/connect/saved-site/old-instagram/account',json={'account':target,'source':SOURCE,'passcode':'test-passcode'})
    assert out.status_code==400 and state['prompts']==0
    assert desk.STORE_PATH.read_bytes()==before


def test_wrong_profile_and_retarget_refused(env):
    made=desk_accounts.create_account('instagram','existing')
    with pytest.raises(SetupError): sites.attach('old-instagram',{'id':made['id']})
    sites.attach('old-instagram',{'identity':'first'})
    before=desk.STORE_PATH.read_bytes()
    with pytest.raises(SetupError): sites.attach('old-instagram',{'identity':'second'})
    assert desk.STORE_PATH.read_bytes()==before


def test_human_passcode_and_unattended_gates(env):
    client,state=env; url='/api/desk/connect/saved-site/old-instagram/account'
    before=desk.STORE_PATH.read_bytes()
    state['unattended']=True
    assert client.post(url,json={'account':{'identity':'example'},'passcode':'test-passcode'}).status_code==403
    assert state['prompts']==0 and desk.STORE_PATH.read_bytes()==before
    state['unattended']=False
    assert client.post(url,json={'account':{'identity':'example'},'source':SOURCE}).status_code==403
    assert desk.STORE_PATH.read_bytes()==before
    assert client.post(url,json={'account':{'identity':'example'},'source':SOURCE,'passcode':'test-passcode'}).status_code==200


def test_failed_atomic_write_leaves_no_partial_attachment(env,monkeypatch):
    before=desk.STORE_PATH.read_bytes()
    def fail(*args): raise OSError('fixture write failure')
    monkeypatch.setattr(desk,'_write_store',fail)
    with pytest.raises(OSError): sites.attach('old-instagram',{'identity':'example'})
    assert desk.STORE_PATH.read_bytes()==before


@pytest.mark.parametrize('platform',['youtube','tiktok'])
def test_other_pane_sites_keep_their_existing_capability_contract(env,platform):
    with desk._store_lock:
        store=desk._read_store()
        store['services']['old-instagram']['link']=f'https://www.{platform}.com'
        desk._write_store(store)
    out=sites.attach('old-instagram',{'identity':'example'})
    rec=desk._read_store()['accounts'][out['account_id']]
    assert rec['platform']==platform and rec['capability']=='none'
    assert 'read_via' not in rec and 'connections' not in rec


def test_removed_account_can_be_attached_again_explicitly(env):
    first=sites.attach('old-instagram',{'identity':'example'})
    with desk._store_lock:
        store=desk._read_store(); del store['accounts'][first['account_id']]; desk._write_store(store)
    again=sites.attach('old-instagram',{'identity':'example'})
    assert again['account_id'] != first['account_id'] and not again['unchanged']


def test_changed_source_is_refused_before_passcode_and_at_write(env):
    client,state=env
    stale={**SOURCE,'browser_profile':'different'}
    before=desk.STORE_PATH.read_bytes()
    out=client.post('/api/desk/connect/saved-site/old-instagram/account',json={'account':{'identity':'example'},'source':stale,'passcode':'test-passcode'})
    assert out.status_code==409 and state['prompts']==0
    with pytest.raises(SetupError): sites.attach('old-instagram',{'identity':'example'},stale)
    assert desk.STORE_PATH.read_bytes()==before
