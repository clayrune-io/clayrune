"""MC-1151 P1: agent-channel store, identity, schemas, manual-channel projection.

Spec: docs/desk_v1/AGENT_CHANNEL_SPEC.md section 9 row P1. Each test names
the P1 requirement it pins.
"""
from __future__ import annotations

import copy
import json
import os
import re
import subprocess
import threading
from pathlib import Path

import pytest

from mc import desk_agent_projection as P
from mc import desk_agent_schema as S
from mc import desk_agent_store as store_mod
from mc.desk_agent_public_data import carries_credential, validate_public_url, PublicDataRefused
from mc.desk_agent_store import (
    AgentChannelStore, ArchivedError, ChecklistExists, NotFound, OwnershipError,
    RevisionConflict, StoreCorrupt,
)

ROOT = Path(__file__).resolve().parents[1]
FAKE_TOKEN = 'ghp_' + 'A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6'


@pytest.fixture
def campaigns():
    return {
        'camp-a': {'id': 'camp-a', 'project_id': 'proj1', 'title': 'A',
                   'plan': {'accounts': [{'id': 'acc-1', 'platform': 'x'}]}},
        'camp-b': {'id': 'camp-b', 'project_id': 'proj1', 'title': 'B', 'plan': {}},
        'camp-other': {'id': 'camp-other', 'project_id': 'proj2', 'title': 'O'},
        'camp-orphan': {'id': 'camp-orphan', 'project_id': None},
    }


@pytest.fixture
def logs(monkeypatch):
    lines: list[str] = []
    monkeypatch.setattr(store_mod, '_log',
                        lambda *a, **k: lines.append(' '.join(str(x) for x in a)))
    return lines


@pytest.fixture
def st(tmp_path, campaigns, logs):
    return AgentChannelStore(tmp_path / 'data' / 'desk_agent',
                             campaign_resolver=lambda cid: copy.deepcopy(campaigns.get(cid)),
                             data_dir=tmp_path / 'data' / 'projects')


def _rev(doc):
    return doc['checklist']['revision']


def _artifact():
    return {'kind': 'mcp_server', 'name': 'Example MCP',
            'public_url': 'https://github.com/example/example-mcp',
            'identity': 'io.github.example/example-mcp', 'version': '1.2.0'}


def _prepared(st, cid='camp-a'):
    """Checklist with one artifact, an applicable MCP Registry entry and one
    revision citing one evidence record."""
    doc = st.open_checklist('proj1', cid)
    doc = st.set_artifacts('proj1', cid, _rev(doc), [_artifact()])
    art = doc['checklist']['artifacts'][0]['id']
    doc, entry = st.open_entry('proj1', cid, 'mcp_registry', art)
    doc = st.set_applicability('proj1', cid, _rev(doc), entry['id'], 'applicable')
    doc, ev = st.add_evidence('proj1', cid, _rev(doc), {
        'excerpt': 'Example MCP exposes read-only search tools.',
        'source_url': 'https://github.com/example/example-mcp#readme',
        'source_kind': 'owner_provided', 'field_bindings': ['description']})
    doc, rev = st.add_revision('proj1', cid, _rev(doc), entry['id'],
                               fields={'description': 'Read-only search tools.'},
                               field_evidence={'description': [ev['id']]},
                               rendered_text='Example MCP: read-only search tools.',
                               template_version='mcp_registry-2026-11-04')
    return doc, entry, ev, rev


# -- identity, ownership, traversal ------------------------------------------

@pytest.mark.parametrize('bad', [
    '..', '../proj1', 'proj1/../x', 'a/b', 'a\\b', 'C:', 'C:\\x', '.hidden',
    '', ' ', 'proj\x00', 'x' * 65, '-lead', 'é', None, 7,
])
def test_traversal_and_malformed_ids_refused_before_any_path(st, tmp_path, bad):
    with pytest.raises(S.SchemaError):
        st.open_checklist(bad, 'camp-a')
    with pytest.raises(S.SchemaError):
        st.open_checklist('proj1', bad)
    assert not (tmp_path / 'data' / 'desk_agent').exists()
    assert list(tmp_path.rglob('checklist.json')) == []


def test_wrong_project_reads_like_unknown_campaign(st):
    with pytest.raises(OwnershipError) as wrong:
        st.open_checklist('proj1', 'camp-other')
    with pytest.raises(NotFound) as unknown:
        st.open_checklist('proj1', 'camp-missing')
    assert str(wrong.value) == str(unknown.value)
    with pytest.raises(NotFound):
        st.open_checklist('proj1', 'camp-orphan')   # no owning project at all


