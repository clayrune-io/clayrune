"""Real route caller/passcode gates and state contracts on temporary projects."""
from __future__ import annotations

import json

import pytest
from flask import Flask

from mc import caller_attribution as ca
from mc.blueprints import dev_harness_routes as routes, local_auth
from mc.dev_harness_catalogue import load_catalogue
from mc.dev_harness_policy import lost_protections, resolve_policy

BROWSER = {'Origin': 'http://localhost:5199'}
URL = '/api/project/one/dev-harness'


@pytest.fixture
def client(tmp_path, monkeypatch):
    projects = {'one': {'id': 'one', 'project_path': str(tmp_path / 'one')},
                'two': {'id': 'two', 'project_path': str(tmp_path / 'two')}}
    monkeypatch.setattr(routes, '_store', None)
    monkeypatch.setattr(routes, '_load_project', lambda _: None)
    routes.wire(data_root=tmp_path / 'data', load_project_fn=projects.get)
    monkeypatch.setattr(ca, 'attribute_caller', lambda *args: ca.Attribution(ca.UNATTRIBUTED))
    monkeypatch.setattr(local_auth, '_local_auth_is_configured', lambda: True)
    monkeypatch.setattr(local_auth, '_local_auth_try_passcode', lambda value: value == 'fixture-proof')
    app = Flask(__name__)
    app.config['TESTING'] = True
    app.register_blueprint(routes.bp)
    with app.test_client() as c:
        yield c, tmp_path


def body(mode='preview', revision=0, draft=None):
    draft = draft or {'profile': 'local-hobby'}
    catalogue = load_catalogue()
    return {'passcode': 'fixture-proof', 'expected_revision': revision, 'mode': mode,
            'draft': draft, 'acknowledged_losses': lost_protections(None, resolve_policy(draft, catalogue), mode, catalogue)}


def test_unconfigured_default_read_is_preview_without_side_effects(client):
    c, root = client
    r = c.get(URL)
    assert r.status_code == 200
    data = r.get_json()
    assert data['mode'] == 'preview' and data['revision'] == 0
    assert data['status'] == 'not_configured' and data['configured'] is False
    assert data['merge_enforcement_available'] is False
    assert data['languages'] == ['python', 'javascript', 'typescript']
    assert not (root / 'data').exists()
    assert len(c.get(URL + '/catalogue').get_json()['practices']) == 22


@pytest.mark.parametrize('suffix', ['', '/catalogue'])
def test_unknown_project_is_not_fabricated(client, suffix):
    c, _ = client
    assert c.get('/api/project/absent/dev-harness' + suffix).status_code == 404


def test_plain_agent_refused_before_passcode_or_any_side_effect(client, monkeypatch):
    c, root = client
    def forbidden(*args):
        pytest.fail('passcode must not be probed by a refused caller')
    monkeypatch.setattr(local_auth, '_local_auth_try_passcode', forbidden)
    assert c.post(URL + '/activate', json=body()).status_code == 403
    assert not (root / 'data').exists()


def test_forged_origin_attributed_agent_refused(client, monkeypatch):
    c, root = client
    monkeypatch.setattr(ca, 'attribute_caller', lambda *args: ca.Attribution(ca.ATTRIBUTED, 'builder'))
    assert c.post(URL + '/activate', json=body(), headers=BROWSER).status_code == 403
    assert not (root / 'data').exists()


@pytest.mark.parametrize('proof', [None, '', 'wrong', [], {'passcode': 'fixture-proof'}])
def test_browser_needs_per_call_passcode(client, proof):
    c, root = client
    data = body()
    data['passcode'] = proof
    assert c.post(URL + '/activate', json=data, headers=BROWSER).status_code == 403
    assert not (root / 'data').exists()


