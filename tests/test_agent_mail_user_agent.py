"""Cloudflare refuses urllib's default UA (error 1010), so both agent-mail HTTP callers must name one."""
from mc.agent_mail_manifest_fetch import HttpsGetTransport
from mc.agent_mail_user_agent import USER_AGENT


class _Opener:
    def __init__(self):
        self.request = None

    def open(self, request, timeout):
        self.request = request
        raise OSError('stop')


def test_named_user_agent_is_not_urllib_default():
    assert USER_AGENT and not USER_AGENT.lower().startswith('python')


def test_manifest_get_sends_named_user_agent():
    opener = _Opener()
    try:
        HttpsGetTransport(opener=opener).get('https://example.invalid/v1/mail/manifest')
    except Exception:
        pass
    assert opener.request.get_header('User-agent') == USER_AGENT


def test_service_post_sends_named_user_agent():
    import inspect
    from mc import agent_mail_service_client as sc
    assert "'user-agent': USER_AGENT" in inspect.getsource(sc)
