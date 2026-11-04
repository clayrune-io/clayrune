"""BUILTINS-4 HR-1: roster assignment and persisted schema (mc/hivemind_roster.py).

Spec: docs/BUILTINS_4_HIVEMIND_ROSTER_SPEC.md section 5 HR-1, decisions Q1/Q2/Q4.
Characters are real files in a tmp agents dir (no live roster is read); engines
resolve through the real shared resolver against fake registered runtimes.
"""
import pytest

from tests.test_hivemind_routes import client  # noqa: F401  (fixture)

from mc import agent_runtime, characters, engine_selection
from mc import hivemind_roster as roster

PROVIDERS = ['claude', 'gemini', 'qwen', 'codex', 'opencode', 'goose', 'aider', 'kiro']


@pytest.fixture()
def agents_dir(tmp_path, monkeypatch):
    d = tmp_path / 'agents'
    d.mkdir()
    monkeypatch.setattr(characters, 'GLOBAL_AGENTS_DIR', d)
    return d


def _char(name, **engine):
    characters.write_character(
        'global', name, f'{name} test character', f'You are {name}.',
        engine=engine or None)


APP_CFG = {'default_provider': 'claude'}


# ── reference parsing ───────────────────────────────────────────────────────

class TestParse:
    @pytest.mark.parametrize('bad', [
        None, '', '   ', 5, ['global:x'], 'dave', 'global:', ':dave',
        'user:dave', 'global:../x', 'global:a/b', 'global:a\\b', 'global:.x',
        'global:' + 'a' * 65, 'global:has space'])
    def test_malformed_refused(self, bad):
        with pytest.raises(roster.RosterError) as e:
            roster.parse_character_ref(bad)
        assert e.value.status == 400

    def test_project_scope_refused_q1(self):
        with pytest.raises(roster.RosterError, match='only .global'):
            roster.parse_character_ref('project:dave')

    def test_canonical_form(self):
        assert roster.parse_character_ref('  Global : code-reviewer ') == 'global:code-reviewer'

    def test_unknown_character_refused(self, agents_dir):
        warnings = []
        assert roster.validate_character('global:nobody', app_config=APP_CFG, warnings=warnings) == 'global:nobody'
        assert len(warnings) == 1 and 'global:nobody' in warnings[0]
        assert roster.resolve_assignment('global:nobody', APP_CFG)['substituted'] is True
        # The raw lookup remains strict; only assignment/validation use policy.
        with pytest.raises(roster.RosterError, match='not found'):
            roster.lookup_character('global:nobody')

    def test_display_name_not_inferred(self, agents_dir):
        # A personal name is not a type: only the file stem resolves.
        characters.write_character('global', 'code-reviewer', 'd', 'b',
                                   agent_name='Fenn')
        with pytest.raises(roster.RosterError, match='not found'):
            roster.lookup_character('global:Fenn')
        assert roster.lookup_character('global:code-reviewer')[0] == 'global:code-reviewer'


# ── per-attempt resolution across all eight providers (Q2) ──────────────────