def test_unconfigured_passcode_and_shared_throttle_preserved(client, monkeypatch):
    c, root = client
    monkeypatch.setattr(local_auth, '_local_auth_is_configured', lambda: False)
    assert c.post(URL + '/activate', json=body(), headers=BROWSER).get_json()['error'] == 'passcode_required'
    monkeypatch.setattr(local_auth, '_local_auth_is_configured', lambda: True)
    monkeypatch.setattr(local_auth, '_local_auth_try_passcode', lambda _: None)
    assert c.post(URL + '/activate', json=body(), headers=BROWSER).status_code == 429
    assert not (root / 'data').exists()


def test_human_preview_activation_revision_and_proof_not_persisted(client):
    c, root = client
    response = c.post(URL + '/activate', json=body(), headers=BROWSER)
    assert response.status_code == 200
    record = response.get_json()
    assert record['mode'] == 'preview' and record['revision'] == 1
    path = root / 'data/dev_harness/one/activated/active.json'
    before = path.read_bytes()
    assert b'fixture-proof' not in before and b'passcode' not in before
    assert c.post(URL + '/activate', json=body(), headers=BROWSER).status_code == 409
    assert path.read_bytes() == before
    assert c.get(URL).get_json()['active'] == record
    assert c.get('/api/project/two/dev-harness').get_json()['revision'] == 0


def test_repository_policy_cannot_activate_or_replace_active_snapshot(client):
    c, root = client
    assert c.post(URL + '/activate', json=body(), headers=BROWSER).status_code == 200
    draft = root / 'one/.clayrune/dev-harness/policy.json'
    draft.parent.mkdir(parents=True)
    draft.write_text(json.dumps({'profile': 'external-product'}), encoding='utf-8')
    data = c.get(URL).get_json()
    assert data['draft']['profile'] == 'external-product'
    assert data['active']['policy']['profile'] == 'local-hobby'
    assert data['mode'] == 'preview' and data['revision'] == 1
    draft.write_text('{"mode":"enforce"}', encoding='utf-8')
    data = c.get(URL).get_json()
    assert data['status'] == 'error' and data['draft_error']
    assert data['active']['policy']['profile'] == 'local-hobby'


def test_draft_without_active_snapshot_is_preview(client):
    c, root = client
    draft = root / 'one/.clayrune/dev-harness/policy.json'
    draft.parent.mkdir(parents=True)
    draft.write_text('{"profile":"external-product"}', encoding='utf-8')
    data = c.get(URL).get_json()
    assert data['mode'] == 'preview' and data['status'] == 'draft' and data['configured'] is True


@pytest.mark.parametrize('mutation,value', [('expected_revision', None), ('expected_revision', True),
    ('draft', []), ('mode', []), ('acknowledged_losses', 'P01'),
    ('evidence', {'status': 'pass'}), ('source', 'ui')])
def test_malformed_activation_or_client_evidence_is_rejected(client, mutation, value):
    c, root = client
    data = body()
    data[mutation] = value
    assert c.post(URL + '/activate', json=data, headers=BROWSER).status_code == 400
    assert not (root / 'data').exists()


def test_enforcement_without_evidence_never_activates(client):
    c, root = client
    data = body('enforce', draft={'profile': 'external-product'})
    response = c.post(URL + '/activate', json=data, headers=BROWSER)
    assert response.status_code == 400
    assert 'evidence' in response.get_json()['error']
    assert not (root / 'data').exists()


def test_corruption_reports_error_and_no_mode_reset(client):
    c, root = client
    c.post(URL + '/activate', json=body(), headers=BROWSER)
    path = root / 'data/dev_harness/one/activated/active.json'
    path.write_text('broken', encoding='utf-8')
    assert c.get(URL).status_code == 500
    assert c.get(URL).get_json()['status'] == 'error'
    assert c.post(URL + '/activate', json=body(revision=1), headers=BROWSER).status_code == 500
    assert path.read_text(encoding='utf-8') == 'broken'


@pytest.mark.parametrize('suffix', ['/exceptions', '/preview'])
def test_later_ticket_routes_are_absent(client, suffix):
    c, _ = client
    assert c.post(URL + suffix, json={}).status_code == 404
