"""A character pinned to an engine that cannot run a tool-free transform (Codex)
must still be able to name itself, pick a face and write a voice -- on a
certified engine, DISCLOSED in the response. Nothing certified -> a plain 502.

The payload on these routes is the user's own persona text, which is why a
fallback is acceptable here; mc/workflows.py and mail_launder keep refusing.
"""
from pathlib import Path

import pytest

# Reuse the character-route fixtures (client, test-double autouse, helpers).
from test_character_routes import (  # noqa: F401
    _mk, _model_calls_use_the_test_double, _read, client)


from mc import agent_runtime as _ar
_REAL_TRANSFORM = _ar.run_text_transform


class _FakeRuntime:
    def __init__(self, name, *, enforced, binary='/bin/x', display_name=''):
        self.name = name
        self.tool_free_transform_enforced = enforced
        self.display_name = display_name or name.title()
        self._binary = binary

    def resolve_binary(self):
        return Path(self._binary) if self._binary else None

    def model_supported(self, model):
        return True

    def model_choices(self):
        return []


@pytest.fixture()
def engines(monkeypatch):
    """codex (uncertified) + claude/gemini/qwen (certified), recording which
    provider the transform seam was asked to run."""
    from mc import agent_runtime as ar, state
    from mc.blueprints import character_routes as cr

    reg = {
        'codex': _FakeRuntime('codex', enforced=False, display_name='Codex CLI'),
        'claude': _FakeRuntime('claude', enforced=True),
        'gemini': _FakeRuntime('gemini', enforced=True),
        'qwen': _FakeRuntime('qwen', enforced=True),
    }
    for name, rt in reg.items():
        monkeypatch.setitem(ar._RUNTIMES, name, rt)
    monkeypatch.setitem(state.CONFIG, 'default_provider', 'codex')
    monkeypatch.setitem(state.CONFIG, 'agent_model', '')

    calls = []

    def transform(provider, *, prompt, model='', effort='', stdin_text=None, **kw):
        calls.append({'provider': provider, 'model': model, 'effort': effort})
        return cr._scribe_call(model, prompt, stdin_text or '')

    monkeypatch.setattr(cr._agent_runtime, 'run_text_transform', transform)
    cr._scribe_call = lambda *a, **k: 'Quill'
    return type('E', (), {'reg': reg, 'calls': calls})


def test_codex_pinned_name_is_picked_by_claude_and_disclosed(client, engines):
    _mk(client, name='wren', provider='codex', model='gpt-5-codex', effort='high')
    r = client.post('/api/characters/project/wren/name', json={'project_id': 'tchar'})
    assert r.status_code == 200
    body = r.get_json()
    assert body['agent_name'] == 'Quill'
    assert body['picked_by'] == 'claude'
    assert body['fallback_reason'] == "Codex CLI can't run a tool-free call yet"
    # Ran on claude, and did not carry codex's pinned model/effort across vendors.
    assert engines.calls == [{'provider': 'claude', 'model': '', 'effort': ''}]
    # The character's own pin is untouched on disk.
    assert _read(client, 'wren')['engine']['provider'] == 'codex'


def test_codex_pinned_face_and_voice_are_disclosed_too(client, engines, monkeypatch):
    from mc import characters as chars
    from mc.blueprints import character_routes as cr
    monkeypatch.setattr(chars, 'list_figures', lambda: ['lamplighter', 'courier'])
    _mk(client, name='wren', provider='codex')
    cr._scribe_call = lambda *a, **k: 'lamplighter'
    r = client.post('/api/characters/project/wren/avatar', json={'project_id': 'tchar'})
    assert r.status_code == 200
    assert r.get_json()['picked_by'] == 'claude'
    assert r.get_json()['avatar'] == 'fig:lamplighter'
    cr._scribe_call = lambda *a, **k: 'A dry, exact voice.'
    r = client.post('/api/characters/voice', json={
        'description': 'builds things', 'body': 'You build.',
        'engine': {'provider': 'codex'}})
    assert r.status_code == 200
    assert r.get_json()['picked_by'] == 'claude'
    assert r.get_json()['voice'].startswith('## Voice')


def test_certified_default_engine_is_preferred_over_the_order(client, engines, monkeypatch):
    from mc import state
    monkeypatch.setitem(state.CONFIG, 'default_provider', 'gemini')
    _mk(client, name='wren', provider='codex')
    body = client.post('/api/characters/project/wren/name',
                       json={'project_id': 'tchar'}).get_json()
    assert body['picked_by'] == 'gemini'


def test_fallback_skips_a_provider_that_is_not_installed(client, engines):
    engines.reg['claude']._binary = ''  # not installed
    _mk(client, name='wren', provider='codex')
    body = client.post('/api/characters/project/wren/name',
                       json={'project_id': 'tchar'}).get_json()
    assert body['picked_by'] == 'gemini'


def test_certified_pinned_engine_is_not_disclosed_as_a_fallback(client, engines):
    _mk(client, name='wren', provider='claude')
    body = client.post('/api/characters/project/wren/name',
                       json={'project_id': 'tchar'}).get_json()
    assert body['agent_name'] == 'Quill'
    assert 'picked_by' not in body and 'fallback_reason' not in body
    assert engines.calls[0]['provider'] == 'claude'


def test_nothing_certified_is_a_plain_502(client, engines):
    for n in ('claude', 'gemini', 'qwen'):
        engines.reg[n].tool_free_transform_enforced = False
    _mk(client, name='wren', provider='codex')
    r = client.post('/api/characters/project/wren/name', json={'project_id': 'tchar'})
    assert r.status_code == 502
    assert r.get_json()['error'] == ('No engine on this machine can pick a name; '
                                     'type one instead')
    assert engines.calls == []
    assert 'agent_name' not in _read(client, 'wren')


def test_nothing_installed_voice_is_a_plain_502(client, engines):
    for n in ('claude', 'gemini', 'qwen'):
        engines.reg[n]._binary = ''
    r = client.post('/api/characters/voice', json={
        'description': 'builds things', 'body': 'You build.',
        'engine': {'provider': 'codex'}})
    assert r.status_code == 502
    assert 'No engine on this machine can write a voice' in r.get_json()['error']


def test_untrusted_input_seams_still_refuse_codex():
    """The fallback is local to character_routes; the shared seam is unchanged
    (the autouse double patches run_text_transform, so use the saved original)."""
    from mc import agent_runtime
    with pytest.raises(agent_runtime.TransformFailure):
        _REAL_TRANSFORM('codex', prompt='untrusted transcript')
