"""Outbound agent-mail display names: server-derived, allowlisted, never fatal."""
import json

import pytest

from test_agent_mail_outbound import out, kit, setup, env, prepare, request  # noqa: F401
from mc import agent_mail_display_name as dn
from mc.agent_mail_enrollment_keys import EnrollmentRefused
from mc.agent_mail_outbound import validate_request


@pytest.mark.parametrize('raw,want', [
    ('Dave', 'Dave'), ('  Posy   Q.  ', 'Posy Q.'), ("O'Neil-Smith_2", "O'Neil-Smith_2"),
    ('a' * 40, 'a' * 40)])
def test_allowlisted_names_pass_and_collapse_whitespace(raw, want):
    assert dn.sanitize(raw) == want


@pytest.mark.parametrize('raw', [
    'a' * 41, '', '   ', 'Dave<evil@x.com>', 'Dave' + chr(34), 'Dave' + chr(92), 'Dave;', 'Dave,x',
    'Zo' + chr(0xeb), 'Dave' + chr(0xa0) + 'X', 'Dave' + chr(13) + chr(10) + 'Bcc: evil@example.com',
    'Dave' + chr(10) + 'X', 'Dave' + chr(9) + 'X',
    'Dave' + chr(0), 'Dave' + chr(0x7f), 'Dave' + chr(0x2028) + 'X', 'Dave' + chr(0x85) + 'X',
    None, 42, ['Dave'], {}])
def test_everything_else_falls_back_never_raises(raw):
    assert dn.sanitize(raw) == dn.FALLBACK == 'Clayrune agent'


def test_lookup_failure_falls_back(monkeypatch):
    from mc.blueprints import agent_routes as ar
    def boom(project, session):
        raise RuntimeError('character file unreadable')
    monkeypatch.setattr(ar, '_session_character_parts', boom)
    assert dn.for_session({}, {}) == dn.FALLBACK


def test_request_shape_is_unchanged_agent_cannot_supply_a_name():
    ok = dict(request_id='note1', kind='note', subject='s', body='b')
    validate_request(ok)
    for extra in ('display_name', 'from', 'name'):
        with pytest.raises(EnrollmentRefused, match='invalid_send'):
            validate_request({**ok, extra: 'Dave'})


def _payload_name(out, monkeypatch, name):
    from mc.blueprints import agent_routes as ar
    monkeypatch.setattr(ar, '_session_character_parts', lambda project, session: ('', name, []))
    seen = {}
    out.recording.hook = lambda headers, raw: seen.update(json.loads(raw))
    result = prepare(out)
    assert out.outbound.send(result['intent_id'])['state'] == 'accepted'
    return seen['display_name']


def test_sending_session_character_name_reaches_the_signed_payload(out, monkeypatch):
    assert _payload_name(out, monkeypatch, 'Dave') == 'Dave'


def test_unnamed_session_sends_fallback(out, monkeypatch):
    assert _payload_name(out, monkeypatch, '') == 'Clayrune agent'


def test_injection_in_character_name_is_replaced_and_send_still_succeeds(out, monkeypatch):
    assert _payload_name(out, monkeypatch, 'Dave\r\nBcc: evil@example.com') == 'Clayrune agent'