class TestResolveAssignment:
    def test_all_eight_providers_registered(self):
        names = [r.name for r in agent_runtime.available_runtimes()]
        assert sorted(names) == sorted(PROVIDERS)

    @pytest.mark.parametrize('prov', PROVIDERS)
    def test_character_pins_provider_native_default(self, agents_dir, prov):
        _char(f'c-{prov}', provider=prov)
        a = roster.resolve_assignment(f'global:c-{prov}', APP_CFG)
        assert a['character_ref'] == f'global:c-{prov}'
        assert a['provider'] == prov
        assert a['model'] == ''           # native default, nothing inherited
        assert a['effort'] == ''
        assert a['session_id'] == ''

    @pytest.mark.parametrize('prov,expect', [
        ('claude', 'opus'), ('gemini', 'gemini-pro-latest'),
        ('codex', 'gpt-6-astra'), ('qwen', ''), ('opencode', ''),
        ('goose', ''), ('aider', '')])
    def test_tier_tracking_resolves_per_runtime(self, agents_dir, prov, expect):
        _char(f't-{prov}', provider=prov, model='tier:best', effort='high')
        a = roster.resolve_assignment(f'global:t-{prov}', APP_CFG)
        assert (a['provider'], a['model'], a['effort']) == (prov, expect, 'high')

    def test_exact_pin_kept(self, agents_dir):
        _char('pinned', provider='codex', model='gpt-6.1-sol')
        a = roster.resolve_assignment('global:pinned', APP_CFG)
        assert a['model'] == 'gpt-6.1-sol'

    def test_kiro_manages_its_own_model(self, agents_dir):
        _char('kiro-ok', provider='kiro')
        assert roster.resolve_assignment('global:kiro-ok', APP_CFG)['model'] == ''
        _char('kiro-pin', provider='kiro', model='claude-sonnet-5')
        with pytest.raises(engine_selection.EngineSelectionError):
            roster.resolve_assignment('global:kiro-pin', APP_CFG)

    def test_character_without_engine_uses_app_default(self, agents_dir):
        _char('plain')
        a = roster.resolve_assignment('global:plain', {'default_provider': 'codex'})
        assert a['provider'] == 'codex' and a['model'] == ''

    @pytest.mark.parametrize('prov', [p for p in PROVIDERS if p not in ('claude', 'aider')])
    def test_non_claude_never_inherits_sonnet(self, agents_dir, prov):
        """A manifest-baked worker_model of 'sonnet' is not an input here, and
        a hivemind-level 'agent_model' default must not leak either."""
        _char(f'nc-{prov}', provider=prov)
        a = roster.resolve_assignment(
            f'global:nc-{prov}', {'default_provider': 'claude', 'agent_model': 'sonnet'},
            {'id': 'p', 'provider': 'claude', 'model': 'sonnet'})
        assert a['provider'] == prov and a['model'] != 'sonnet'

    def test_mismatched_character_model_is_visible_not_substituted(self, agents_dir):
        _char('bad-pair', provider='codex', model='sonnet')
        with pytest.raises(engine_selection.EngineSelectionError, match='belongs to provider'):
            roster.resolve_assignment('global:bad-pair', APP_CFG)

    def test_unregistered_provider_is_visible(self, agents_dir):
        _char('ghost', provider='nosuchvendor')
        with pytest.raises(engine_selection.EngineSelectionError, match='Unknown provider'):
            roster.resolve_assignment('global:ghost', APP_CFG)

    def test_deleted_character_falls_back_with_disclosure(self, agents_dir):
        _char('temp', provider='gemini')
        assert roster.resolve_assignment('global:temp', APP_CFG)['provider'] == 'gemini'
        characters.delete_character('global', 'temp')
        a = roster.resolve_assignment('global:temp', APP_CFG, previously_defined=True)
        assert a['substituted'] and a['requested_character'] == 'global:temp'
        assert a['substitution_reason'] == 'missing_at_attempt'

    def test_changed_character_reaches_the_next_attempt_only(self, agents_dir):
        _char('drift', provider='gemini')
        ws = {'character': 'global:drift', 'assignment_attempt': 0}
        first = roster.resolve_assignment('global:drift', APP_CFG, session_id='hm_a')
        roster.stamp_attempt(ws, first)
        _char_overwrite = lambda: characters.write_character(  # noqa: E731
            'global', 'drift', 'd', 'b', engine={'provider': 'codex'}, overwrite=True)
        _char_overwrite()
        # the pinned attempt is untouched by the roster edit ...
        assert ws['resolved_assignment']['provider'] == 'gemini'
        assert ws['assignment_attempt'] == 1
        # ... and the next attempt picks the edit up.
        second = roster.resolve_assignment('global:drift', APP_CFG, session_id='hm_b')
        roster.stamp_attempt(ws, second)
        assert (ws['assignment_attempt'], ws['resolved_assignment']['provider'],
                ws['resolved_assignment']['session_id']) == (2, 'codex', 'hm_b')

    def test_next_attempt_tolerates_junk(self):
        assert roster.next_attempt({}) == 1
        assert roster.next_attempt({'assignment_attempt': 'x'}) == 1
        assert roster.next_attempt({'assignment_attempt': True}) == 1
        assert roster.next_attempt({'assignment_attempt': 3}) == 4


