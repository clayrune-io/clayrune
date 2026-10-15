"""MC-964 T5 — Step D counts a standing topic note by its frontmatter.

Acceptance (journal b2d85e51): a topic note with `holds_while: allowance(codex)`
is counted by Step D. Before this, only a bracketed `[YYYY-MM-DD]` in the hit's
snippet counted, so `project_codex_allowance_topped_up.md` (date written as
`**2026-09-17**`, no frontmatter date) could never trigger the re-probe.
"""
from datetime import datetime, timedelta, timezone

import pytest

from mc import allowance_state as al
from mc import memory_state_trigger as mst
from mc import state
from tests.test_revive_notify_carry import ar  # noqa: F401 — reuse its fixture

_NOW = datetime.now(timezone.utc)
_TODAY = _NOW.date().isoformat()
_OLD = (_NOW.date() - timedelta(days=30)).isoformat()


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    al.wire(tmp_path / 'allowance_state.json')
    al._LAST_PROBE.clear()
    monkeypatch.delitem(state.CONFIG, mst.CONFIG_KEY, raising=False)
    yield
    al._STATE = {}
    al._LAST_PROBE.clear()


@pytest.fixture()
def vault(tmp_path, monkeypatch, ar):  # noqa: F811
    mem = tmp_path / 'memory'
    mem.mkdir()
    monkeypatch.setattr(ar, '_get_memory_path', lambda project: mem / 'MEMORY.md')
    return mem


def _probe_spy(monkeypatch, answer=True):
    from mc import agent_runtime
    calls = []
    for rt in agent_runtime._RUNTIMES.values():
        monkeypatch.setattr(rt, 'probe_allowance', lambda: None)
    monkeypatch.setattr(agent_runtime._RUNTIMES['codex'], 'probe_allowance',
                         lambda: calls.append(1) or answer)
    return calls


def _note(vault, name, frontmatter, body='Standing note.\n'):
    path = vault / name
    path.write_text(f'---\n{frontmatter}\n---\n\n{body}', encoding='utf-8')
    return path


_HIT = {'file': 'standing_note.md', 'snippet': 'Ron buys more credits when it runs out.'}


def test_topic_note_with_holds_while_allowance_codex_is_counted(ar, vault, monkeypatch):  # noqa: F811
    """The acceptance line. The snippet never says 'codex' and carries no
    bracketed date -- only the frontmatter makes this note count."""
    _note(vault, 'standing_note.md',
          f'name: standing-note\nholds_while: allowance(codex)\nmetadata:\n'
          f'  modified: {_TODAY}T10:00:00.000Z')
    al.record_exhaustion('codex', limit_kind='usage_limit',
                          resets_at_display='Sep 29, 2026 7:58 AM')
    calls = _probe_spy(monkeypatch, True)

    block = ar._allowance_conflict_block('', [dict(_HIT)], {'id': 'p'})

    assert len(calls) == 1, 'exactly one re-probe'
    assert 'codex' in block and 'standing_note.md' in block
    assert al.is_exhausted('codex') is False


def test_the_same_note_before_this_change_did_not_count(ar, vault, monkeypatch):  # noqa: F811
    """Negative control: no project -> no frontmatter read -> the old,
    bracketed-date-only behaviour (what shipped before T5)."""
    _note(vault, 'standing_note.md',
          f'name: standing-note\nholds_while: allowance(codex)\nmodified: {_TODAY}')
    al.record_exhaustion('codex', limit_kind='usage_limit',
                          resets_at_display='Sep 29, 2026 7:58 AM')
    calls = _probe_spy(monkeypatch)

    assert ar._allowance_conflict_block('', [dict(_HIT)]) == ''
    assert not calls


def test_the_grammar_spelling_allowance_exhausted_counts_too(ar, vault, monkeypatch):  # noqa: F811
    _note(vault, 'standing_note.md',
          f'name: n\nholds_while: allowance_exhausted(codex) >= 1\nupdated: {_TODAY}')
    al.record_exhaustion('codex', limit_kind='usage_limit')
    calls = _probe_spy(monkeypatch)
    assert ar._allowance_conflict_block('', [dict(_HIT)], {'id': 'p'})
    assert len(calls) == 1


def test_declared_note_without_dated_metadata_falls_back_to_file_mtime(ar, vault, monkeypatch):  # noqa: F811
    _note(vault, 'standing_note.md', 'name: n\nholds_while: allowance(codex)')
    al.record_exhaustion('codex', limit_kind='usage_limit')
    calls = _probe_spy(monkeypatch)
    assert ar._allowance_conflict_block('', [dict(_HIT)], {'id': 'p'})
    assert len(calls) == 1