def test_ownership_is_resolved_on_reads_and_on_entry_lookup(st, campaigns):
    doc, entry, _ev, rev = _prepared(st)
    # Campaign moves to another project: every read refuses, by id too.
    campaigns['camp-a']['project_id'] = 'proj2'
    for call in (lambda: st.get_checklist('proj1', 'camp-a'),
                 lambda: st.find_entry('proj1', 'camp-a', entry['id']),
                 lambda: st.find_revision('proj1', 'camp-a', rev['id']),
                 lambda: st.entry_history('proj1', 'camp-a', entry['id'])):
        with pytest.raises(OwnershipError):
            call()


def test_entry_and_revision_ids_do_not_cross_campaigns(st):
    _doc, entry, _ev, rev = _prepared(st, 'camp-a')
    st.open_checklist('proj1', 'camp-b')
    with pytest.raises(NotFound):
        st.find_entry('proj1', 'camp-b', entry['id'])
    with pytest.raises(NotFound):
        st.find_revision('proj1', 'camp-b', rev['id'])
    with pytest.raises(S.SchemaError):
        st.find_entry('proj1', 'camp-a', rev['id'])   # wrong id kind


def test_unknown_surface_and_artifact_refused(st):
    st.open_checklist('proj1', 'camp-a')
    with pytest.raises(S.SchemaError):
        st.open_entry('proj1', 'camp-a', 'producthunt')
    with pytest.raises(NotFound):
        st.open_entry('proj1', 'camp-a', 'mcp_registry', 'agar-0000000000000000')


# -- idempotent open ----------------------------------------------------------

def test_duplicate_opens_are_idempotent(st, tmp_path):
    a = st.open_checklist('proj1', 'camp-a')
    b = st.open_checklist('proj1', 'camp-a')
    assert a['checklist']['id'] == b['checklist']['id']
    assert a['checklist']['channel_id'] == b['checklist']['channel_id']
    assert _rev(a) == _rev(b) == 1
    d1, e1 = st.open_entry('proj1', 'camp-a', 'llms_txt')
    d2, e2 = st.open_entry('proj1', 'camp-a', 'llms_txt')
    assert e1['id'] == e2['id'] and _rev(d1) == _rev(d2)
    assert d2['checklist']['entry_ids'] == [e1['id']]
    assert len(list(tmp_path.rglob('checklist.json'))) == 1


def test_concurrent_opens_create_one_checklist_and_one_entry(st):
    ids, entry_ids, errors = set(), set(), []

    def worker():
        try:
            ids.add(st.open_checklist('proj1', 'camp-a')['checklist']['id'])
            entry_ids.add(st.open_entry('proj1', 'camp-a', 'context7')[1]['id'])
        except Exception as e:  # pragma: no cover - surfaced by the assert
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert len(ids) == 1 and len(entry_ids) == 1
    assert st.get_checklist('proj1', 'camp-a')['checklist']['entry_ids'] == list(entry_ids)


def test_channel_id_survives_campaign_title_change(st, campaigns):
    first = st.open_checklist('proj1', 'camp-a')['checklist']['channel_id']
    campaigns['camp-a']['title'] = 'Renamed'
    assert st.open_checklist('proj1', 'camp-a')['checklist']['channel_id'] == first


# -- concurrent writes conflict ----------------------------------------------

def test_stale_revision_conflicts_instead_of_losing_a_write(st):
    doc = st.open_checklist('proj1', 'camp-a')
    seen = _rev(doc)
    st.set_artifacts('proj1', 'camp-a', seen, [_artifact()])
    with pytest.raises(RevisionConflict) as e:
        st.set_artifacts('proj1', 'camp-a', seen, [])
    assert e.value.current == seen + 1
    assert len(st.get_checklist('proj1', 'camp-a')['checklist']['artifacts']) == 1


def test_racing_writers_exactly_one_wins(st):
    doc = st.open_checklist('proj1', 'camp-a')
    seen = _rev(doc)
    barrier = threading.Barrier(8)
    wins, conflicts = [], []

    def worker(n):
        barrier.wait()
        try:
            st.set_artifacts('proj1', 'camp-a', seen,
                             [dict(_artifact(), name=f'Writer {n}')])
            wins.append(n)
        except RevisionConflict:
            conflicts.append(n)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(wins) == 1 and len(conflicts) == 7
    final = st.get_checklist('proj1', 'camp-a')
    assert final['checklist']['artifacts'][0]['name'] == f'Writer {wins[0]}'
    assert _rev(final) == seen + 1