# ── routes ──────────────────────────────────────────────────────────────────

def _roster_payload(**over):
    p = {
        'goal': 'Ship three things', 'project_id': 'thm',
        'roster_version': 1, 'reviewer_character': 'global:code-reviewer',
        'workstreams': [
            {'id': 'ws_001', 'title': 'A', 'character': 'global:alpha',
             'smokes': ['a.mjs']},
            {'id': 'ws_002', 'title': 'B', 'character': 'global:beta',
             'dependencies': ['ws_001']},
        ],
    }
    p.update(over)
    return p


@pytest.fixture()
def roster_chars(agents_dir):
    _char('code-reviewer', provider='claude')
    _char('alpha', provider='codex')
    _char('beta', provider='gemini')
    return agents_dir


def _ws_files(client):
    return sorted(p.name for p in client.hm_dir.rglob('workstreams/*.json'))


class TestCreateRoundTrip:
    def test_inline_create_persists_and_round_trips(self, client, roster_chars):
        r = client.post('/api/hivemind/create', json=_roster_payload())
        assert r.status_code == 200, r.get_data(as_text=True)
        body = r.get_json()
        hm_id = body['hivemind']['id']
        cfg = body['hivemind']['config']
        assert cfg['roster_version'] == 1
        assert cfg['reviewer_character'] == 'global:code-reviewer'
        by_id = {w['id']: w for w in body['workstreams']}
        assert by_id['ws_001']['character'] == 'global:alpha'
        assert by_id['ws_001']['assignment_attempt'] == 0
        assert by_id['ws_001']['resolved_assignment'] is None
        assert by_id['ws_001']['provider'] == by_id['ws_001']['model'] == ''
        assert by_id['ws_001']['smokes'] == ['a.mjs']
        # no decomposer for an inline roster create
        assert client.hm_dir.exists()
        full = client.get(f'/api/hivemind/{hm_id}').get_json()
        got = {w['id']: w['character'] for w in full['workstreams']}
        assert got == {'ws_001': 'global:alpha', 'ws_002': 'global:beta'}
        assert full['manifest']['config']['roster_version'] == 1

    def test_separate_create_round_trips(self, client, roster_chars):
        hm_id = client.post('/api/hivemind/create', json=_roster_payload()
                            ).get_json()['hivemind']['id']
        r = client.post(f'/api/hivemind/{hm_id}/workstreams/create', json={
            'id': 'ws_003', 'title': 'C', 'character': ' global:alpha '})
        assert r.status_code == 200, r.get_data(as_text=True)
        assert r.get_json()['workstream']['character'] == 'global:alpha'
        listed = client.get(f'/api/hivemind/{hm_id}/workstreams').get_json()
        assert {w['id']: w.get('character') for w in listed}['ws_003'] == 'global:alpha'

    def test_put_round_trips(self, client, roster_chars):
        hm_id = client.post('/api/hivemind/create', json=_roster_payload()
                            ).get_json()['hivemind']['id']
        r = client.put(f'/api/hivemind/{hm_id}/workstreams/ws_001',
                       json={'character': 'global:beta', 'title': 'A2'})
        assert r.status_code == 200, r.get_data(as_text=True)
        assert r.get_json()['workstream']['character'] == 'global:beta'
        again = client.get(f'/api/hivemind/{hm_id}/workstreams').get_json()
        assert {w['id']: w['character'] for w in again}['ws_001'] == 'global:beta'

    def test_put_same_character_is_noop_even_when_active(self, client, roster_chars):
        hm_id = client.post('/api/hivemind/create', json=_roster_payload()
                            ).get_json()['hivemind']['id']
        client.put(f'/api/hivemind/{hm_id}/workstreams/ws_001', json={'status': 'active'})
        r = client.put(f'/api/hivemind/{hm_id}/workstreams/ws_001',
                       json={'character': 'global:alpha'})
        assert r.status_code == 200


