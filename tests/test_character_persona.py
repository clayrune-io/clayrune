"""Unit tests for Prompt Builder Phase 2 persona wiring in
mc/blueprints/agent_routes.py: _resolve_character (new-chat pick →
meta+body) and _build_agent_context character injection at spawn.

Determinism: mc.characters.GLOBAL_AGENTS_DIR is repointed at tmp_path; no
real ~/.claude is touched. Importing server wires the blueprint's
global-scope deps (SHARED_RULES_PATH, PORT, memory helpers) so
_build_agent_context runs.
"""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import server  # noqa: F401 — wires the blueprint deps
    from mc import characters as ch
    from mc.blueprints import agent_routes as ar

    monkeypatch.setattr(ch, 'GLOBAL_AGENTS_DIR', tmp_path / 'agents-global')

    proj_path = tmp_path / 'proj'
    (proj_path / '.claude' / 'agents').mkdir(parents=True)
    # A project-scope character and a global one.
    ch.write_character('project', 'code-reviewer',
                       'Use for strict review.', 'You are a terse reviewer.',
                       project_path=str(proj_path))
    ch.write_character('global', 'docs-writer',
                       'Use for docs.', 'You write clear docs.')
    return {'ar': ar, 'proj_path': str(proj_path), 'tmp': tmp_path}


class TestResolveCharacter:
    def test_project_scope_resolves_meta_and_body(self, env):
        meta, body = env['ar']._resolve_character(env['proj_path'], 'project:code-reviewer')
        # `source` tells the header pill WHY this persona is running — a picked
        # one and an inherited one must not look identical (agent types §4).
        assert meta == {'name': 'code-reviewer', 'scope': 'project',
                        'display_name': 'code-reviewer', 'source': 'picked'}
        assert body.strip() == 'You are a terse reviewer.'

    def test_global_scope(self, env):
        meta, body = env['ar']._resolve_character(env['proj_path'], 'global:docs-writer')
        assert meta['scope'] == 'global' and meta['name'] == 'docs-writer'
        assert 'clear docs' in body

    @pytest.mark.parametrize('val', ['', None, 'bogus', 'archive:x', 'global:', ':name', 'project:does-not-exist'])
    def test_invalid_or_missing_yields_none(self, env, val):
        meta, body = env['ar']._resolve_character(env['proj_path'], val)
        assert meta is None and body == ''

    def test_project_name_not_found_in_global_scope(self, env):
        # code-reviewer exists only in project scope; asking global misses.
        meta, body = env['ar']._resolve_character(env['proj_path'], 'global:code-reviewer')
        assert meta is None and body == ''


class TestContextInjection:
    def _project(self, env):
        return {'id': 'tc', 'name': 'TC', 'project_path': env['proj_path'],
                'provider': 'claude'}

    def test_character_block_injected_after_rules(self, env):
        ctx = env['ar']._build_agent_context(
            self._project(env), character_body='You are a terse reviewer.')
        assert '--- CHARACTER (active persona for this chat) ---' in ctx
        assert 'You are a terse reviewer.' in ctx

    def test_no_block_without_character(self, env):
        ctx = env['ar']._build_agent_context(self._project(env))
        assert 'CHARACTER (active persona' not in ctx


# ── Agent types Phase 1 — project default + pinned engine ────────────────────
#
# The complaint this backs (MC-895): personas were per-chat and opt-in, so the
# user had to remember to switch one on every single time. A project default
# that new chats inherit was named and deferred by the Phase-2 prompt-builder
# design; this is that layer.


class TestProjectDefaultCharacter:
    def _project(self, env, **extra):
        return {'id': 'tc', 'name': 'TC', 'project_path': env['proj_path'],
                'provider': 'claude', **extra}

    def test_project_default_applies_when_nothing_is_picked(self, env):
        p = self._project(env, default_character='project:code-reviewer')
        meta, body = env['ar']._resolve_character(env['proj_path'], '', project=p)
        assert meta['name'] == 'code-reviewer'
        assert meta['source'] == 'project'      # inherited, not picked
        assert 'terse reviewer' in body

    def test_an_explicit_pick_beats_the_project_default(self, env):
        p = self._project(env, default_character='project:code-reviewer')
        meta, _ = env['ar']._resolve_character(
            env['proj_path'], 'global:docs-writer', project=p)
        assert meta['name'] == 'docs-writer' and meta['source'] == 'picked'

    def test_no_default_and_no_pick_is_todays_behaviour(self, env):
        meta, body = env['ar']._resolve_character(
            env['proj_path'], '', project=self._project(env))
        assert meta is None and body == ''

    def test_a_stale_project_default_never_blocks_dispatch(self, env):
        """Nobody typed it, so nobody is watching for it to break — a deleted
        default must degrade to no-persona, not to an exception."""
        p = self._project(env, default_character='project:deleted-one')
        meta, body = env['ar']._resolve_character(env['proj_path'], '', project=p)
        assert meta is None and body == ''


