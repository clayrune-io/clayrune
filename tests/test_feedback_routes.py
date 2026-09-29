"""Request-level tests for the feedback send path (mc/blueprints/feedback_routes.py).

Backlog f638e8d9 (MC-904), FEEDBACK CHANNEL 1/3. Covers: rate limit, honeypot,
payload-equals-visible-text (no silently appended fields), and the honest
mailer_not_configured failure when no 'feedback-mailer' vault secret exists
(true of every fresh install, including this one — see the module docstring
in feedback_routes.py for why).
"""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture()
def client(monkeypatch):
    """Flask test client with the feedback route's rate-limit state isolated
    per test (module-level dict, so a leftover count from another test would
    otherwise leak in)."""
    import server
    from mc.blueprints import feedback_routes as fr
    monkeypatch.setattr(fr, '_SEND_LOG', {})
    server.app.config['TESTING'] = True
    return server.app.test_client()


def _post(client, **body):
    return client.post('/api/feedback', json=body)


class TestValidation:
    def test_empty_message_rejected(self, client):
        r = _post(client, message='   ', reply_to='')
        assert r.status_code == 400
        assert r.get_json()['error'] == 'empty_message'

    def test_message_too_long_rejected(self, client):
        r = _post(client, message='x' * 8001)
        assert r.status_code == 400
        assert r.get_json()['error'] == 'message_too_long'

    def test_email_too_long_rejected(self, client):
        r = _post(client, message='hi', reply_to='a' * 321)
        assert r.status_code == 400
        assert r.get_json()['error'] == 'email_too_long'


class TestHoneypot:
    def test_honeypot_filled_reports_fake_success_without_sending(self, client, monkeypatch):
        from mc.blueprints import feedback_routes as fr
        called = []
        monkeypatch.setattr(fr, '_send', lambda *a, **k: called.append(1) or (True, ''))
        r = _post(client, message='hello', hp_topic='I am a bot')
        assert r.status_code == 200
        assert r.get_json() == {'ok': True}
        assert called == []  # _send was never invoked

    def test_honeypot_does_not_count_against_rate_limit(self, client, monkeypatch):
        from mc.blueprints import feedback_routes as fr
        monkeypatch.setattr(fr, '_send', lambda *a, **k: (False, 'mailer_not_configured'))
        for _ in range(5):
            _post(client, message='hello', hp_topic='bot')
        # Rate limiter never incremented for honeypot hits, so a real send
        # right after still gets through the limiter (fails downstream on
        # the mailer instead, proving it wasn't rate-limited).
        r = _post(client, message='hello')
        assert r.get_json()['error'] == 'mailer_not_configured'


class TestRateLimit:
    def test_third_send_ok_fourth_rate_limited(self, client, monkeypatch):
        from mc.blueprints import feedback_routes as fr
        monkeypatch.setattr(fr, '_send', lambda *a, **k: (True, ''))
        for _ in range(3):
            r = _post(client, message='hello')
            assert r.status_code == 200
        r = _post(client, message='hello')
        assert r.status_code == 429
        assert r.get_json()['error'] == 'too_many_attempts'

    def test_rate_limit_is_per_ip(self):
        # Exercises the limiter directly rather than over HTTP: a non-loopback
        # source IP is also gated by the unrelated LAN-passcode wall
        # (mc/blueprints/local_auth.py), so routing a second IP through the
        # full stack would conflate that gate's 401/429 with this route's own.
        from mc.blueprints import feedback_routes as fr
        fr._SEND_LOG = {}
        for _ in range(3):
            assert fr._rate_limited('1.2.3.4') is False
        assert fr._rate_limited('1.2.3.4') is True
        assert fr._rate_limited('5.6.7.8') is False


class TestPayloadShape:
    def test_payload_is_exactly_visible_fields_no_silent_extras(self, client, monkeypatch):
        """The server sends exactly what the caller supplied — message and
        reply_to — nothing else appended (backlog privacy constraint (c))."""
        from mc.blueprints import feedback_routes as fr
        captured = {}

        def fake_send(message, reply_to):
            captured['message'] = message
            captured['reply_to'] = reply_to
            return True, ''
        monkeypatch.setattr(fr, '_send', fake_send)

        sent_message = 'Clayrune v2.4.2 on Windows 11\n\nDoes this work with Codex?'
        r = _post(client, message=sent_message, reply_to='me@example.com')
        assert r.status_code == 200
        assert captured['message'] == sent_message
        assert captured['reply_to'] == 'me@example.com'

    def test_unknown_extra_fields_are_ignored_not_forwarded(self, client, monkeypatch):
        from mc.blueprints import feedback_routes as fr
        captured = {}
        monkeypatch.setattr(fr, '_send', lambda m, rt: (captured.update(message=m, reply_to=rt), (True, ''))[1])
        r = client.post('/api/feedback', json={
            'message': 'hi', 'reply_to': '', 'hostname': 'ROGUE-PC', 'user_id': 'abc123',
        })
        assert r.status_code == 200
        assert captured == {'message': 'hi', 'reply_to': ''}


class TestMailerNotConfigured:
    def test_default_install_has_no_feedback_mailer_secret(self, client, tmp_path, monkeypatch):
        """On a fresh install (and on this dev checkout, before a human adds
        the 'feedback-mailer' vault secret) the route must fail honestly,
        never claim success without actually sending."""
        monkeypatch.setenv('CLAYRUNE_HOME', str(tmp_path / '.clayrune'))
        monkeypatch.setenv('CLAYRUNE_SECRETS_KEY_BACKEND', 'file')
        r = _post(client, message='does this work with Codex?')
        assert r.status_code == 502
        assert r.get_json()['error'] == 'mailer_not_configured'


class TestContextEndpoint:
    def test_context_returns_version_and_os(self, client):
        r = client.get('/api/feedback/context')
        assert r.status_code == 200
        j = r.get_json()
        assert 'version' in j and j['version']
        assert 'os' in j and j['os']