class TestCreateAtomicity:
    def test_invalid_second_item_writes_and_launches_nothing(self, client, roster_chars):
        before = list(client.hm_dir.iterdir())
        payload = _roster_payload()
        payload['workstreams'][1]['character'] = 'global:../ghost'
        r = client.post('/api/hivemind/create', json=payload)
        assert r.status_code == 400
        assert 'workstreams[1]' in r.get_json()['error']
        assert list(client.hm_dir.iterdir()) == before          # no manifest, no dirs
        assert client.popen_calls == [] and client.reg_calls == []
        assert client.fake_rt.dispatch_calls == [] and client.claude_rt.dispatch_calls == []
        from mc import state
        assert not any(k.startswith('hm_') for k in state.agent_sessions)

    @pytest.mark.parametrize('mut,frag', [
        (lambda p: p['workstreams'][1].pop('character'), "'character' is required"),
        (lambda p: p['workstreams'][1].update(character='dave'), 'not a valid'),
        (lambda p: p['workstreams'][1].update(character='project:x'), "only 'global"),
        (lambda p: p['workstreams'][0].update(provider='codex'), "'provider' cannot be set"),
        (lambda p: p['workstreams'][0].update(model='sonnet'), "'model' cannot be set"),
        (lambda p: p['workstreams'][0].update(effort='high'), "'effort' cannot be set"),
        (lambda p: p['workstreams'][1].update(id='ws_001'), 'duplicate'),
        (lambda p: p['workstreams'][1].update(id='../evil'), 'invalid workstream id'),
        (lambda p: p['workstreams'][1].update(title=' '), 'title required'),
        (lambda p: p['workstreams'].append('junk'), 'must be an object'),
        (lambda p: p.update(workstreams=[]), 'needs inline workstreams'),
        (lambda p: p.pop('workstreams'), 'needs inline workstreams'),
        (lambda p: p.pop('reviewer_character'), "'reviewer_character' is required"),
        (lambda p: p.update(reviewer_character='global:ghost'), 'not found'),
        (lambda p: p.update(roster_version=2), 'unsupported'),
        (lambda p: p.update(roster_version='1'), 'unsupported'),
        (lambda p: p.update(roster_version=True), 'unsupported'),
    ])
    def test_refusals(self, client, roster_chars, mut, frag):
        payload = _roster_payload()
        mut(payload)
        r = client.post('/api/hivemind/create', json=payload)
        if payload.get('reviewer_character') == 'global:ghost':
            assert r.status_code == 200
            assert 'global:ghost' in r.get_json()['warnings'][0]
            return
        assert r.status_code == 400, r.get_data(as_text=True)
        assert frag in r.get_json()['error']
        assert list(client.hm_dir.iterdir()) == []
        assert client.fake_rt.dispatch_calls == [] and client.popen_calls == []

    def test_unusable_engine_refused_at_create(self, client, roster_chars):
        _char('bad-pair', provider='codex', model='sonnet')
        payload = _roster_payload()
        payload['workstreams'][0]['character'] = 'global:bad-pair'
        r = client.post('/api/hivemind/create', json=payload)
        assert r.status_code == 400 and 'belongs to provider' in r.get_json()['error']
        assert list(client.hm_dir.iterdir()) == []