def test_a_note_older_than_the_record_does_not_count(ar, vault, monkeypatch):  # noqa: F811
    _note(vault, 'standing_note.md',
          f'name: n\nholds_while: allowance(codex)\nupdated: {_OLD}')
    al.record_exhaustion('codex', limit_kind='usage_limit')
    calls = _probe_spy(monkeypatch)
    assert ar._allowance_conflict_block('', [dict(_HIT)], {'id': 'p'}) == ''
    assert not calls


def test_dated_metadata_alone_counts_when_the_note_names_the_vendor(ar, vault, monkeypatch):  # noqa: F811
    """'dated metadata on standing topic notes': no holds_while, but the note
    mentions the vendor and its frontmatter date is newer than the record."""
    _note(vault, 'standing_note.md', f'name: n\nmetadata:\n  modified: {_TODAY}T01:00:00Z')
    al.record_exhaustion('codex', limit_kind='usage_limit')
    calls = _probe_spy(monkeypatch)
    hit = {'file': 'standing_note.md', 'snippet': 'codex credits get topped up'}
    assert ar._allowance_conflict_block('', [hit], {'id': 'p'})
    assert len(calls) == 1


def test_a_bare_mention_in_an_undated_undeclared_note_still_does_not_count(ar, vault, monkeypatch):  # noqa: F811
    _note(vault, 'standing_note.md', 'name: n')
    al.record_exhaustion('codex', limit_kind='usage_limit')
    calls = _probe_spy(monkeypatch)
    hit = {'file': 'standing_note.md', 'snippet': 'codex CLI flag notes'}
    assert ar._allowance_conflict_block('', [hit], {'id': 'p'}) == ''
    assert not calls


def test_a_declaration_for_another_vendor_does_not_trigger_this_one(ar, vault, monkeypatch):  # noqa: F811
    _note(vault, 'standing_note.md',
          f'name: n\nholds_while: allowance(gemini)\nupdated: {_TODAY}')
    al.record_exhaustion('codex', limit_kind='usage_limit')
    calls = _probe_spy(monkeypatch)
    assert ar._allowance_conflict_block('', [dict(_HIT)], {'id': 'p'}) == ''
    assert not calls


def test_flag_off_restores_bracketed_date_only(ar, vault, monkeypatch):  # noqa: F811
    state.CONFIG[mst.CONFIG_KEY] = False
    _note(vault, 'standing_note.md',
          f'name: n\nholds_while: allowance(codex)\nupdated: {_TODAY}')
    al.record_exhaustion('codex', limit_kind='usage_limit')
    calls = _probe_spy(monkeypatch)
    assert ar._allowance_conflict_block('', [dict(_HIT)], {'id': 'p'}) == ''
    assert not calls


def test_bracketed_archive_date_path_is_unchanged(ar, vault, monkeypatch):  # noqa: F811
    al.record_exhaustion('codex', limit_kind='usage_limit')
    calls = _probe_spy(monkeypatch)
    hit = {'file': 'MEMORY_ARCHIVE.md', 'snippet': f'- [{_TODAY}] Ron topped up codex'}
    assert ar._allowance_conflict_block('', [hit], {'id': 'p'})
    assert len(calls) == 1


def test_gate_path_counts_a_declared_note_for_a_non_user_dispatch(ar, vault, monkeypatch):  # noqa: F811
    """The gate (`_allowance_refusal(..., project=)`) has no read-floor hits;
    it searches. A declared note it finds must count there too."""
    _note(vault, 'standing_note.md',
          f'name: n\nholds_while: allowance(codex)\nupdated: {_TODAY}')
    al.record_exhaustion('codex', limit_kind='usage_limit',
                          resets_at_display='Sep 29, 2026 7:58 AM')
    calls = _probe_spy(monkeypatch)
    monkeypatch.setattr(ar, '_memory_search', lambda *a, **k: [dict(_HIT)])

    assert ar._allowance_refusal('codex', user_initiated=False,
                                 project={'id': 'p'}) == ''
    assert len(calls) == 1


@pytest.mark.parametrize('name', ['../escape.md', 'sub/dir.md', '..\\x.md',
                                   'MEMORY_ARCHIVE.md', 'MEMORY.md', 'a.md#managed',
                                   '.hidden.md', 'notamd.txt', '', None])
def test_hit_filenames_cannot_walk_out_of_the_memory_dir(vault, name):
    (vault.parent / 'escape.md').write_text(
        '---\nholds_while: allowance(codex)\n---\n', encoding='utf-8')
    assert mst.note_signals({'file': name}, vault) == (set(), None)


def test_multiple_vendors_in_one_declaration():
    text = '---\nholds_while: allowance(Codex, gemini)\n---\nbody'
    assert mst.declared_vendors(text) == {'codex', 'gemini'}


def test_body_text_is_never_read_as_frontmatter():
    text = '---\nname: n\n---\nholds_while: allowance(codex)\nupdated: 2099-01-01\n'
    assert mst.declared_vendors(text) == set()
    assert mst.metadata_date(text) is None