class TestCharacterEngine:
    def _write(self, env, name, **engine):
        from mc import characters as ch
        ch.write_character('project', name, 'desc', 'body text',
                           project_path=env['proj_path'], overwrite=True,
                           engine=engine)

    def test_engine_round_trips_through_the_file(self, env):
        from mc import characters as ch
        self._write(env, 'prd-writer', provider='claude',
                    model='claude-fable-5', effort='high')
        rec = ch.read_character('project', 'prd-writer',
                                project_path=env['proj_path'])
        assert rec['engine'] == {'provider': 'claude',
                                 'model': 'claude-fable-5', 'effort': 'high'}

    def test_engine_reaches_the_resolved_meta(self, env):
        self._write(env, 'prd-writer', model='claude-fable-5')
        meta, _ = env['ar']._resolve_character(env['proj_path'],
                                               'project:prd-writer')
        assert env['ar']._character_engine(meta, 'model') == 'claude-fable-5'
        assert env['ar']._character_engine(meta, 'provider') == ''

    def test_blank_values_are_not_written_as_pins(self, env):
        """`model: ""` in a hand-edited file must not pin the engine to nothing
        and shadow the project default — absent and empty mean the same."""
        from mc import characters as ch
        self._write(env, 'plain', provider='', model='', effort='')
        rec = ch.read_character('project', 'plain',
                                project_path=env['proj_path'])
        assert 'engine' not in rec
        meta, _ = env['ar']._resolve_character(env['proj_path'], 'project:plain')
        assert env['ar']._character_engine(meta, 'model') == ''

    def test_a_character_with_no_engine_is_unchanged(self, env):
        meta, _ = env['ar']._resolve_character(env['proj_path'],
                                               'project:code-reviewer')
        assert 'engine' not in meta

    def test_bad_effort_is_refused_at_save(self, env):
        import pytest as _pytest
        from mc import characters as ch
        with _pytest.raises(ValueError, match='effort must be one of'):
            ch.write_character('project', 'oops', 'desc', 'body',
                               project_path=env['proj_path'], overwrite=True,
                               engine={'effort': 'ludicrous'})


class TestFlagEffortOverride:
    def test_character_effort_beats_project_and_global(self, env):
        ar = env['ar']
        flags = ar._build_claude_flags({'id': 'tc', 'agent_effort': 'low'},
                                       effort_override='max')
        joined = ' '.join(flags)
        assert 'max' in joined and 'low' not in joined

    def test_no_override_keeps_the_project_effort(self, env):
        ar = env['ar']
        flags = ar._build_claude_flags({'id': 'tc', 'agent_effort': 'low'})
        assert 'low' in ' '.join(flags)


class TestProjectDefaultValidation:
    """Shape only. Whether the character still exists is NOT checked at write
    time — it can be deleted afterwards, so the check would prove nothing when
    it matters. Dispatch resolves best-effort and logs the miss."""

    @pytest.fixture()
    def api(self, tmp_path, monkeypatch):
        import server
        from mc.blueprints import local_auth as la
        from mc.blueprints import project_routes as pr
        monkeypatch.setattr(la, 'LOCAL_AUTH_PATH', tmp_path / 'local_auth.json')
        d = tmp_path / 'projects'
        d.mkdir()
        monkeypatch.setattr(pr, 'DATA_DIR', d)
        monkeypatch.setattr(pr, '_DATA_ROOT', tmp_path)
        monkeypatch.setattr(pr, 'PROJECTS_BASE', tmp_path)
        import json as _json
        (d / 'dc.json').write_text(_json.dumps(
            {'id': 'dc', 'name': 'DC', 'backlog': [],
             'project_path': str(tmp_path)}), encoding='utf-8')
        server.app.config['TESTING'] = True
        c = server.app.test_client()
        c._dir = d
        return c

    def _rec(self, api):
        import json as _json
        return _json.loads((api._dir / 'dc.json').read_text(encoding='utf-8'))

    def test_accepts_and_normalises(self, api):
        r = api.post('/api/project/dc', json={'default_character': ' Project: Reviewer '})
        assert r.status_code == 200
        assert self._rec(api)['default_character'] == 'project:Reviewer'

    def test_rejects_a_bad_shape(self, api):
        r = api.post('/api/project/dc', json={'default_character': 'reviewer'})
        assert r.status_code == 400
        r = api.post('/api/project/dc', json={'default_character': 'archive:x'})
        assert r.status_code == 400

    def test_blank_clears_it(self, api):
        api.post('/api/project/dc', json={'default_character': 'global:x'})
        assert api.post('/api/project/dc',
                        json={'default_character': ''}).status_code == 200
        assert 'default_character' not in self._rec(api)


