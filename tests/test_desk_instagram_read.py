"""Instagram is a Desk own-read through the browser pane (backlog 92c4f9c6).

The profile declares read_own (mentions, post_metrics) on `instagram-browser`, the executor
table (`mc/desk_connect/instagram_read.py`) says the pane runs it, and the generic pane reader
is what a project's Instagram account gets. SYNTHETIC ONLY: no browser, no network, nothing
here has run against instagram.com.
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc import desk as _desk  # noqa: E402
from mc import desk_engagement as eng  # noqa: E402
from mc import desk_engagement_pane_digest as pd  # noqa: E402
from mc.desk_connect import registry, route_readiness  # noqa: E402
from tests.test_desk_engagement_pane_digest import env  # noqa: E402,F401

ROUTE = 'instagram-browser'


def test_profile_declares_the_pane_read_own_coverage():
    prof = registry.profile('instagram')
    route = next(r for r in prof['routes'] if r['id'] == ROUTE)
    assert {(c['purpose'], c['capability'], c['account_kind']) for c in route['coverage']} == {
        ('read_own', 'mentions', 'account'), ('read_own', 'post_metrics', 'account')}
    purpose = next(p for p in prof['purposes'] if p['id'] == 'read_own')
    assert purpose['routes'] == [ROUTE] and set(purpose['capabilities']) == {'mentions', 'post_metrics'}


def test_the_pane_executes_instagram_read_own_and_nothing_else_is_claimed():
    assert route_readiness.executor('instagram', ROUTE, 'read_own') == 'pane'
    assert route_readiness.executor('instagram', ROUTE, 'publish') is None
    route = next(r for r in registry.profile('instagram')['routes'] if r['id'] == ROUTE)
    st = route_readiness.route_status('instagram', route, 'read_own')
    assert st['executes'] is True and st['via'] == 'pane' and st['bindable'] is True and st['why'] is None


def test_an_instagram_account_gets_the_generic_pane_reader(env):
    _desk.upsert_presence('ig-proj', {'accounts': [
        {'channel_id': 'ig-1', 'platform': 'instagram', 'browser_profile': 'main'}]})
    reader = eng.readers_for_project('ig-proj')['instagram']
    assert isinstance(reader, pd.PaneDigestReader) and reader.via == 'pane'
