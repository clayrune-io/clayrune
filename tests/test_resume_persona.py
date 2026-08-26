"""A conversation keeps the agent it was started with (Ron, 2026-08-26).

Ron started a chat with Dave, left it, resumed, and was answered by Vector —
the project's plain agent name. Same conversation, different agent, no event
anywhere to explain it.

`_dispatch_agent_internal` already recovered the persona on a resume
(`_prior_character`, 894200a). What did not was every OTHER way a conversation
comes back:

  * `_revive_from_agent_log` — the finalized/purged session path, which
    rebuilds the whole system prompt from scratch after MC forgot the session.
  * the ten "start fresh" branches in the follow-up / interrupt machinery,
    which called `_build_agent_context(p, task=…)` bare while holding a session
    dict that knew exactly who was talking.

Both now go through `_fresh_context_for`. The `-r` branches beside them were
already fine — they use `_respawn_sysprompt_args`.
"""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc.blueprints import agent_routes  # noqa: E402

DAVE = {'name': 'dave', 'scope': 'global', 'display_name': 'dave',
        'agent_name': 'Dave', 'avatar': 'fig:guard'}
PROJECT = {'id': 'p1', 'project_path': '/tmp/p1'}


@pytest.fixture()
def resolves(monkeypatch):
    """_resolve_character stands in for the persona files on disk."""
    seen = {}

    def _fake(pp, character, project=None):
        seen['asked'] = character
        if character == 'global:dave':
            return dict(DAVE, skills=['audit-doc']), 'YOU ARE DAVE.'
        return None, ''
    monkeypatch.setattr(agent_routes, '_resolve_character', _fake)
    return seen


@pytest.fixture()
def builds(monkeypatch):
    """_build_agent_context records what it was handed."""
    got = {}

    def _fake(project, **kw):
        got.update(kw)
        got['project'] = project
        return 'CTX'
    monkeypatch.setattr(agent_routes, '_build_agent_context', _fake)
    return got


class TestSessionCharacterParts:

    def test_a_persona_session_yields_body_name_and_skills(self, resolves):
        body, name, sk = agent_routes._session_character_parts(
            PROJECT, {'character': DAVE})
        assert body == 'YOU ARE DAVE.' and name == 'Dave' and sk == ['audit-doc']
        assert resolves['asked'] == 'global:dave'

    def test_it_is_re_read_from_disk_not_trusted_from_the_session(self, resolves):
        """An EDITED persona has to take effect on the next rebuild rather than
        never — so the body comes off disk, not out of the session dict."""
        agent_routes._session_character_parts(PROJECT, {'character': DAVE})
        assert resolves['asked'] == 'global:dave'

    @pytest.mark.parametrize('session', [
        {}, None, {'character': None}, {'character': {}}, {'character': 'dave'},
    ])
    def test_a_session_with_no_persona_yields_nothing(self, session, resolves):
        assert agent_routes._session_character_parts(PROJECT, session) == ('', '', [])


class TestFreshContextKeepsThePersona:

    def test_a_fresh_rebuild_carries_the_persona(self, resolves, builds):
        agent_routes._fresh_context_for(PROJECT, {'character': DAVE,
                                                  'session_id': 's1'}, 'go on')
        assert builds['character_body'] == 'YOU ARE DAVE.'
        assert builds['character_name'] == 'Dave'
        assert builds['character_skills'] == ['audit-doc']
        assert builds['session_id'] == 's1'
        assert builds['task'] == 'go on'

    def test_a_session_that_had_no_persona_does_not_acquire_one(self, resolves, builds):
        """The bug mirrored. A chat that ran plain must not pick up the
        project default just because it was respawned."""
        agent_routes._fresh_context_for(PROJECT, {'session_id': 's1'}, 'go on')
        assert builds['character_body'] == '' and builds['character_name'] == ''

    def test_incognito_is_carried(self, resolves, builds):
        """A bare rebuild put MEMORY and rules back into an incognito session —
        the one thing incognito exists to prevent."""
        agent_routes._fresh_context_for(
            PROJECT, {'incognito': True, 'session_id': 's1'}, 'go on')
        assert builds['incognito'] is True

    def test_a_persona_lookup_failure_still_returns_a_context(self, monkeypatch, builds):
        """Coming back as the wrong agent is bad; not coming back is worse."""
        def _boom(*a, **kw):
            raise RuntimeError('disk gone')
        monkeypatch.setattr(agent_routes, '_resolve_character', _boom)
        assert agent_routes._fresh_context_for(PROJECT, {'character': DAVE}, 'x') == 'CTX'
        assert builds['character_body'] == ''

    def test_a_none_session_is_safe(self, resolves, builds):
        assert agent_routes._fresh_context_for(PROJECT, None, 'x') == 'CTX'


class TestPriorCharacter:
    """What `_revive_from_agent_log` leans on to know who a purged
    conversation was."""

    def _log(self, monkeypatch, entries):
        monkeypatch.setattr(agent_routes, '_load_agent_log', lambda pid: entries)

    def test_it_finds_the_persona_by_claude_session_id(self, monkeypatch):
        self._log(monkeypatch, [{'claude_session_id': 'abc', 'character': DAVE}])
        assert agent_routes._prior_character('p1', 'abc') == 'global:dave'

    def test_it_skips_rows_this_bug_already_blanked(self, monkeypatch):
        """The log is newest-first and the broken revives wrote `character:
        null` rows on top of the good one. A persona is resolved once and is
        immutable for the chat's lifetime, so any row that names one names THE
        one — which makes this self-healing rather than sticky."""
        self._log(monkeypatch, [
            {'claude_session_id': 'abc', 'character': None},
            {'claude_session_id': 'abc', 'character': None},
            {'claude_session_id': 'abc', 'character': DAVE},
        ])
        assert agent_routes._prior_character('p1', 'abc') == 'global:dave'

    def test_a_conversation_that_had_none_returns_empty_not_none(self, monkeypatch):
        """'' means "deliberately no persona" and must not fall through to the
        project default; None means "no record", which leaves precedence alone."""
        self._log(monkeypatch, [{'claude_session_id': 'abc', 'character': None}])
        assert agent_routes._prior_character('p1', 'abc') == ''

    def test_an_unknown_conversation_returns_none(self, monkeypatch):
        self._log(monkeypatch, [{'claude_session_id': 'other', 'character': DAVE}])
        assert agent_routes._prior_character('p1', 'abc') is None

    def test_no_resume_id_returns_none(self, monkeypatch):
        self._log(monkeypatch, [{'claude_session_id': 'abc', 'character': DAVE}])
        assert agent_routes._prior_character('p1', '') is None

    def test_a_broken_log_never_blocks_the_resume(self, monkeypatch):
        def _boom(pid):
            raise OSError('log unreadable')
        monkeypatch.setattr(agent_routes, '_load_agent_log', _boom)
        assert agent_routes._prior_character('p1', 'abc') is None


class TestNoBareRebuildsSurvive:
    """The regression that actually matters: someone adds an eleventh
    start-fresh branch and reaches for the bare call again."""

    def test_the_respawn_machinery_has_no_bare_context_rebuilds(self):
        src = (PROJECT_ROOT / 'mc' / 'blueprints' / 'agent_routes.py').read_text(
            encoding='utf-8')
        bare = [ln.strip() for ln in src.split('\n')
                if "_build_agent_context(p, task=message or '')" in ln
                or "_build_agent_context(p, task=task or '')" in ln]
        assert not bare, (
            'a start-fresh respawn rebuilt its context without the session, '
            'which drops the persona — use _fresh_context_for(p, <session>, …): '
            + '; '.join(bare))