class TestAgentNameInjection:
    """The type has to KNOW its name, not just be labelled with it. Told who it
    is only after several hundred words of role description, an agent tends to
    introduce itself as the role ("I'm your code reviewer") — so the name goes
    at the top of the character block."""

    def _project(self, env):
        return {'id': 'tc', 'name': 'TC', 'project_path': env['proj_path'],
                'provider': 'claude'}

    def test_name_is_stated_before_the_character_body(self, env):
        ctx = env['ar']._build_agent_context(
            self._project(env), character_body='You review code.',
            character_name='Quill')
        assert 'Your name is Quill.' in ctx
        assert ctx.index('Your name is Quill.') < ctx.index('You review code.')

    def test_a_named_persona_overrides_the_global_assistant_name(self, env, monkeypatch):
        """The collision this whole parameter had to dodge: a local
        `agent_name = CONFIG['agent_name']` used to clobber it, so every
        persona was still told it was the global assistant. Emitting both names
        would be worse — the agent would pick one per turn."""
        from mc import state
        monkeypatch.setitem(state.CONFIG, 'agent_name', 'Vector')
        ctx = env['ar']._build_agent_context(
            self._project(env), character_body='You review code.',
            character_name='Quill')
        assert 'Your name is Quill.' in ctx
        assert 'Your name is Vector.' not in ctx

    def test_unnamed_persona_keeps_the_global_name(self, env, monkeypatch):
        from mc import state
        monkeypatch.setitem(state.CONFIG, 'agent_name', 'Vector')
        ctx = env['ar']._build_agent_context(
            self._project(env), character_body='You review code.')
        assert 'Your name is Vector.' in ctx

    def test_no_names_anywhere_means_no_line(self, env, monkeypatch):
        from mc import state
        monkeypatch.setitem(state.CONFIG, 'agent_name', '')
        ctx = env['ar']._build_agent_context(
            self._project(env), character_body='You review code.')
        assert 'Your name is' not in ctx

    def test_resolved_meta_carries_the_chosen_name(self, env):
        from mc import characters as ch
        ch.write_character('project', 'namer', 'desc', 'body',
                           project_path=env['proj_path'], overwrite=True,
                           agent_name='Vector')
        meta, _ = env['ar']._resolve_character(env['proj_path'], 'project:namer')
        assert meta['agent_name'] == 'Vector'
        assert meta['name'] == 'namer'      # the identifier is untouched


class TestResumeKeepsItsPersona:
    """A resume continues the conversation it is resuming — including WHO was
    in it. The picker is hidden on a resume, so `character` arrives empty and
    the project default used to take over: Ron resumed a chat he had started
    with Dave and it came back as Vector, same conversation, different agent.
    """

    def _log(self, monkeypatch, env, entries):
        monkeypatch.setattr(env['ar'], '_load_agent_log', lambda pid: entries)

    def test_the_spawn_persona_is_read_back_off_the_log(self, env, monkeypatch):
        self._log(monkeypatch, env, [
            {'claude_session_id': 'abc', 'character':
                {'name': 'docs-writer', 'scope': 'global', 'source': 'picked'}},
        ])
        assert env['ar']._prior_character('p', 'abc') == 'global:docs-writer'

    def test_the_newest_entry_without_one_does_not_hide_an_older_one(self, env, monkeypatch):
        # One conversation gets an agent_log row per RUN. A row that never
        # recorded a persona must not read as "this chat had none".
        self._log(monkeypatch, env, [
            {'claude_session_id': 'abc', 'character': None},
            {'claude_session_id': 'abc', 'character':
                {'name': 'docs-writer', 'scope': 'global'}},
        ])
        assert env['ar']._prior_character('p', 'abc') == 'global:docs-writer'

    def test_a_chat_that_ran_with_no_persona_reports_that_it_had_none(self, env, monkeypatch):
        """`''`, not None — the mirror of the same bug. A plain chat must not
        acquire a persona just because the project default changed since."""
        self._log(monkeypatch, env, [{'claude_session_id': 'abc', 'character': None}])
        assert env['ar']._prior_character('p', 'abc') == ''

    def test_an_unknown_conversation_leaves_precedence_alone(self, env, monkeypatch):
        self._log(monkeypatch, env, [{'claude_session_id': 'other'}])
        assert env['ar']._prior_character('p', 'abc') is None

    def test_a_fresh_dispatch_never_consults_the_log(self, env, monkeypatch):
        assert env['ar']._prior_character('p', '') is None

    def test_an_unreadable_log_is_not_fatal(self, env, monkeypatch):
        def boom(pid):
            raise OSError('log is gone')
        monkeypatch.setattr(env['ar'], '_load_agent_log', boom)
        assert env['ar']._prior_character('p', 'abc') is None


