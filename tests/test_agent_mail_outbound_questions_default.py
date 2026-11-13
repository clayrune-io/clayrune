"""W2: the question adapter's factory defaults to the process MailFactory's outbound()."""
import importlib

import pytest

from mc import agent_mail_factory, state
from mc import agent_mail_outbound_questions as adapter

QUESTION = dict(questions=[dict(header='Day', question='Which day?', options=[dict(label='Friday')])])
SESSION = dict(project_id='p1', session_id='s1')


class FakeOutbound:
    def __init__(self):
        self.calls = []

    def question_channel(self, *args):
        self.calls.append(('question_channel', args))
        return args[-1]

    def prepare(self, *args, **kwargs):
        self.calls.append(('prepare', args, kwargs))
        return dict(intent_id='i1')

    def send(self, intent_id):
        self.calls.append(('send', intent_id))
        return dict(state='sent')


class FakeFactory:
    def __init__(self):
        self.out = FakeOutbound()

    def outbound(self):
        return self.out


@pytest.fixture
def fake(monkeypatch):
    f = FakeFactory()
    agent_mail_factory.reset_for_tests(f)
    yield f
    agent_mail_factory.reset_for_tests(None)


def test_module_default_is_the_mail_factory_outbound(fake):
    # Fresh import state: the shipped default, not a test-installed stub.
    default = importlib.import_module('mc.agent_mail_outbound_questions')._default_factory
    assert adapter.factory is default
    assert default() is fake.out


def test_default_factory_used_by_selected_and_deliver_when_flag_on(fake, monkeypatch):
    monkeypatch.setitem(state.CONFIG, 'agent_mail_outbound_enabled', True)
    assert adapter.selected(dict(SESSION), QUESTION) is True
    result = adapter.deliver(dict(SESSION), QUESTION, 'Proj')
    assert result == dict(state='sent')
    assert [c[0] for c in fake.out.calls] == ['question_channel', 'prepare', 'send']


def test_flag_off_sends_nothing_and_never_touches_the_factory(fake, monkeypatch):
    monkeypatch.setitem(state.CONFIG, 'agent_mail_outbound_enabled', False)
    assert adapter.deliver(dict(SESSION), QUESTION, 'Proj') == dict(
        state='blocked', reason='operational_adapter_unavailable')
    adapter.parsed(dict(SESSION))
    assert fake.out.calls == []


def test_flag_absent_sends_nothing(fake, monkeypatch):
    monkeypatch.delitem(state.CONFIG, 'agent_mail_outbound_enabled', raising=False)
    assert adapter.deliver(dict(SESSION), QUESTION, 'Proj')['state'] == 'blocked'
    assert fake.out.calls == []


def test_flag_on_with_no_store_holds_never_raises(monkeypatch):
    class NoStore:
        def outbound(self):
            raise agent_mail_factory.StoreHeld('not_enrolled')
    agent_mail_factory.reset_for_tests(NoStore())
    try:
        monkeypatch.setitem(state.CONFIG, 'agent_mail_outbound_enabled', True)
        assert adapter.deliver(dict(SESSION), QUESTION, 'Proj') == dict(
            state='blocked', reason='outbound_unavailable')
    finally:
        agent_mail_factory.reset_for_tests(None)