def test_expected_revision_is_required_for_edits(st):
    st.open_checklist('proj1', 'camp-a')
    for bad in (None, '1', True, 1.0):
        with pytest.raises(S.SchemaError):
            st.set_artifacts('proj1', 'camp-a', bad, [])


# -- crash-safe replacement ----------------------------------------------------

def test_crash_during_replace_keeps_previous_record(st, tmp_path, monkeypatch):
    doc = st.open_checklist('proj1', 'camp-a')
    path = tmp_path / 'data' / 'desk_agent' / 'proj1' / 'camp-a' / 'checklist.json'
    before = path.read_bytes()

    import mc.atomic_json as aj

    def boom(tmp, target):
        raise OSError('simulated power loss before rename')
    monkeypatch.setattr(aj, '_replace_with_retry', boom)
    with pytest.raises(store_mod.StoreIOError):
        st.set_artifacts('proj1', 'camp-a', _rev(doc), [_artifact()])
    monkeypatch.undo()
    assert path.read_bytes() == before
    json.loads(before)   # still a whole, parseable record
    assert [p.name for p in path.parent.iterdir()] == ['checklist.json']  # temp cleaned
    assert _rev(st.get_checklist('proj1', 'camp-a')) == _rev(doc)


def test_failed_body_saves_nothing(st, tmp_path):
    doc = st.open_checklist('proj1', 'camp-a')
    path = tmp_path / 'data' / 'desk_agent' / 'proj1' / 'camp-a' / 'checklist.json'
    before = path.read_bytes()
    with pytest.raises(NotFound):
        st.set_applicability('proj1', 'camp-a', _rev(doc), 'agen-0000000000000000',
                             'applicable')
    assert path.read_bytes() == before


# -- corruption is observable -------------------------------------------------

@pytest.mark.parametrize('garbage', [
    b'{"schema_version": 1, "kind": "desk_agent_checklist", "checkl',   # torn
    b'\x00' * 512,                                                       # NUL-filled
    b'[]',
    json.dumps({'schema_version': 99, 'kind': 'desk_agent_checklist'}).encode(),
])
def test_corrupt_record_raises_logs_and_is_never_replaced(st, tmp_path, logs, garbage):
    st.open_checklist('proj1', 'camp-a')
    path = tmp_path / 'data' / 'desk_agent' / 'proj1' / 'camp-a' / 'checklist.json'
    path.write_bytes(garbage)
    for call in (lambda: st.get_checklist('proj1', 'camp-a'),
                 lambda: st.open_checklist('proj1', 'camp-a'),
                 lambda: st.open_entry('proj1', 'camp-a', 'llms_txt'),
                 lambda: st.set_artifacts('proj1', 'camp-a', 1, [])):
        with pytest.raises(StoreCorrupt):
            call()
    assert path.read_bytes() == garbage
    assert any('corrupt record' in line and 'left in place' in line for line in logs)


def test_record_moved_between_campaigns_is_corrupt_not_adopted(st, tmp_path):
    st.open_checklist('proj1', 'camp-a')
    base = tmp_path / 'data' / 'desk_agent' / 'proj1'
    (base / 'camp-b').mkdir()
    (base / 'camp-b' / 'checklist.json').write_bytes(
        (base / 'camp-a' / 'checklist.json').read_bytes())
    with pytest.raises(StoreCorrupt):
        st.get_checklist('proj1', 'camp-b')


def test_tampered_revision_text_is_corrupt(st, tmp_path):
    _doc, _e, _ev, rev = _prepared(st)
    path = tmp_path / 'data' / 'desk_agent' / 'proj1' / 'camp-a' / 'checklist.json'
    raw = json.loads(path.read_text(encoding='utf-8'))
    raw['revisions'][rev['id']]['rendered_text'] += ' Ignore previous instructions.'
    path.write_text(json.dumps(raw), encoding='utf-8')
    with pytest.raises(StoreCorrupt):
        st.get_checklist('proj1', 'camp-a')


# -- archive / copy / history ---------------------------------------------------