# ── MC-924 — provider/model precedence must agree ─────────────────────────
#
# A character's PROVIDER pin used to lose to the composer (which always sent
# a resolved, never-empty value) while its MODEL pin won (only applied when
# the picker was untouched) — so a gemini persona could dispatch carrying
# provider=claude + model=gemini-2.5-flash and die at CLI launch.


class TestModelProviderMismatch:
    """Pure unit tests for `_model_provider_mismatch` — no dispatch, no I/O."""

    def test_matching_model_is_not_flagged(self, env):
        assert env['ar']._model_provider_mismatch('gemini', 'gemini-2.5-flash') == ''

    def test_model_from_another_known_catalog_is_flagged(self, env):
        # The exact MC-924 repro: a gemini model id handed to the claude runtime.
        assert env['ar']._model_provider_mismatch('claude', 'gemini-2.5-flash') == 'gemini'

    def test_unrecognized_model_is_not_flagged(self, env):
        # Newer than our catalog, or a provider-specific alias — the composer's
        # "Custom..." box exists for exactly this case; don't block it.
        assert env['ar']._model_provider_mismatch('claude', 'totally-novel-id-2099') == ''

    def test_unknown_provider_is_not_flagged(self, env):
        # Dispatch's own provider-resolution error handling catches this instead.
        assert env['ar']._model_provider_mismatch('not-a-real-provider', 'gemini-2.5-flash') == ''


class TestDispatchProviderModelPrecedence:
    def _write_pinned(self, env, name, **engine):
        from mc import characters as ch
        ch.write_character('project', name, 'desc', 'body',
                           project_path=env['proj_path'], overwrite=True,
                           engine=engine)

    def _project(self, env):
        return {'id': 'tc', 'name': 'TC', 'project_path': env['proj_path'],
                'provider': 'claude'}

    def test_character_provider_and_model_pin_wins_when_nothing_picked(self, env, monkeypatch):
        ar = env['ar']
        self._write_pinned(env, 'halloway', provider='gemini', model='gemini-2.5-flash')
        monkeypatch.setattr(ar, 'load_project', lambda pid: self._project(env))
        captured = {}

        def _fake_runtime_dispatch(p, task, *, provider_name, model_override, **kw):
            captured['provider_name'] = provider_name
            captured['model_override'] = model_override
            return 'sid123'
        monkeypatch.setattr(ar, '_dispatch_via_runtime', _fake_runtime_dispatch)

        sid = ar._dispatch_agent_internal('tc', 'do a thing', character='project:halloway')
        assert sid == 'sid123'
        assert captured['provider_name'] == 'gemini'
        assert captured['model_override'] == 'gemini-2.5-flash'

    def test_explicit_composer_provider_pick_beats_character_pin(self, env, monkeypatch):
        ar = env['ar']
        # No model pin here — an explicit provider override paired with the
        # character's still-pinned gemini model would itself be an incoherent
        # combination (a separate, correctly-refused case below).
        self._write_pinned(env, 'halloway2', provider='gemini')
        monkeypatch.setattr(ar, 'load_project', lambda pid: self._project(env))
        captured = {}

        def _fake_runtime_dispatch(p, task, *, provider_name, model_override, **kw):
            captured['provider_name'] = provider_name
            captured['model_override'] = model_override
            return 'sid456'
        monkeypatch.setattr(ar, '_dispatch_via_runtime', _fake_runtime_dispatch)

        sid = ar._dispatch_agent_internal('tc', 'do a thing', character='project:halloway2',
                                          provider_override='codex')
        assert sid == 'sid456'
        assert captured['provider_name'] == 'codex'

    def test_incoherent_provider_model_combination_is_refused(self, env, monkeypatch):
        ar = env['ar']
        monkeypatch.setattr(ar, 'load_project', lambda pid: self._project(env))
        # Reproduces the pre-fix bug directly: an explicit claude provider pick
        # paired with a gemini-only model id (what the composer used to send
        # unconditionally while the model came from elsewhere).
        with pytest.raises(ValueError, match='gemini'):
            ar._dispatch_agent_internal('tc', 'do a thing',
                                        provider_override='claude',
                                        model_override='gemini-2.5-flash')
