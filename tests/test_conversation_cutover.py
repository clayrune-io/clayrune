"""Default-off canonical history, Agent Log, and Scribe read selection."""
from pathlib import Path

import pytest

from mc.conversation_cutover import (
    CutoverPolicy,
    canonical_agent_log,
    read_canonical_history,
    read_history_with_cutover,
    read_scribe_with_cutover,
    select_history,
    select_scribe,
)
from mc.conversation_store import ConversationStore


ENGINE = {'provider': 'fixture', 'model': 'requested', 'effort': 'high'}


def journal(tmp_path: Path, *, complete: bool = True, output: str = 'full result'):
    store = ConversationStore(tmp_path / 'canonical.sqlite')
    state = store.create_lifecycle_conversation('p', 'c', engine=ENGINE, event_id='create')
    state = store.accept_request('p', 'c', request_id='request',
        user_message={'text': 'original input', 'attachments': [{'id': 'a', 'name': 'note.txt'}]},
        engine=ENGINE, provenance={'origin': 'interactive'},
        expected_revision=state.revision, event_id='accept')
    state, owner = store.claim_owner('p', 'c', owner_id='owner',
        expected_revision=state.revision, event_id='owner')
    state, token = store.claim_attempt(owner, request_id='request', attempt_id='attempt',
        expected_revision=state.revision, event_id='claim')
    store.append_evidence(token, event_id='call', kind='tool_call',
        payload={'call_id': 'call', 'name': 'Read', 'input': {'path': 'note.txt'}})
    store.append_evidence(token, event_id='result', kind='tool_result',
        payload={'call_id': 'call', 'output': output, 'is_error': False})
    store.append_evidence(token, event_id='reply', kind='assistant_message',
        payload={'message_id': 'message', 'block_id': 'message/text',
                 'text': 'final answer', 'completeness': 'final'})
    if complete:
        state = store.lifecycle_state('p', 'c')
        store.record_coverage(owner, high_water=state.high_water, complete=True,
            source_reference='fixture-complete', expected_revision=state.revision,
            event_id='coverage')
    return store


def test_display_preserves_large_tool_result_while_scribe_has_explicit_budget(tmp_path):
    from mc.conversation_cutover import ConversationCutover

    output = 'start-' + '🧱中' * 5000 + '-end'
    store = journal(tmp_path, output=output)
    cutover = ConversationCutover(policy=CutoverPolicy(enabled=True),
                                  store_provider=lambda: store)
    lines = cutover.display_lines('p', 'c')
    assert lines is not None
    assert f'RESULT [call call]: {output}' in lines
    scribe = cutover.scribe('p', 'c')
    assert any('chars elided' in line for line in scribe.lines)
    assert read_canonical_history(store, 'p', 'c').complete


def test_default_off_selection_never_promotes_canonical_history(tmp_path):
    store = journal(tmp_path)
    canonical = read_canonical_history(store, 'p', 'c')
    assert canonical is not None and canonical.complete
    legacy = ('LEGACY: richer native transcript',)
    chosen = select_history(canonical=canonical, legacy_reader=lambda: legacy)
    assert chosen.source == 'legacy'
    assert chosen.coverage == 'legacy_only'
    assert chosen.events == legacy
    assert chosen.reason == 'cutover_disabled'


def test_default_off_composition_helpers_do_not_open_canonical_store(tmp_path, monkeypatch):
    store = journal(tmp_path)
    import mc.conversation_cutover as cutover
    monkeypatch.setattr(cutover, 'read_canonical_history',
                        lambda *args: pytest.fail('canonical read must stay disabled'))
    policy = CutoverPolicy()
    history = read_history_with_cutover(store, 'p', 'c',
                                        legacy_reader=lambda: ('legacy',), policy=policy)
    scribe = read_scribe_with_cutover(store, 'p', 'c',
                                      legacy_reader=lambda: ('LEGACY',), policy=policy)
    assert history.source == 'legacy' and history.events == ('legacy',)
    assert scribe.source == 'legacy' and scribe.lines == ('LEGACY',)


