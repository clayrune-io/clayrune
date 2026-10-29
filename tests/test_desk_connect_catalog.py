"""Sources uses the complete existing Connect registry, without private setup data."""
from flask import Flask

from mc.blueprints.desk_connect_routes import bp
from mc.desk_connect import registry


def test_catalog_is_complete_public_projection():
    app = Flask(__name__)
    app.register_blueprint(bp)
    response = app.test_client().get('/api/desk/connect/services')
    assert response.status_code == 200
    rows = response.get_json()['services']
    assert {s['id'] for s in rows} == {s['id'] for s in registry.registry()['services']}
    assert all(set(s) == {'id', 'label', 'host', 'url'} for s in rows)
    assert {'youtube', 'instagram'} <= {s['id'] for s in rows}


def test_catalog_does_not_hide_new_registry_entries(monkeypatch):
    monkeypatch.setattr(registry, 'registry', lambda: {'services': [
        {'id': 'new-service', 'label': 'New service', 'url': 'https://new.example', 'private': 'never exposed'},
    ]})
    app = Flask(__name__)
    app.register_blueprint(bp)
    assert app.test_client().get('/api/desk/connect/services').get_json() == {'services': [
        {'id': 'new-service', 'label': 'New service', 'host': 'new.example', 'url': 'https://new.example'},
    ]}
