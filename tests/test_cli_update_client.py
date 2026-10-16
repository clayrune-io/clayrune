"""Cross-platform CLI entry-point tests; no real HTTP, probes or installers."""
import importlib.util
import io
import json
from pathlib import Path
import urllib.error

import pytest


@pytest.fixture
def cvc(monkeypatch):
    path = Path(__file__).resolve().parent.parent / 'tools' / 'cli-version-check.py'
    spec = importlib.util.spec_from_file_location('cvc_retry_client', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, '_run', lambda *a, **k: pytest.fail('no real subprocess'))
    monkeypatch.setattr(mod, 'process_sweep_before_apply', lambda *a: pytest.fail('no sweep'))
    return mod


@pytest.mark.parametrize('status,rc', [('skipped_in_use', 1), ('updated', 0), ('up_to_date', 0)])
def test_apply_main_joins_server_retry_cycle_without_local_updates(cvc, monkeypatch, capsys, status, rc):
    monkeypatch.setattr(cvc.sys, 'argv', ['cli-version-check.py', '--apply', '--json'])
    monkeypatch.setattr(cvc, 'cli_updates_apply', lambda host: {
        'ok': True, 'checked': {'claude': status},
        'clis': [{'name': 'claude', 'status': status, 'latest': '2.1.301'}]})
    monkeypatch.setattr(cvc, 'check_one', lambda *a: pytest.fail('must not update locally'))
    assert cvc.main() == rc
    assert json.loads(capsys.readouterr().out)['clis'][0]['status'] == status


def test_apply_server_failure_is_explicit_without_local_fallback(cvc, monkeypatch, capsys):
    monkeypatch.setattr(cvc.sys, 'argv', ['cli-version-check.py', '--apply'])
    monkeypatch.setattr(cvc, 'cli_updates_apply', lambda host: {'error': 'HTTP 404 old server'})
    monkeypatch.setattr(cvc, 'check_one', lambda *a: pytest.fail('must not update locally'))
    assert cvc.main() == 1
    assert 'HTTP 404 old server' in capsys.readouterr().out


def test_apply_client_posts_only_fixed_request(cvc, monkeypatch):
    seen = []

    def open_request(req, timeout):
        seen.append((req.full_url, req.get_method(), req.data))
        return io.BytesIO(b'{"ok":true,"clis":[]}')

    monkeypatch.setattr(cvc.urllib.request, 'urlopen', open_request)
    assert cvc.cli_updates_apply('http://localhost:5199') == {'ok': True, 'clis': []}
    assert seen == [('http://localhost:5199/api/cli-updates/run', 'POST', b'{}')]


@pytest.mark.parametrize('body', [b'[]', b'{}', b'{"ok":true,"clis":["bad"]}',
                                 b'{"ok":true,"clis":[{}]}'])
def test_apply_client_refuses_invalid_report(cvc, monkeypatch, body):
    monkeypatch.setattr(cvc.urllib.request, 'urlopen', lambda *a, **k: io.BytesIO(body))
    assert 'invalid CLI update report' in cvc.cli_updates_apply('http://localhost:5199')['error']


def test_apply_client_reports_connection_failure(cvc, monkeypatch):
    def fail(*a, **k):
        raise urllib.error.URLError('connection refused')
    monkeypatch.setattr(cvc.urllib.request, 'urlopen', fail)
    assert 'connection refused' in cvc.cli_updates_apply('http://localhost:5199')['error']


def test_report_only_main_keeps_local_diagnostics(cvc, monkeypatch, capsys):
    monkeypatch.setattr(cvc.sys, 'argv', ['cli-version-check.py', '--json'])
    monkeypatch.setattr(cvc, 'cli_updates_apply', lambda *a: pytest.fail('report must not apply'))
    seen = []
    monkeypatch.setattr(cvc, 'check_one', lambda cli: seen.append(cli.name) or
                        {'name': cli.name, 'status': 'not_installed'})
    assert cvc.main() == 0
    assert len(json.loads(capsys.readouterr().out)['clis']) == len(cvc.CLIS)
    assert seen == [c.name for c in cvc.CLIS]


def test_apply_keeps_shadow_diagnostics(cvc, monkeypatch, capsys):
    monkeypatch.setattr(cvc.sys, 'argv', ['cli-version-check.py', '--apply', '--json'])
    monkeypatch.setattr(cvc, 'cli_updates_apply', lambda host: {'ok': True, 'clis': [
        {'name': 'claude', 'status': 'up_to_date', 'path': '/first/claude'}]})
    monkeypatch.setattr(cvc, 'shadow_check', lambda *a: ['/other/claude'])
    assert cvc.main() == 1
    assert json.loads(capsys.readouterr().out)['clis'][0]['shadowed_by'] == ['/other/claude']
