"""Backlog 1d940d0f: tools/pod-usage-runner/pod_usage_runner.py — runs in the
pod, reads the pod's own credentials, publishes ONLY the numbers document.
The token must never reach the document, stdout or stderr."""
import importlib.util
import io
import json
import sys
import urllib.error
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
_spec = importlib.util.spec_from_file_location(
    'pod_usage_runner', PROJECT_ROOT / 'tools' / 'pod-usage-runner' / 'pod_usage_runner.py')
runner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(runner)

TOKEN = 'sk-ant-oat01-SUPER-SECRET-TOKEN'
USAGE = {
    'five_hour': {'utilization': 7.0, 'resets_at': '2026-10-03T20:00:00+00:00'},
    'seven_day': {'utilization': 31.0, 'resets_at': '2026-10-08T00:00:00+00:00'},
    'seven_day_opus': None,
    'extra_usage': {'is_enabled': False, 'utilization': 0},
    'org_id': 'org-should-not-be-published',
    'email': 'someone@example.com',
}


class _Resp(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): return False


@pytest.fixture()
def creds(tmp_path):
    p = tmp_path / '.credentials.json'
    p.write_text(json.dumps({'claudeAiOauth': {'accessToken': TOKEN, 'refreshToken': 'R-' + TOKEN}}),
                 encoding='utf-8')
    return p


def test_writes_numbers_document_without_the_token(creds, tmp_path, monkeypatch, capsys):
    sent = {}
    def fake(req, timeout=None):
        sent['auth'] = req.get_header('Authorization')
        return _Resp(json.dumps(USAGE).encode())
    monkeypatch.setattr(runner.urllib.request, 'urlopen', fake)
    out = tmp_path / 'run' / 'usage.json'

    rc = runner.main(['--once', '--out', str(out), '--credentials', str(creds), '--claude-version', '2.1.0'])

    assert rc == 0
    assert sent['auth'] == f'Bearer {TOKEN}'          # it did use the token to call out...
    raw = out.read_text(encoding='utf-8')
    doc = json.loads(raw)
    assert doc['five_hour']['utilization'] == 7.0 and doc['seven_day']['utilization'] == 31.0
    assert 'sampled_at' in doc
    assert set(doc) <= set(runner.PUBLISHED_KEYS) | {'sampled_at'}
    assert 'org_id' not in doc and 'email' not in doc
    captured = capsys.readouterr()
    for text in (raw, captured.out, captured.err):    # ...and it never wrote it anywhere
        assert TOKEN not in text and 'SECRET' not in text


@pytest.mark.parametrize('failure', [
    urllib.error.HTTPError('https://x', 401, 'Unauthorized: ' + TOKEN, {}, None),
    RuntimeError('boom ' + TOKEN),
    urllib.error.URLError('dns ' + TOKEN),
])
def test_failures_never_echo_the_token_and_write_nothing(creds, tmp_path, monkeypatch, capsys, failure):
    def fake(req, timeout=None):
        raise failure
    monkeypatch.setattr(runner.urllib.request, 'urlopen', fake)
    out = tmp_path / 'usage.json'

    rc = runner.main(['--once', '--out', str(out), '--credentials', str(creds)])

    assert rc == 1
    assert not out.exists()
    captured = capsys.readouterr()
    assert TOKEN not in captured.out and TOKEN not in captured.err
    assert 'SECRET' not in captured.out + captured.err


def test_missing_credentials_is_a_clean_failure(tmp_path, capsys):
    rc = runner.main(['--once', '--out', str(tmp_path / 'u.json'),
                      '--credentials', str(tmp_path / 'nope.json')])
    assert rc == 1
    assert not (tmp_path / 'u.json').exists()


def test_document_is_readable_by_the_service_side_runner_source(creds, tmp_path, monkeypatch):
    """Round trip: what the runner writes is exactly what the server's
    runner source accepts."""
    from mc import claude_usage_source as cus
    from mc import state
    monkeypatch.setattr(runner.urllib.request, 'urlopen',
                        lambda req, timeout=None: _Resp(json.dumps(USAGE).encode()))
    out = tmp_path / 'usage.json'
    assert runner.main(['--once', '--out', str(out), '--credentials', str(creds),
                        '--claude-version', '2.1.0']) == 0
    monkeypatch.setitem(state.CONFIG, 'claude_usage_source', 'runner')
    monkeypatch.setitem(state.CONFIG, 'claude_usage_runner_doc', str(out))
    cus._oauth_usage_cache['ts'] = 0.0
    cus._oauth_usage_cache['data'] = None
    try:
        got = cus.fetch_oauth_usage_limits()
    finally:
        cus._oauth_usage_cache['ts'] = 0.0
        cus._oauth_usage_cache['data'] = None
    assert got['five_hour']['utilization'] == 7.0
    assert got['seven_day']['utilization'] == 31.0