class TestWorkstreamRoutes:
    def _hm(self, client):
        return client.post('/api/hivemind/create', json=_roster_payload()
                           ).get_json()['hivemind']['id']

    @pytest.mark.parametrize('body,frag,status', [
        ({'id': 'w', 'title': 'x'}, "'character' is required", 400),
        ({'id': 'w', 'title': 'x', 'character': 'global:ghost'}, 'not found', 400),
        ({'id': 'w', 'title': 'x', 'character': 'global:alpha', 'provider': 'codex'},
         "'provider' cannot be set", 400),
        ({'id': 'ws_001', 'title': 'x', 'character': 'global:alpha'}, 'already exists', 409),
        ({'id': '../x', 'title': 'x', 'character': 'global:alpha'}, 'invalid workstream id', 400),
    ])
    def test_create_refusals_write_nothing(self, client, roster_chars, body, frag, status):
        hm_id = self._hm(client)
        before = _ws_files(client)
        r = client.post(f'/api/hivemind/{hm_id}/workstreams/create', json=body)
        if body.get('character') == 'global:ghost':
            assert r.status_code == 200
            assert 'global:ghost' in r.get_json()['warnings'][0]
            assert r.get_json()['workstream']['character_known'] is False
            return
        assert r.status_code == status, r.get_data(as_text=True)
        assert frag in r.get_json()['error']
        assert _ws_files(client) == before

    @pytest.mark.parametrize('body,frag', [
        ({'character': 'global:ghost'}, 'not found'),
        ({'character': ''}, 'cannot be cleared'),
        ({'character': None}, 'cannot be cleared'),
        ({'provider': 'codex'}, "'provider' cannot be set"),
        ({'model': 'x'}, "'model' cannot be set"),
        ({'effort': 'high'}, "'effort' cannot be set"),
    ])
    def test_put_refusals_leave_record_untouched(self, client, roster_chars, body, frag):
        hm_id = self._hm(client)
        r = client.put(f'/api/hivemind/{hm_id}/workstreams/ws_001',
                       json={'title': 'SHOULD NOT LAND', **body})
        if body.get('character') == 'global:ghost':
            assert r.status_code == 200
            assert 'global:ghost' in r.get_json()['warnings'][0]
            assert r.get_json()['workstream']['character'] == 'global:ghost'
            return
        assert r.status_code == 400
        assert frag in r.get_json()['error']
        ws = client.get(f'/api/hivemind/{hm_id}/workstreams').get_json()[0]
        assert ws['title'] == 'A' and ws['character'] == 'global:alpha'

    def test_put_empty_engine_strings_allowed(self, client, roster_chars):
        hm_id = self._hm(client)
        r = client.put(f'/api/hivemind/{hm_id}/workstreams/ws_001',
                       json={'provider': '', 'model': '', 'effort': ''})
        assert r.status_code == 200

    @pytest.mark.parametrize('state_patch', [
        {'status': 'active'},
        {'status': 'paused', 'current_agent_session_id': 'hm_live'},
        {'status': 'pending', 'current_agent_session_id': 'hm_live'},
    ])
    def test_active_assignment_edit_refused(self, client, roster_chars, state_patch):
        hm_id = self._hm(client)
        ws = client.hm._hm_load_workstream(hm_id, 'ws_001')
        ws.update(state_patch)
        client.hm._hm_save_workstream(hm_id, 'ws_001', ws)
        r = client.put(f'/api/hivemind/{hm_id}/workstreams/ws_001',
                       json={'character': 'global:beta'})
        assert r.status_code == 409
        assert client.hm._hm_load_workstream(hm_id, 'ws_001')['character'] == 'global:alpha'

    def test_inactive_attempt_boundary_allows_edit(self, client, roster_chars):
        hm_id = self._hm(client)
        for st in ('completed', 'failed', 'pending'):
            ws = client.hm._hm_load_workstream(hm_id, 'ws_001')
            ws.update(status=st, current_agent_session_id=None)
            client.hm._hm_save_workstream(hm_id, 'ws_001', ws)
            target = 'global:beta' if ws['character'] == 'global:alpha' else 'global:alpha'
            r = client.put(f'/api/hivemind/{hm_id}/workstreams/ws_001',
                           json={'character': target})
            assert r.status_code == 200, (st, r.get_data(as_text=True))


