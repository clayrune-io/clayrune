"""Backlog 1d940d0f: `claude_usage_source` ('local' default | 'runner').

'runner' reads a numbers-only document the pod runner publishes and must
NEVER open ~/.claude/.credentials.json (the hosted service sits outside the
pod; reading the credential file there is collecting it). 'local' is today's
behaviour, unchanged.
"""
import builtins
import io
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc import claude_usage_source as cus
from mc import state

WINDOWS = {
    'five_hour': {'utilization': 12.5, 'resets_at': '2026-10-03T20:00:00+00:00'},
    'seven_day': {'utilization': 40.0, 'resets_at': '2026-10-08T00:00:00+00:00'},
    'seven_day_opus': None,
}


@pytest.fixture(autouse=True)
def _clean_cache():
    cus._oauth_usage_cache['ts'] = 0.0
    cus._oauth_usage_cache['data'] = None
    yield
    cus._oauth_usage_cache['ts'] = 0.0
    cus._oauth_usage_cache['data'] = None


def _cfg(monkeypatch, source, doc=None):
    monkeypatch.setitem(state.CONFIG, 'claude_usage_source', source)
    monkeypatch.setitem(state.CONFIG, 'claude_usage_runner_doc', doc or '')


def _doc(path, **over):
    d = dict(WINDOWS)
    d['sampled_at'] = datetime.now(timezone.utc).isoformat()
    d.update(over)
    path.write_text(json.dumps(d), encoding='utf-8')
    return str(path)


def _forbid_credentials(monkeypatch, tmp_path):
    """Plant a real-looking credentials file under a fake home, then make any
    attempt to open/read it (or to call Anthropic) fail the test."""
    cred_dir = tmp_path / 'home' / '.claude'
    cred_dir.mkdir(parents=True)
    (cred_dir / '.credentials.json').write_text(
        json.dumps({'claudeAiOauth': {'accessToken': 'sk-ant-SECRET-TOKEN'}}), encoding='utf-8')
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path / 'home'))
    touched = []

    real_open = builtins.open
    def guarded_open(file, *a, **k):
        if '.credentials.json' in str(file):
            touched.append(str(file))
            raise AssertionError('.credentials.json opened')
        return real_open(file, *a, **k)
    monkeypatch.setattr(builtins, 'open', guarded_open)

    real_read_text = Path.read_text
    def guarded_read_text(self, *a, **k):
        if '.credentials.json' in str(self):
            touched.append(str(self))
            raise AssertionError('.credentials.json read')
        return real_read_text(self, *a, **k)
    monkeypatch.setattr(Path, 'read_text', guarded_read_text)

    def no_network(req, *a, **k):
        url = getattr(req, 'full_url', req)
        raise AssertionError(f'unexpected network call: {url}')
    monkeypatch.setattr(cus.urllib.request, 'urlopen', no_network)
    return touched


class TestSourceResolution:
    @pytest.mark.parametrize('raw,want', [
        (None, 'local'), ('', 'local'), ('local', 'local'), (' LOCAL ', 'local'),
        ('runner', 'runner'), ('Runner', 'runner'),
        ('runer', 'runner'),   # typo lands on the side that never opens credentials
    ])
    def test_normalisation(self, monkeypatch, raw, want):
        monkeypatch.setitem(state.CONFIG, 'claude_usage_source', raw)
        assert cus.usage_source() == want

    def test_server_declares_the_defaults(self):
        import server  # noqa: F401
        assert state.CONFIG.get('claude_usage_source', 'local') == 'local'


