"""Request-level tests for the feedback context endpoint (mc/blueprints/feedback_routes.py).

Backlog f638e8d9 (MC-904), REWORK 2026-09-29: the POST /api/feedback send
path (SMTP/vault, rate limit, honeypot) was removed — feedback now goes out
via a client-built mailto: link with no server call. Only the read-only
/api/feedback/context endpoint (version/OS for the modal's prefilled line)
remains, and is all this file covers now.
"""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture()
def client():
    import server
    server.app.config['TESTING'] = True
    return server.app.test_client()


class TestContextEndpoint:
    def test_context_returns_version_and_os(self, client):
        r = client.get('/api/feedback/context')
        assert r.status_code == 200
        j = r.get_json()
        assert 'version' in j and j['version']
        assert 'os' in j and j['os']

    def test_post_feedback_route_no_longer_exists(self, client):
        r = client.post('/api/feedback', json={'message': 'hi'})
        assert r.status_code == 404
