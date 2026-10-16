"""Pure policy edge cases; integration and server tests live in test_cli_update."""
from mc import cli_update_retry as retry


def test_legacy_skip_uses_checked_timestamp_as_cache_anchor():
    entry = {'status': 'skipped_in_use', 'checked_ts': 100,
             'latest': '2.1.301', 'latest_source': 'npm'}
    assert not retry.due(entry, 3699)
    assert retry.due(entry, 3700)
    assert retry.cached_latest(entry, 3700) == ('2.1.301', 'npm')
    assert retry.cached_latest(entry, 100 + retry.CHECK_INTERVAL_S) is None


def test_future_or_missing_cache_never_reused():
    entry = {'status': 'skipped_in_use', 'checked_ts': 100, 'latest': '2.1.301'}
    assert retry.cached_latest(entry, 99) is None
    assert retry.cached_latest({**entry, 'latest': None}, 3700) is None
    assert retry.cached_latest({**entry, 'status': 'update_failed'}, 3700) is None
    assert retry.cached_latest(None, 3700) is None