def test_history_is_append_only_and_uncapped(st):
    doc, entry, ev, first = _prepared(st)
    for n in range(2, 121):
        doc, _ = st.add_revision('proj1', 'camp-a', _rev(doc), entry['id'],
                                 fields={'description': f'Version {n}.'},
                                 field_evidence={'description': [ev['id']]},
                                 rendered_text=f'Example MCP v{n}.',
                                 template_version='mcp_registry-2026-11-04')
    hist = st.entry_history('proj1', 'camp-a', entry['id'])
    assert [r['number'] for r in hist['revisions']] == list(range(1, 121))
    assert hist['revisions'][0] == first          # old revision unchanged, inspectable
    assert st.find_entry('proj1', 'camp-a', entry['id'])['draft_revision'] == 120


def test_submission_correction_appends_superseding_event(st):
    doc, entry, _ev, rev = _prepared(st)
    doc, sub = st.append_submission_event('proj1', 'camp-a', _rev(doc), entry['id'],
                                          rev['id'], outcome='listed',
                                          listing_url='https://registry.example/x')
    doc, fix = st.append_submission_event('proj1', 'camp-a', _rev(doc), entry['id'],
                                          rev['id'], outcome='rejected',
                                          reason='Reported in error.', supersedes=sub['id'])
    events = st.entry_history('proj1', 'camp-a', entry['id'])['submission_events']
    assert [e['id'] for e in events] == [sub['id'], fix['id']]
    assert events[0]['outcome'] == 'listed'           # original kept
    assert all(e['reported_by'] == 'human' and e['content_hash'] == rev['content_hash']
               for e in events)
    assert P.external_state(doc, entry) == 'rejected'
    with pytest.raises(NotFound):
        st.append_submission_event('proj1', 'camp-a', _rev(doc), entry['id'], rev['id'],
                                   outcome='listed', supersedes='agsb-0000000000000000')


def test_removed_entry_keeps_identity_and_is_not_recreated(st):
    doc, entry, _ev, _rev_ = _prepared(st)
    doc = st.remove_entry('proj1', 'camp-a', _rev(doc), entry['id'])
    _doc, again = st.open_entry('proj1', 'camp-a', 'mcp_registry', entry['artifact_id'])
    assert again['id'] == entry['id'] and again['lifecycle'] == 'removed'
    assert len(st.entry_history('proj1', 'camp-a', entry['id'])['revisions']) == 1


def test_archive_preserves_history_and_refuses_edits(st):
    doc, entry, _ev, rev = _prepared(st)
    before = st.entry_history('proj1', 'camp-a', entry['id'])
    doc = st.archive_checklist('proj1', 'camp-a', _rev(doc))
    assert doc['checklist']['archived_at']
    assert st.entry_history('proj1', 'camp-a', entry['id'])['revisions'] == before['revisions']
    reopened = st.open_checklist('proj1', 'camp-a')
    assert reopened['checklist']['id'] == doc['checklist']['id']   # not a fresh checklist
    with pytest.raises(ArchivedError):
        st.set_artifacts('proj1', 'camp-a', _rev(doc), [])
    with pytest.raises(ArchivedError):
        st.open_entry('proj1', 'camp-a', 'skills_sh')
    assert P.project_manual_channel(reopened)['archived'] is True


