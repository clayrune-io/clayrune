"""P0 regression: `_revive_non_claude_from_agent_log` (mc/blueprints/
agent_routes.py) passed a BARE persona name where `_resolve_character`
requires a 'scope:name' reference.

hm_d9c76579 f_98b5163b. `_resolve_character` does
`scope, _, name = character.partition(':')`; for the bare string 'dave' that
yields `('dave', '', '')` — an invalid scope — so it silently fell through to
the project's `default_character` instead of the persona the dead
conversation was actually running. A gemini/codex chat started with a real
persona, left to die, then messaged again, came back under the project
default (or a different persona than the one it had).

Each test below fails on the parent commit and passes after the fix.
"""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture()
def ar(tmp_path, monkeypatch):
    import server  # noqa: F401 — wires the blueprint's global-scope deps
    from mc.blueprints import agent_routes as _ar
    # Isolate the module-level session map for this test only.
    snapshot = dict(_ar.agent_sessions)
    _ar.agent_sessions.clear()
    try:
        yield _ar
    finally:
        _ar.agent_sessions.clear()
        _ar.agent_sessions.update(snapshot)


PROJECT = {'id': 'p1', 'project_path': '/tmp/p1', 'provider': 'claude'}


class TestReviveNonClaudeCharacterReference:

    def test_scope_and_name_are_joined_into_a_resolvable_reference(self, ar, monkeypatch):
        """f_98b5163b's exact repro: a log row with character={'name':'dave',
        'scope':'global'} must dispatch with character=='global:dave', not the
        bare name 'dave' ('dave'.partition(':') == ('dave', '', '') is not a
        valid scope, so _resolve_character used to fall through to the
        project default)."""
        entries = [{'session_id': 's1', 'provider': 'gemini', 'claude_session_id': '',
                   'character': {'name': 'dave', 'scope': 'global',
                                 'display_name': 'dave'}, 'ts': '2026-09-01T00:00:00Z'}]
        monkeypatch.setattr(ar, '_load_agent_log', lambda pid: entries)
        captured = {}

        def _fake_dispatch(project_id, message, **kw):
            captured.update(kw)
            return 's1'
        monkeypatch.setattr(ar, '_dispatch_agent_internal', _fake_dispatch)

        out = ar._revive_non_claude_from_agent_log('p1', 's1', 'keep going', PROJECT)
        assert out == 's1'
        assert captured['character'] == 'global:dave', (
            f"expected a resolvable 'scope:name' reference, got {captured.get('character')!r}")

    def test_project_scope_is_also_joined_correctly(self, ar, monkeypatch):
        entries = [{'session_id': 's2', 'provider': 'codex', 'claude_session_id': '',
                   'character': {'name': 'builder', 'scope': 'project'},
                   'ts': '2026-09-01T00:00:00Z'}]
        monkeypatch.setattr(ar, '_load_agent_log', lambda pid: entries)
        captured = {}
        monkeypatch.setattr(ar, '_dispatch_agent_internal',
                            lambda pid, msg, **kw: captured.update(kw) or 's2')
        ar._revive_non_claude_from_agent_log('p1', 's2', 'go', PROJECT)
        assert captured['character'] == 'project:builder'

    def test_no_persona_on_the_dead_conversation_stays_no_persona(self, ar, monkeypatch):
        """A conversation that genuinely ran with no character must not
        acquire one just because it was revived — character='' (empty),
        never a bare name that would coincidentally resolve to something."""
        entries = [{'session_id': 's3', 'provider': 'gemini', 'claude_session_id': '',
                   'character': None, 'ts': '2026-09-01T00:00:00Z'}]
        monkeypatch.setattr(ar, '_load_agent_log', lambda pid: entries)
        captured = {}
        monkeypatch.setattr(ar, '_dispatch_agent_internal',
                            lambda pid, msg, **kw: captured.update(kw) or 's3')
        ar._revive_non_claude_from_agent_log('p1', 's3', 'go', PROJECT)
        assert captured['character'] == ''

    def test_source_and_incognito_are_carried_through(self, ar, monkeypatch):
        entries = [{'session_id': 's4', 'provider': 'gemini', 'claude_session_id': '',
                   'character': {'name': 'dave', 'scope': 'global'},
                   'source': 'agent', 'incognito': False,
                   'ts': '2026-09-01T00:00:00Z'}]
        monkeypatch.setattr(ar, '_load_agent_log', lambda pid: entries)
        captured = {}
        monkeypatch.setattr(ar, '_dispatch_agent_internal',
                            lambda pid, msg, **kw: captured.update(kw) or 's4')
        ar._revive_non_claude_from_agent_log('p1', 's4', 'go', PROJECT)
        assert captured['source'] == 'agent'
        assert captured['incognito'] is False