class TestManifestPut:
    def test_reviewer_validated_and_canonicalised(self, client, roster_chars):
        hm_id = client.post('/api/hivemind/create', json=_roster_payload()
                            ).get_json()['hivemind']['id']
        r = client.put(f'/api/hivemind/{hm_id}',
                       json={'config': {'reviewer_character': ' global:alpha '}})
        assert r.status_code == 200
        assert r.get_json()['manifest']['config']['reviewer_character'] == 'global:alpha'
        accepted = client.put(f'/api/hivemind/{hm_id}',
                              json={'config': {'reviewer_character': 'global:ghost'}})
        assert accepted.status_code == 200
        assert 'global:ghost' in accepted.get_json()['warnings'][0]
        for bad in ('dave', '', None, 7):
            r = client.put(f'/api/hivemind/{hm_id}',
                           json={'title': 'NOPE', 'config': {'reviewer_character': bad}})
            assert r.status_code == 400, bad
        m = client.get(f'/api/hivemind/{hm_id}').get_json()['manifest']
        assert m['config']['reviewer_character'] == 'global:ghost' and m['title'] != 'NOPE'

    def test_roster_version_is_immutable(self, client, roster_chars):
        hm_id = client.post('/api/hivemind/create', json=_roster_payload()
                            ).get_json()['hivemind']['id']
        for v in (None, 0, 2, '1', False):
            r = client.put(f'/api/hivemind/{hm_id}', json={'config': {'roster_version': v}})
            assert r.status_code == 400, v
        assert client.put(f'/api/hivemind/{hm_id}',
                          json={'config': {'roster_version': 1}}).status_code == 200
        m = client.get(f'/api/hivemind/{hm_id}').get_json()['manifest']
        assert m['config']['roster_version'] == 1

    def test_other_config_keys_still_merge(self, client, roster_chars):
        hm_id = client.post('/api/hivemind/create', json=_roster_payload()
                            ).get_json()['hivemind']['id']
        r = client.put(f'/api/hivemind/{hm_id}',
                       json={'config': {'max_concurrent_workers': 1}})
        assert r.status_code == 200
        assert r.get_json()['manifest']['config']['max_concurrent_workers'] == 1
        assert r.get_json()['manifest']['config']['roster_version'] == 1