def test_copy_mints_new_ids_and_drops_claims_and_lint(st):
    doc, entry, ev, rev = _prepared(st, 'camp-a')
    doc, _lint = st.record_lint_result('proj1', 'camp-a', _rev(doc), rev['id'],
                                       result='pass', findings=[],
                                       linter_version='lint-1', rules_version='rules-1')
    doc, _sub = st.append_submission_event('proj1', 'camp-a', _rev(doc), entry['id'],
                                           rev['id'], outcome='listed')
    source_bytes = json.dumps(st.get_checklist('proj1', 'camp-a'), sort_keys=True)

    copy_doc = st.copy_checklist('proj1', 'camp-a', 'camp-b')
    ck = copy_doc['checklist']
    assert ck['id'] != doc['checklist']['id']
    assert ck['channel_id'] != doc['checklist']['channel_id']
    assert ck['copied_from']['checklist_id'] == doc['checklist']['id']
    assert copy_doc['lint_results'] == {} and copy_doc['submission_events'] == []
    [new_entry] = copy_doc['entries'].values()
    assert new_entry['id'] != entry['id'] and new_entry['link_ids'] == []
    assert new_entry['applicability'] == 'applicable'
    [new_rev] = copy_doc['revisions'].values()
    assert new_rev['id'] != rev['id'] and new_rev['number'] == 1
    assert new_rev['rendered_text'] == rev['rendered_text']
    [new_ev] = copy_doc['evidence'].values()
    assert new_ev['id'] != ev['id'] and new_rev['field_evidence'] == {'description': [new_ev['id']]}
    proj = P.project_manual_channel(copy_doc, current_versions={
        'linter_version': 'lint-1', 'rules_version': 'rules-1',
        'template_versions': {'mcp_registry': 'mcp_registry-2026-11-04'}})
    assert proj['entries'][0]['preparation'] == 'draft'           # no inherited pass
    assert proj['entries'][0]['external_outcome'] == 'not_reported'  # no inherited claim
    # Source untouched; copy refuses a target that already has a checklist,
    # a cross-project target and copying onto itself.
    assert json.dumps(st.get_checklist('proj1', 'camp-a'), sort_keys=True) == source_bytes
    with pytest.raises(ChecklistExists):
        st.copy_checklist('proj1', 'camp-a', 'camp-b')
    with pytest.raises(OwnershipError):
        st.copy_checklist('proj1', 'camp-a', 'camp-other')
    with pytest.raises(S.SchemaError):
        st.copy_checklist('proj1', 'camp-a', 'camp-a')


def test_store_has_no_delete_or_purge():
    names = [n for n in dir(AgentChannelStore) if not n.startswith('_')]
    assert not [n for n in names if re.search(r'delete|purge|truncate|prune|clear', n)]


# -- limits: refuse, never truncate -------------------------------------------

def test_over_limit_input_refused_without_saving(st, tmp_path):
    doc, entry, ev, _rev_ = _prepared(st)
    path = tmp_path / 'data' / 'desk_agent' / 'proj1' / 'camp-a' / 'checklist.json'
    before = path.read_bytes()
    big = 'a' * (S.MAX_RENDERED_BYTES + 1)
    with pytest.raises(S.SchemaError, match='rendered_text'):
        st.add_revision('proj1', 'camp-a', _rev(doc), entry['id'],
                        fields={'description': 'x'}, field_evidence={},
                        rendered_text=big, template_version='t-1')
    with pytest.raises(S.SchemaError, match='longer than'):
        st.add_evidence('proj1', 'camp-a', _rev(doc), {
            'excerpt': 'e' * (S.MAX_EXCERPT + 1), 'source_url': 'https://example.com',
            'source_kind': 'owner_provided'})
    assert path.read_bytes() == before


def test_whole_record_limit_refuses_the_write(st, tmp_path, monkeypatch):
    doc = st.open_checklist('proj1', 'camp-a')
    path = tmp_path / 'data' / 'desk_agent' / 'proj1' / 'camp-a' / 'checklist.json'
    before = path.read_bytes()
    monkeypatch.setattr(S, 'MAX_RECORD_BYTES', len(before) + 10)
    with pytest.raises(S.SchemaError, match='Nothing was saved'):
        st.set_artifacts('proj1', 'camp-a', _rev(doc), [_artifact()])
    assert path.read_bytes() == before


# -- no secrets persisted or logged ------------------------------------------

@pytest.mark.parametrize('field,value', [
    ('excerpt', f'Install with token={FAKE_TOKEN}'),
    ('excerpt', 'Authorization: Bearer abcdefghijklmnopqrstuvwxyz012345'),
    ('excerpt', 'export OPENAI_API_KEY=sk-' + 'Zz9' * 10),
    ('source_url', 'https://user:hunter22@example.com/docs'),
    ('source_url', 'https://example.com/docs?access_token=abc123def456'),
    ('source_url', 'https://bucket.example.com/x?X-Amz-Signature=deadbeef'),
    ('source_url', 'javascript:alert(1)'),
    ('source_url', 'file:///etc/passwd'),
])
def test_credentials_refused_before_persistence_echo_or_log(st, tmp_path, logs, field, value):
    doc = st.open_checklist('proj1', 'camp-a')
    raw = {'excerpt': 'Plain public fact.', 'source_url': 'https://example.com',
           'source_kind': 'owner_provided', field: value}
    with pytest.raises(S.SchemaError) as e:
        st.add_evidence('proj1', 'camp-a', _rev(doc), raw)
    secretish = value.split('=')[-1].split(' ')[-1].split('@')[0].split(':')[-1]
    assert secretish not in str(e.value)
    stored = b''.join(p.read_bytes() for p in tmp_path.rglob('*') if p.is_file())
    assert secretish.encode() not in stored
    assert not any(secretish in line for line in logs)