class TestRunnerSource:
    def test_reads_document_and_never_touches_credentials(self, monkeypatch, tmp_path):
        touched = _forbid_credentials(monkeypatch, tmp_path)
        _cfg(monkeypatch, 'runner', _doc(tmp_path / 'usage.json'))
        out = cus.fetch_oauth_usage_limits()
        assert out['five_hour'] == WINDOWS['five_hour']
        assert out['seven_day']['utilization'] == 40.0
        assert 'sampled_at' not in out
        assert touched == []

    def test_unset_document_path_is_none_and_never_touches_credentials(self, monkeypatch, tmp_path):
        touched = _forbid_credentials(monkeypatch, tmp_path)
        _cfg(monkeypatch, 'runner', '')
        assert cus.fetch_oauth_usage_limits() is None
        assert touched == []

    def test_missing_or_garbage_document_is_none(self, monkeypatch, tmp_path):
        touched = _forbid_credentials(monkeypatch, tmp_path)
        _cfg(monkeypatch, 'runner', str(tmp_path / 'nope.json'))
        assert cus.fetch_oauth_usage_limits() is None
        bad = tmp_path / 'bad.json'
        bad.write_text('not json', encoding='utf-8')
        _cfg(monkeypatch, 'runner', str(bad))
        assert cus.fetch_oauth_usage_limits() is None
        assert touched == []

    def test_stale_document_is_not_shown_as_live(self, monkeypatch, tmp_path):
        old = datetime.fromtimestamp(time.time() - 3600, tz=timezone.utc).isoformat()
        _cfg(monkeypatch, 'runner', _doc(tmp_path / 'usage.json', sampled_at=old))
        assert cus.fetch_oauth_usage_limits() is None

    def test_document_without_stamp_falls_back_to_file_mtime(self, monkeypatch, tmp_path):
        p = tmp_path / 'usage.json'
        p.write_text(json.dumps(WINDOWS), encoding='utf-8')
        _cfg(monkeypatch, 'runner', str(p))
        assert cus.fetch_oauth_usage_limits()['seven_day']['utilization'] == 40.0

    def test_only_known_keys_and_short_strings_survive(self, monkeypatch, tmp_path):
        _cfg(monkeypatch, 'runner', _doc(
            tmp_path / 'usage.json',
            access_token='sk-ant-LEAK', note='x' * 500,
            five_hour={'utilization': 1.0, 'resets_at': 'y' * 500}))
        out = cus.fetch_oauth_usage_limits()
        assert set(out) <= set(cus._RUNNER_KEYS)
        assert 'access_token' not in out and 'note' not in out
        assert out['five_hour'] == {'utilization': 1.0, 'resets_at': None}

    def test_url_document(self, monkeypatch):
        body = json.dumps({**WINDOWS, 'sampled_at': datetime.now(timezone.utc).isoformat()}).encode()
        seen = []

        class _Resp(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *a): return False

        def fake_urlopen(url, timeout=None):
            seen.append(url)
            return _Resp(body)
        monkeypatch.setattr(cus.urllib.request, 'urlopen', fake_urlopen)
        _cfg(monkeypatch, 'runner', 'https://pod.internal/claude-usage.json')
        out = cus.fetch_oauth_usage_limits()
        assert out['five_hour']['utilization'] == 12.5
        assert seen == ['https://pod.internal/claude-usage.json']

    def test_result_is_cached_and_cache_ts_set(self, monkeypatch, tmp_path):
        p = _doc(tmp_path / 'usage.json')
        _cfg(monkeypatch, 'runner', p)
        first = cus.fetch_oauth_usage_limits()
        assert cus._oauth_usage_cache['ts'] > 0
        Path(p).unlink()
        assert cus.fetch_oauth_usage_limits() is first   # served from cache within TTL


class TestLocalSourceUnchanged:
    def test_local_reads_credentials_and_calls_the_endpoint(self, monkeypatch, tmp_path):
        cred_dir = tmp_path / 'home' / '.claude'
        cred_dir.mkdir(parents=True)
        (cred_dir / '.credentials.json').write_text(
            json.dumps({'claudeAiOauth': {'accessToken': 'tok-123'}}), encoding='utf-8')
        monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path / 'home'))
        monkeypatch.setitem(state._LAST_SYSTEM_STATUS, 'claude_code_version', '9.9.9')
        _cfg(monkeypatch, 'local')
        sent = {}

        class _Resp(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *a): return False

        def fake_urlopen(req, timeout=None):
            sent['url'] = req.full_url
            sent['auth'] = req.get_header('Authorization')
            sent['ua'] = req.get_header('User-agent')
            return _Resp(json.dumps(WINDOWS).encode())
        monkeypatch.setattr(cus.urllib.request, 'urlopen', fake_urlopen)

        out = cus.fetch_oauth_usage_limits()
        assert out == WINDOWS
        assert sent == {'url': 'https://api.anthropic.com/api/oauth/usage',
                        'auth': 'Bearer tok-123', 'ua': 'claude-code/9.9.9'}

    def test_local_without_credentials_is_none(self, monkeypatch, tmp_path):
        monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path / 'empty'))
        _cfg(monkeypatch, 'local')
        assert cus.fetch_oauth_usage_limits() is None

    def test_system_routes_still_exposes_the_old_names(self):
        import server  # noqa: F401
        from mc.blueprints import system_routes as sr
        assert sr._oauth_usage_cache is cus._oauth_usage_cache
        assert sr._fetch_oauth_usage_limits is cus.fetch_oauth_usage_limits