class TestLegacyUnchanged:
    """Engine-only runs keep their exact record shape and behaviour (Q4)."""

    def test_legacy_create_has_no_roster_keys(self, client):
        r = client.post('/api/hivemind/create', json={
            'goal': 'g', 'project_id': 'thm', 'worker_provider': 'fakeprov',
            'workstreams': [{'id': 'ws_001', 'title': 'T', 'provider': 'fakeprov',
                             'model': 'm1', 'effort': 'high'}]})
        assert r.status_code == 200
        body = r.get_json()
        assert 'roster_version' not in body['hivemind']['config']
        assert 'reviewer_character' not in body['hivemind']['config']
        ws = body['workstreams'][0]
        for k in ('character', 'assignment_attempt', 'resolved_assignment'):
            assert k not in ws
        assert (ws['provider'], ws['model'], ws['effort']) == ('fakeprov', 'm1', 'high')

    def test_legacy_put_with_engine_fields_unchanged(self, client):
        hm_id = client.post('/api/hivemind/create', json={
            'goal': 'g', 'project_id': 'thm',
            'workstreams': [{'id': 'ws_001', 'title': 'T'}]}).get_json()['hivemind']['id']
        r = client.put(f'/api/hivemind/{hm_id}/workstreams/ws_001',
                       json={'provider': 'fakeprov', 'model': 'm2', 'effort': 'low'})
        assert r.status_code == 200
        ws = r.get_json()['workstream']
        assert (ws['provider'], ws['model'], ws['effort']) == ('fakeprov', 'm2', 'low')
        assert 'character' not in ws

    def test_legacy_separate_create_unchanged(self, client):
        hm_id = client.post('/api/hivemind/create', json={
            'goal': 'g', 'project_id': 'thm',
            'workstreams': [{'id': 'ws_001', 'title': 'T'}]}).get_json()['hivemind']['id']
        r = client.post(f'/api/hivemind/{hm_id}/workstreams/create',
                        json={'id': 'ws_001', 'title': 'again', 'provider': 'fakeprov'})
        assert r.status_code == 200          # legacy still overwrites by id
        assert 'character' not in r.get_json()['workstream']

    def test_legacy_inline_still_skips_malformed_entries(self, client):
        r = client.post('/api/hivemind/create', json={
            'goal': 'g', 'project_id': 'thm',
            'workstreams': ['junk', {'title': ''}, {'id': 'ok', 'title': 'fine'}]})
        assert r.status_code == 200
        assert [w['id'] for w in r.get_json()['workstreams']] == ['ok']

    def test_character_on_legacy_run_refused_not_ignored(self, client, roster_chars):
        r = client.post('/api/hivemind/create', json={
            'goal': 'g', 'project_id': 'thm',
            'workstreams': [{'id': 'ws_001', 'title': 'T', 'character': 'global:alpha'}]})
        assert r.status_code == 400 and 'roster_version' in r.get_json()['error']
        assert list(client.hm_dir.iterdir()) == []
        r = client.post('/api/hivemind/create', json={
            'goal': 'g', 'project_id': 'thm', 'reviewer_character': 'global:alpha'})
        assert r.status_code == 400
        hm_id = client.post('/api/hivemind/create', json={
            'goal': 'g', 'project_id': 'thm',
            'workstreams': [{'id': 'ws_001', 'title': 'T'}]}).get_json()['hivemind']['id']
        for call in (
                lambda: client.post(f'/api/hivemind/{hm_id}/workstreams/create',
                                    json={'title': 'x', 'character': 'global:alpha'}),
                lambda: client.put(f'/api/hivemind/{hm_id}/workstreams/ws_001',
                                   json={'character': 'global:alpha'}),
                lambda: client.put(f'/api/hivemind/{hm_id}',
                                   json={'config': {'roster_version': 1}}),
                lambda: client.put(f'/api/hivemind/{hm_id}',
                                   json={'config': {'reviewer_character': 'global:alpha'}})):
            assert call().status_code == 400

    def test_legacy_decomposer_still_dispatched_without_inline(self, client, monkeypatch):
        # no inline workstreams, no roster fields: orchestrator path as before
        calls = []
        monkeypatch.setattr(client.hm, '_hm_dispatch_orchestrator',
                            lambda hm_id, kind: calls.append(kind))
        r = client.post('/api/hivemind/create', json={'goal': 'g', 'project_id': 'thm'})
        assert r.status_code == 200 and calls == ['decompose']


class TestSpawnGuard:
    """HR-2 replaces HR-1's temporary refusal with the roster launch adapter."""

    def test_roster_row_uses_roster_adapter(self, client, roster_chars, monkeypatch):
        calls = []
        monkeypatch.setattr(client.hm._hm_worker, 'spawn_roster_worker',
                            lambda *a: calls.append(a) or 'hm_roster')
        hm_id = client.post('/api/hivemind/create', json=_roster_payload()
                            ).get_json()['hivemind']['id']
        r = client.post(f'/api/hivemind/{hm_id}/workstreams/ws_001/spawn')
        assert r.status_code == 200 and r.get_json()['session_id'] == 'hm_roster'
        assert len(calls) == 1
        assert client.fake_rt.dispatch_calls == [] and client.claude_rt.dispatch_calls == []
        assert client.hm._hm_load_workstream(hm_id, 'ws_001')['status'] == 'active'

    def test_auto_spawn_uses_roster_adapter(self, client, roster_chars, monkeypatch):
        calls = []
        monkeypatch.setattr(client.hm._hm_worker, 'spawn_roster_worker',
                            lambda *a: calls.append(a) or 'hm_roster')
        hm_id = client.post('/api/hivemind/create', json=_roster_payload()
                            ).get_json()['hivemind']['id']
        client.hm._hm_auto_spawn_workers(hm_id)
        ws = client.hm._hm_load_workstream(hm_id, 'ws_001')
        assert ws['status'] == 'active' and ws['current_agent_session_id'] == 'hm_roster'
        assert len(calls) >= 1
        assert client.fake_rt.dispatch_calls == [] and client.claude_rt.dispatch_calls == []
