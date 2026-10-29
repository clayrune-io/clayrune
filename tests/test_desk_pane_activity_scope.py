"""Company activity normalization never substitutes a different admin source."""
import pytest

from mc.desk_pane_activity_scope import normalize_activity_url


@pytest.mark.parametrize('host', ['linkedin.com', 'www.linkedin.com', 'www.linkedin.com:443'])
@pytest.mark.parametrize('slash', ['', '/'])
@pytest.mark.parametrize('tail', ['', '?trk=admin#notifications'])
def test_bare_admin_activity_expands_and_preserves_other_url_parts(host, slash, tail):
    url = f'https://{host}/company/777/admin{slash}{tail}'
    expected = f'https://{host}/company/777/admin/notifications/all/{tail}'
    assert normalize_activity_url(url) == expected
    assert normalize_activity_url(expected) == expected


@pytest.mark.parametrize('url', [
    'https://www.linkedin.com/company/777/admin/notifications/',
    'https://www.linkedin.com/company/777/admin/notifications/all/?filter=comments#top',
    'https://www.linkedin.com/company/777/admin/page-posts/published/',
    'https://www.linkedin.com/company/777/admin/analytics?tab=engagement',
    'https://www.linkedin.com/company/777/',
    'https://www.linkedin.com/company/777/admin-other/',
    'https://www.linkedin.com/notifications/',
    'https://www.linkedin.com/company/777/admin//',
    'https://www.linkedin.com/company/777/./admin/',
    'https://www.linkedin.com/company/777/%2e/admin/',
    'https://www.linkedin.com/company/777/admin/../admin/',
    'https://example.com/company/777/admin/',
    'http://www.linkedin.com/company/777/admin/',
    'https://www.linkedin.com.evil.test/company/777/admin/',
    'https://[bad/company/777/admin',
])
def test_other_paths_and_invalid_destinations_are_not_rewritten(url):
    assert normalize_activity_url(url) == url
