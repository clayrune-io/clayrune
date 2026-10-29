"""New leaf inspection and unchanged human-only Activate gates in a test app."""
from __future__ import annotations

import json

import pytest

from mc.blueprints import dev_harness_routes as routes
from mc import caller_attribution as ca
from test_dev_harness_diff import repo
from test_dev_harness_native_plan import inputs, inspect
from test_dev_harness_activation_evidence import ready, compose
from test_dev_harness_routes import client, BROWSER, URL


def bind(monkeypatch, inputs):
    monkeypatch.setattr(routes, '_store', inputs[0])
    monkeypatch.setattr(routes, '_load_project', lambda identity: inputs[1] if identity == 'one' else None)


def plan_body(inputs):
    return {'expected_revision':0,'draft':inputs[2],'steps':inputs[3],'groups':inputs[4]}


def test_agent_can_inspect_but_never_approve(client, inputs, monkeypatch):
    c, _ = client
    bind(monkeypatch, inputs)
    result = c.post(URL+'/native-plan', json=plan_body(inputs))
    assert result.status_code == 200
    assert result.get_json()['authorizes_merge'] is False
    monkeypatch.setattr(routes, '_project', lambda *_: pytest.fail('agent approval must refuse before reading'))
    assert c.post(URL+'/activate', data=b'invalid JSON').status_code == 403
    assert inputs[0].read('one') is None


def test_native_plan_exact_fields_revision_and_duplicate_json(client, inputs, monkeypatch):
    c, _ = client
    bind(monkeypatch, inputs)
    body = plan_body(inputs)
    for field in ('argv', 'evidence', 'environment', 'native_plan_digest', 'approve'):
        result = c.post(URL+'/native-plan', json={**body, field: True})
        assert result.status_code == 400
    for raw in (b'{"draft":{},"draft":{}}', b'null', b'{"x":NaN}'):
        assert c.post(URL+'/native-plan',data=raw, content_type='application/json').status_code == 400
    assert c.post(URL+'/native-plan',json={**body,'expected_revision':True}).status_code == 400
    assert c.post(URL+'/native-plan',json={**body,'expected_revision':1}).status_code == 409
    assert c.post('/api/project/missing/dev-harness/native-plan',json=body).status_code == 404
    assert inputs[0].read('one') is None


def test_human_preview_exact_plan_and_native_enforce_route(client, ready, monkeypatch):
    c, _ = client
    inputs, proposal, _, _ = ready
    bind(monkeypatch, inputs)
    evidence = compose(ready)
    data = {'expected_revision':1,'draft':{k:proposal['policy'][k] for k in ('profile','practices','digests','native_checks')},
            'mode':'enforce', 'native_plan_digest':proposal['native_plan_digest'],
            'acknowledged_native_losses':[], 'evidence_id':evidence['evidence_id'],
            'acknowledged_losses':proposal['protection_losses'], 'passcode':'fixture-proof'}
    wrong = c.post(URL+'/activate',json={**data,'native_plan_digest':'b'*64},headers=BROWSER)
    assert wrong.status_code != 200 and inputs[0].read('one')['revision']==1
    ok = c.post(URL+'/activate',json=data,headers=BROWSER)
    assert ok.status_code == 200, ok.get_json()
    assert ok.get_json()['mode']=='enforce'
    assert c.post(URL+'/activate',json=data,headers=BROWSER).status_code == 409
    # A2a intentionally has no executable coordinator or merge gate yet.
    assert c.get(URL).get_json()['merge_enforcement_available'] is False
    for route in ('preview','run','runs'):
        assert c.post(URL+'/'+route,json={}).status_code == 404


def test_duplicate_activation_keys_rejected_after_agent_gate(client, inputs, monkeypatch):
    c, _ = client
    bind(monkeypatch, inputs)
    raw = '{"passcode":"fixture-proof","mode":"preview","mode":"off"}'
    assert c.post(URL+'/activate',data=raw,content_type='application/json').status_code == 403
    assert c.post(URL+'/activate',data=raw,content_type='application/json',headers=BROWSER).status_code == 400
    assert inputs[0].read('one') is None


@pytest.mark.parametrize('field,value', [('draft',[]),('draft',None),('groups',{}),('steps',{})])
def test_malformed_declarative_types_return_400(client, inputs, monkeypatch, field, value):
    c,_=client
    bind(monkeypatch,inputs)
    assert c.post(URL+'/native-plan',json={**plan_body(inputs),field:value}).status_code==400


@pytest.mark.parametrize('value',[None,False,[],{}])
def test_malformed_coverage_path_returns_400(client,inputs,monkeypatch,value):
    c,_=client
    bind(monkeypatch,inputs)
    inputs[2]['native_checks']['unit']['severity']='warning'
    inputs[2]['practices']['P02']['caps']={'changed_line_floor_percent':75}
    inputs[3][0].update(recipe_id='coverage-python',coverage={'report':value,'format':'coverage.py-xml-7.10.6',
        'source_roots':['.'],'floor_percent':75,'diff_launcher':inputs[3][0]['launcher']})
    assert c.post(URL+'/native-plan',json=plan_body(inputs)).status_code==400