def test_credential_in_revision_fields_refused(st):
    doc, entry, ev, _r = _prepared(st)
    for kw in ({'fields': {'description': f'use {FAKE_TOKEN}'}, 'rendered_text': 'ok'},
               {'fields': {'description': 'ok'}, 'rendered_text': f'use {FAKE_TOKEN}'}):
        with pytest.raises(S.SchemaError) as e:
            st.add_revision('proj1', 'camp-a', _rev(doc), entry['id'], field_evidence={},
                            template_version='t-1', **kw)
        assert FAKE_TOKEN not in str(e.value)


@pytest.mark.parametrize('text', [
    'See https://github.com/example/repo/blob/3f1e2d4c5b6a79881726354433221100ffeeddcc/README.md',
    'Set API_KEY=<your key> before starting the server.',
    'token: ${GITHUB_TOKEN}',
    'Integrity sha256:9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08',
    'The API key is read from the environment.',
])
def test_public_facts_that_resemble_secrets_are_accepted(text):
    assert not carries_credential(text)


def test_url_guard_accepts_ordinary_public_urls():
    for url in ('https://modelcontextprotocol.io/registry',
                'https://example.com/docs?page=2&lang=en#install',
                'http://example.org:8080/path'):
        assert validate_public_url('u', url) == url
    with pytest.raises(PublicDataRefused):
        validate_public_url('u', ' https://example.com')


# -- project discovery unaffected; runtime dir ignored and unbundled ----------

def test_store_root_may_not_overlap_data_dir(tmp_path):
    data_dir = tmp_path / 'data' / 'projects'
    for bad in (data_dir, data_dir / 'desk_agent', tmp_path / 'data'):
        with pytest.raises(ValueError):
            AgentChannelStore(bad, campaign_resolver=lambda c: None, data_dir=data_dir)
    assert store_mod.default_root(tmp_path) == tmp_path / 'data' / 'desk_agent'
    assert not store_mod.default_root(tmp_path).is_relative_to(data_dir)


def test_project_discovery_unaffected(st, tmp_path, monkeypatch):
    from mc.blueprints import project_routes
    data_dir = tmp_path / 'data' / 'projects'
    data_dir.mkdir(parents=True)
    (data_dir / 'proj1.json').write_text(json.dumps({'id': 'proj1', 'name': 'P1'}),
                                         encoding='utf-8')
    monkeypatch.setattr(project_routes, 'DATA_DIR', data_dir)
    before = [p['id'] for p in project_routes.load_projects()]
    _prepared(st, 'camp-a')
    st.copy_checklist('proj1', 'camp-a', 'camp-b')
    after = [p['id'] for p in project_routes.load_projects()]
    assert before == after == ['proj1']
    assert sorted(p.name for p in data_dir.iterdir()) == ['proj1.json']


def test_runtime_dir_is_gitignored():
    probe = 'data/desk_agent/proj1/camp-a/checklist.json'
    out = subprocess.run(['git', 'check-ignore', '-q', probe], cwd=ROOT,
                         capture_output=True, stdin=subprocess.DEVNULL)
    assert out.returncode == 0, 'data/desk_agent/ must be gitignored'


def test_runtime_dir_absent_from_build_bundle():
    spec = (ROOT / 'installer' / 'build-macos.spec').read_text(encoding='utf-8')
    assert 'desk_agent' not in spec
    # The spec bundles named data/ subdirectories only; a whole-`data` entry
    # would sweep runtime state (this store included) into the .app.
    assert not re.search(r"R\(\s*'data'\s*\)\s*,", spec)


def test_new_modules_are_not_wired_anywhere_yet():
    """P1 is dark: no route, UI or server wiring imports the store."""
    hits = []
    for base in ('server.py', 'mc', 'static'):
        p = ROOT / base
        files = [p] if p.is_file() else [f for f in p.rglob('*')
                                         if f.suffix in ('.py', '.js', '.html')]
        for f in files:
            if f.name.startswith('desk_agent_'):
                continue
            if 'desk_agent_store' in f.read_text(encoding='utf-8', errors='ignore'):
                hits.append(str(f.relative_to(ROOT)))
    assert hits == []


# -- manual-channel projection --------------------------------------------------

