"""Saved-engine edits retain encrypted fields and reference edges; no live vault."""
import copy

import pytest

from mc import secrets_store as vault
from tests.test_desk_connect import env, _commit_req


def draft(fields):
    return {'url':'https://higgsfield.ai','method':'api_key','fields':fields}


@pytest.mark.parametrize('field',[None,'secret','key_id'])
def test_update_keeps_untouched_pair_half_and_changes_policy(env, monkeypatch, field):
    client,_,_=env
    from mc import desk_engine_schemas
    monkeypatch.setattr(desk_engine_schemas,'connection_saved',lambda *a:None)
    vault.set_secret('higgsfield','old-value',username='old-id',scope='global',allow_unattended=False)
    vault.set_secret('source','new-value',username='new-id')
    fields={'edit_saved':True,'secret':{'keep':True},'key_id':{'keep':True},'allow_unattended':True}
    if field:
        fields[field]={'name':'source','part':'value' if field=='secret' else 'username'}
        fields['confirm_replace']=True
    out=_commit_req(client,draft(fields))
    assert out.status_code==201, out.get_json()
    assert vault.get_secret_value('higgsfield',consumer='test')==('new-value' if field=='secret' else 'old-value')
    assert vault.get_username('higgsfield')==('new-id' if field=='key_id' else 'old-id')
    if field=='key_id':
        assert next(s for s in vault.list_secrets() if s['name']=='higgsfield')['username']==''
    assert next(s for s in vault.list_secrets() if s['name']=='higgsfield')['allow_unattended'] is True
    assert 'old-value' not in out.get_data(as_text=True) and 'new-value' not in out.get_data(as_text=True)


def test_update_keeps_existing_reference_and_scope(env,monkeypatch):
    from mc import desk_engine_schemas, secret_field_refs
    monkeypatch.setattr(desk_engine_schemas,'connection_saved',lambda *a:None)
    client,_,_=env
    vault.set_secret('source','from-source',username='id')
    secret_field_refs.create('higgsfield',{'name':'source','part':'value'},username='original-id',allow_unattended=False)
    before=copy.deepcopy(vault._load_store()['secrets']['higgsfield'])
    out=_commit_req(client,draft({'edit_saved':True,'secret':{'keep':True},'key_id':{'keep':True},'allow_unattended':True}))
    assert out.status_code==201, out.get_json()
    after=vault._load_store()['secrets']['higgsfield']
    assert after['field_refs']==before['field_refs'] and after['scope']==before['scope']
    assert after['allow_unattended'] is True


def test_replacement_still_needs_confirmation_and_passcode(env):
    client,_,_=env
    vault.set_secret('higgsfield','old-value',username='old-id')
    vault.set_secret('source','new-value')
    fields={'edit_saved':True,'secret':{'name':'source','part':'value'},'key_id':{'keep':True}}
    before=copy.deepcopy(vault._load_store()['secrets']['higgsfield'])
    assert _commit_req(client,draft(fields)).status_code==409
    fields['confirm_replace']=True
    assert _commit_req(client,draft(fields),passcode='wrong').status_code==403
    assert vault._load_store()['secrets']['higgsfield']==before


def test_update_cannot_create_missing_entry(env):
    client,_,_=env
    out=_commit_req(client,draft({'edit_saved':True,'secret':{'keep':True},'key_id':{'keep':True}}))
    assert out.status_code==404
    assert not vault.list_secrets()


def test_later_provider_failure_restores_complete_saved_key(env,monkeypatch):
    from mc import desk_engine_connections
    client,_,_=env
    vault.set_secret('higgsfield','old-value',username='old-id',allow_unattended=False)
    vault.set_secret('source','new-value')
    before=copy.deepcopy(vault._load_store()['secrets']['higgsfield'])
    def fail(*args):
        raise OSError('fixture failure after key edit')
    monkeypatch.setattr(desk_engine_connections,'reconnect_saved',fail)
    fields={'edit_saved':True,'secret':{'name':'source','part':'value'},'key_id':{'keep':True},
            'allow_unattended':True,'confirm_replace':True}
    out=_commit_req(client,draft(fields))
    assert out.status_code==500
    assert vault._load_store()['secrets']['higgsfield']==before


@pytest.mark.parametrize('source',['higgsfield','project-key'])
def test_edit_rejects_self_reference_and_project_source(env,source):
    client,_,_=env
    vault.set_secret('higgsfield','old-value',username='old-id')
    vault.set_secret('project-key','restricted-value',scope='project-one')
    before=copy.deepcopy(vault._load_store()['secrets']['higgsfield'])
    out=_commit_req(client,draft({'edit_saved':True,'secret':{'name':source,'part':'value'},
                                 'key_id':{'keep':True},'confirm_replace':True}))
    assert out.status_code==400
    assert vault._load_store()['secrets']['higgsfield']==before
