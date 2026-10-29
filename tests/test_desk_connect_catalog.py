"""Sources projects campaign destinations without private setup data."""
import pytest
from flask import Flask

from mc import desk_accounts
from mc.blueprints.desk_connect_routes import bp
from mc.desk_connect import registry


def catalog():
    app = Flask(__name__)
    app.register_blueprint(bp)
    response = app.test_client().get('/api/desk/connect/services')
    assert response.status_code == 200
    return response.get_json()['services']


def test_catalog_is_complete_destination_projection():
    rows = catalog()
    expected = {
        s['id'] for s in registry.registry()['services']
        if s['id'] in desk_accounts.ACCOUNT_PLATFORMS
        or any(p['id'] == 'publish' for p in s['profile']['purposes'])
    }
    assert {s['id'] for s in rows} == expected
    assert all(set(s) == {'id', 'label', 'host', 'url'} for s in rows)
    assert {'youtube', 'instagram'} <= {s['id'] for s in rows}


@pytest.mark.parametrize('source', ['publish', 'channel'])
def test_catalog_does_not_hide_new_destination_entries(monkeypatch, source):
    if source == 'channel':
        monkeypatch.setattr(desk_accounts, 'ACCOUNT_PLATFORMS', (*desk_accounts.ACCOUNT_PLATFORMS, 'new-service'))
    monkeypatch.setattr(registry, 'registry', lambda: {'services': [
        {'id': 'new-service', 'label': 'New service', 'url': 'https://new.example',
         'private': 'never exposed', 'profile': {'purposes': [{'id': 'publish'}] if source == 'publish' else []}},
    ]})
    assert catalog() == [
        {'id': 'new-service', 'label': 'New service', 'host': 'new.example', 'url': 'https://new.example'},
    ]


@pytest.mark.parametrize('service_id', ['higgsfield', 'google_ai', 'openai', 'notion', 'google_drive', 'google_photos'])
def test_catalog_excludes_generators_and_storage(service_id):
    assert service_id in {s['id'] for s in registry.registry()['services']}, 'exclusion must cover a real profile'
    assert service_id not in {s['id'] for s in catalog()}


def test_catalog_excludes_new_non_destination(monkeypatch):
    monkeypatch.setattr(registry, 'registry', lambda: {'services': [
        {'id': 'new-reader', 'label': 'Reader', 'url': 'https://read.example',
         'profile': {'purposes': [{'id': 'read_own'}]}},
    ]})
    assert catalog() == []
