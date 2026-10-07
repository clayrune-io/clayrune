"""Pace configuration uses the existing protected settings surface."""

from tests.test_settings_routes import ctx  # noqa: F401


def test_pace_config_round_trips_through_settings(ctx):
    config = {'engine_pace_enabled': False, 'engine_pace_margin_points': 8,
              'engine_pace_ceiling_percent': 90,
              'engine_pace_alternates': {'global:builder': 'project:alternate'}}
    response = ctx.client.put('/api/config', json=config)
    assert response.status_code == 200
    saved = ctx.client.get('/api/config').get_json()
    assert {key: saved[key] for key in config} == config
