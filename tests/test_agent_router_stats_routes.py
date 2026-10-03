"""/api/router/stats after its carve-out of agent_routes.py.

tests/test_agent_routes.py only checks status 200 + a dict. These pin what the
move had to preserve: the aggregation itself, the per-request DATA_DIR lookup
(wire() binds it late), and the Flask endpoint name staying agent_routes.*.
"""
import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture()
def client_and_dir(tmp_path, monkeypatch):
    import server
    from mc.blueprints import agent_routes as ar
    data_dir = tmp_path / 'projects'
    data_dir.mkdir()
    monkeypatch.setattr(ar, 'DATA_DIR', data_dir)
    server.app.config['TESTING'] = True
    return server.app.test_client(), data_dir


@pytest.mark.usefixtures('client_and_dir')
def test_endpoint_stays_on_agent_routes_blueprint():
    import server
    rules = [r for r in server.app.url_map.iter_rules()
             if r.rule == '/api/router/stats']
    assert [r.endpoint for r in rules] == ['agent_routes.get_router_stats_aggregate']


def test_old_import_name_still_resolves():
    from mc.blueprints import agent_routes as ar
    assert callable(ar.get_router_stats_aggregate)


def test_aggregates_across_projects_and_latest_fallback_wins(client_and_dir):
    client, data_dir = client_and_dir
    (data_dir / 'alpha_router_stats.json').write_text(json.dumps({
        'totals': {'manual': 1, 'auto': 2, 'fallback': 1},
        'by_pair': {'opus->haiku': 3},
        'last_fallback': {'ts': '2026-10-01T00:00:00Z', 'reason': 'old'},
    }), encoding='utf-8')
    (data_dir / 'beta_router_stats.json').write_text(json.dumps({
        'totals': {'auto': 5},
        'by_pair': {'opus->haiku': 1, 'sonnet->haiku': 2},
        'last_fallback': {'ts': '2026-10-02T00:00:00Z', 'reason': 'new'},
    }), encoding='utf-8')
    (data_dir / 'bad_router_stats.json').write_text('{not json', encoding='utf-8')
    (data_dir / 'list_router_stats.json').write_text('[1]', encoding='utf-8')

    body = client.get('/api/router/stats').get_json()
    assert body == {
        'totals': {'manual': 1, 'auto': 7, 'fallback': 1},
        'by_pair': {'opus->haiku': 4, 'sonnet->haiku': 2},
        'last_fallback': {'ts': '2026-10-02T00:00:00Z', 'reason': 'new',
                          'project_id': 'beta'},
        'projects': 2,
    }


def test_empty_data_dir(client_and_dir):
    client, _ = client_and_dir
    assert client.get('/api/router/stats').get_json() == {
        'totals': {}, 'by_pair': {}, 'last_fallback': None, 'projects': 0}
