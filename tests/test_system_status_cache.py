from mc.blueprints import system_routes as routes


def test_status_cache_atomic_failure_keeps_previous_record(tmp_path, monkeypatch, capsys):
    from mc import atomic_json

    cache = tmp_path / 'status.json'
    cache.write_text('{"model": "previous"}', encoding='utf-8')
    monkeypatch.setattr(routes, 'SYSTEM_STATUS_PATH', cache)
    monkeypatch.setattr(routes.state, '_LAST_SYSTEM_STATUS', {'model': 'new'})

    def refused_replace(*args):
        raise OSError('replace failed')

    monkeypatch.setattr(atomic_json.os, 'replace', refused_replace)
    routes._save_system_status_to_disk()

    assert cache.read_text(encoding='utf-8') == '{"model": "previous"}'
    assert 'replace failed' in capsys.readouterr().out


def test_status_cache_logs_unreadable_state(tmp_path, monkeypatch, capsys):
    cache = tmp_path / 'status.json'
    cache.write_text('{', encoding='utf-8')
    monkeypatch.setattr(routes, 'SYSTEM_STATUS_PATH', cache)
    monkeypatch.setattr(routes.state, '_LAST_SYSTEM_STATUS', {'model': 'old'})

    routes._load_system_status_from_disk()

    assert routes.state._LAST_SYSTEM_STATUS == {}
    assert 'status cache read failed' in capsys.readouterr().out


def test_status_refresh_reports_absent_cli_without_spawning(monkeypatch):
    from flask import Flask

    app = Flask(__name__)
    monkeypatch.setattr(routes, '_resolve_claude', lambda: None)
    monkeypatch.setattr(routes, '_build_system_status_payload', lambda: {})
    launches = []
    monkeypatch.setattr(routes.subprocess, 'run', lambda *args, **kw: launches.append(args))

    with app.test_request_context():
        response, status = routes.system_status_refresh()

    assert status == 500
    assert response.get_json()['error'] == 'Claude CLI not found on this server'
    assert not launches