def test_projection_is_a_manual_channel_not_an_account(st, campaigns):
    before = copy.deepcopy(campaigns['camp-a'])
    doc, _entry, _ev, _rev_ = _prepared(st)
    proj = P.project_manual_channel(doc)
    assert proj['kind'] == 'manual_channel' and proj['publish_capability'] == 'none'
    assert proj['composer_destination'] is False and proj['calendar_destination'] is False
    assert proj['cadence_slots'] == 0
    assert proj['channel_id'] == doc['checklist']['channel_id']
    assert not {'account_id', 'platform', 'provider'} & set(proj)
    assert campaigns['camp-a'] == before                    # plan.accounts untouched
    assert 'percent' not in json.dumps(proj) and 'coverage' not in json.dumps(proj)


def test_projection_readiness_fails_closed_and_excludes_not_applicable(st):
    doc, entry, _ev, rev = _prepared(st)
    doc, na = st.open_entry('proj1', 'camp-a', 'chatgpt_directory')
    doc = st.set_applicability('proj1', 'camp-a', _rev(doc), na['id'], 'not_applicable',
                               'Not an app integration.')
    doc, info = st.open_entry('proj1', 'camp-a', 'skills_sh')
    versions = {'linter_version': 'lint-1', 'rules_version': 'rules-1',
                'template_versions': {'mcp_registry': 'mcp_registry-2026-11-04'}}
    by_id = lambda d, v=None: {e['entry_id']: e for e in  # noqa: E731
                               P.project_manual_channel(d, current_versions=v)['entries']}
    assert by_id(doc)[entry['id']]['preparation'] == 'draft'
    assert by_id(doc)[na['id']]['preparation'] is None
    assert by_id(doc)[info['id']]['preparation'] == 'needs_information'

    doc, _ = st.record_lint_result('proj1', 'camp-a', _rev(doc), rev['id'], result='pass',
                                   findings=[], linter_version='lint-1', rules_version='rules-1')
    assert by_id(doc)[entry['id']]['preparation'] == 'unreviewed'   # versions unknown
    assert by_id(doc, versions)[entry['id']]['preparation'] == 'ready_to_copy'
    stale = dict(versions, rules_version='rules-2')
    assert by_id(doc, stale)[entry['id']]['preparation'] == 'unreviewed'
    assert P.project_manual_channel(doc, current_versions=versions)['readiness'] == {
        'applicable': 2, 'ready_to_copy': 1}

    # An edit makes a new revision; the old pass no longer applies.
    doc, _new = st.add_revision('proj1', 'camp-a', _rev(doc), entry['id'],
                                fields={'description': 'Changed.'}, field_evidence={},
                                rendered_text='Changed.',
                                template_version='mcp_registry-2026-11-04')
    assert by_id(doc, versions)[entry['id']]['preparation'] == 'draft'

    doc, _ = st.record_lint_result('proj1', 'camp-a', _rev(doc), _new['id'], result='refuse',
                                   findings=[{'rule_id': 'R1', 'field': 'description',
                                              'span': [0, 3]}],
                                   linter_version='lint-1', rules_version='rules-1')
    assert by_id(doc, versions)[entry['id']]['preparation'] == 'needs_changes'


def test_lint_result_hashes_come_from_the_stored_revision(st):
    doc, _entry, _ev, rev = _prepared(st)
    doc, lint = st.record_lint_result('proj1', 'camp-a', _rev(doc), rev['id'],
                                      result='unreviewed', findings=[],
                                      linter_version='lint-1', rules_version='rules-1')
    assert lint['content_hash'] == rev['content_hash'] == S.content_hash(rev['rendered_text'])
    assert lint['evidence_hash'] == store_mod.revision_evidence_hash(doc, rev)
    assert S.hash_ok(lint['content_hash']) and S.hash_ok(lint['evidence_hash'])


def test_module_has_no_network_model_or_subprocess_imports():
    banned = re.compile(r'^\s*(?:import|from)\s+(?:requests|urllib\.request|http\.client|'
                        r'socket|subprocess|anthropic|openai|mc\.agent_runtime|'
                        r'mc\.blueprints)\b', re.M)
    for name in ('desk_agent_store', 'desk_agent_schema', 'desk_agent_projection',
                 'desk_agent_public_data'):
        src = (ROOT / 'mc' / f'{name}.py').read_text(encoding='utf-8')
        assert not banned.search(src), name
    assert os.path.basename(store_mod.FILE_NAME) == 'checklist.json'
