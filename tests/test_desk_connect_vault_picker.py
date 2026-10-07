"""Picker reads metadata; a new login uses the existing single human Save."""
from mc import secrets_store as vault
from mc.blueprints import secrets_routes
from tests.test_desk_connect import env, _commit_req, PASSCODE  # noqa: F401

PASSWORD='fixture-password-never-in-picker'

def test_picker_metadata_has_name_and_username_without_value(env,monkeypatch):
    client,_,_=env
    vault.set_secret('linkedin.personal',PASSWORD,username='person@example.test',entry_type='login')
    client.application.register_blueprint(secrets_routes.bp)
    monkeypatch.setattr(vault,'is_locked',lambda:True)
    monkeypatch.setattr(vault,'get_secret_value',lambda *a,**k:(_ for _ in ()).throw(AssertionError('picker must not get a value')))
    response=client.get('/api/secrets')
    assert response.status_code==200
    row=response.json['secrets'][0]
    assert row['name']=='linkedin.personal' and row['username']=='person@example.test'
    assert 'value' not in row and PASSWORD not in response.get_data(as_text=True)

def test_new_login_is_one_entry_after_human_save_only(env):
    client,_,_=env
    draft={'url':'https://youtube.com','method':'save_for_agents','name':'YouTube',
           'credential':{'name':'youtube.personal','username':'person@example.test','value':PASSWORD,'entry_type':'login'}}
    denied=_commit_req(client,draft= draft,passcode='wrong')
    assert denied.status_code==403 and not vault.list_secrets()
    saved=_commit_req(client,draft=draft,passcode=PASSCODE)
    assert saved.status_code==201
    entries=vault.list_secrets()
    assert len(entries)==1 and entries[0]['name']=='youtube.personal' and entries[0]['username']=='person@example.test'
    assert entries[0]['entry_type']=='login' and PASSWORD not in saved.get_data(as_text=True)