def test_enabled_complete_selection_is_canonical_and_does_not_read_legacy(tmp_path):
    store = journal(tmp_path)
    canonical = read_canonical_history(store, 'p', 'c')
    assert canonical is not None
    called = []
    chosen = select_history(canonical=canonical,
        legacy_reader=lambda: called.append(True) or ('legacy',),
        policy=CutoverPolicy(enabled=True))
    assert chosen.source == 'canonical'
    assert chosen.coverage == 'canonical_verified'
    assert [event.kind for event in chosen.events if not event.kind.startswith('lifecycle.')] == [
        'tool_call', 'tool_result', 'assistant_message']
    assert called == []


def test_partial_canonical_history_falls_back_but_remains_visible_without_legacy(tmp_path):
    store = journal(tmp_path, complete=False)
    canonical = read_canonical_history(store, 'p', 'c')
    assert canonical is not None and canonical.coverage == 'partial'
    fallback = select_history(canonical=canonical, legacy_reader=lambda: ('native',),
                              policy=CutoverPolicy(enabled=True))
    assert fallback.source == 'legacy'
    assert fallback.reason == 'canonical_incomplete'
    no_fallback = select_history(canonical=canonical, policy=CutoverPolicy(
        enabled=True, allow_legacy_fallback=False))
    assert no_fallback.source == 'canonical'
    assert no_fallback.coverage == 'partial'
    assert no_fallback.events


def test_canonical_scribe_is_structured_and_only_complete_capture_is_eligible(tmp_path):
    store = journal(tmp_path)
    canonical = read_canonical_history(store, 'p', 'c')
    assert canonical is not None
    chosen = select_scribe(canonical=canonical, policy=CutoverPolicy(enabled=True))
    assert chosen.source == 'canonical'
    assert chosen.lines == (
        'USER: original input', 'ATTACHMENT: note.txt',
        'ACTION Read [call call]: {"path": "note.txt"}',
        'RESULT [call call]: full result', 'ASSISTANT: final answer')

    incomplete = read_canonical_history(journal(tmp_path / 'incomplete', complete=False), 'p', 'c')
    assert incomplete is not None
    fallback = select_scribe(canonical=incomplete, legacy_reader=lambda: ('LEGACY',),
                             policy=CutoverPolicy(enabled=True))
    assert fallback.source == 'legacy'
    blocked = select_scribe(canonical=incomplete, policy=CutoverPolicy(
        enabled=True, allow_legacy_fallback=False))
    assert blocked.source == 'none'
    assert blocked.lines == ()


def test_canonical_agent_log_is_read_only_and_provider_neutral(tmp_path):
    store = journal(tmp_path)
    rows = canonical_agent_log(store, 'p', 'c')
    assert len(rows) == 1
    row = rows[0].as_dict()
    assert row['session_id'] == 'c'
    assert row['attempt_id'] == 'attempt'
    assert row['request_id'] == 'request'
    assert row['origin'] == 'interactive'
    assert row['engine'] == ENGINE
    assert row['canonical'] is True
    assert canonical_agent_log(store, 'missing', 'chat') == ()


def test_history_snapshot_allows_gapped_display_but_revokes_on_delete(tmp_path):
    store = journal(tmp_path, complete=False)
    snapshot = store.history_snapshot('p', 'c')
    rows = []
    cursor = 0
    while cursor < snapshot.high_water:
        page = store.read_history_snapshot(snapshot, after=cursor, limit=2)
        assert page
        rows.extend(page)
        cursor = page[-1].sequence
    assert cursor == snapshot.high_water
    state = store.lifecycle_state('p', 'c')
    state = store.set_lifecycle_deleted('p', 'c', True,
        expected_revision=state.revision, event_id='delete')
    with pytest.raises(Exception, match='deleted|revoked|missing'):
        store.read_history_snapshot(snapshot, after=0, limit=2)
