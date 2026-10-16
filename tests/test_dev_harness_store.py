"""Atomic activation and exact-input prerequisites, using disposable state."""
from __future__ import annotations

import copy
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from mc import atomic_json, dev_harness_store
from mc.dev_harness_catalogue import digest, load_catalogue
from mc.dev_harness_policy import lost_protections, resolve_policy
from mc.dev_harness_store import HarnessStore, RevisionConflict, StoreError


@pytest.fixture
def setup(tmp_path):
    return HarnessStore(tmp_path / 'data'), load_catalogue()


def activate(store, catalogue, draft=None, mode='preview', revision=0, evidence_id=None, project='one'):
    draft = draft or {'profile': 'local-hobby'}
    policy = resolve_policy(draft, catalogue)
    current = store.read(project)
    return store.activate(project, expected_revision=revision, draft=draft, mode=mode,
                          catalogue=catalogue, evidence_id=evidence_id,
                          acknowledged_losses=lost_protections(current['policy'] if current else None,
                                                               policy, mode, catalogue))


def ready_fixture(store, catalogue, practice='P02'):
    """Simulated future runner capability, never changes shipped descriptors.

    This tests the activation boundary; it does not claim native checks ran.
    """
    catalogue = copy.deepcopy(catalogue)
    catalogue['practices'][practice]['delivered_coverage'] = {
        'deterministic': 'native_command', 'reviewer': 'independent_verdict',
        'guidance': 'guidance_only', 'reason': 'Synthetic test capability'}
    catalogue['digest'] = digest({k: v for k, v in catalogue.items() if k != 'digest'})
    draft = {'profile': 'external-product',
             'practices': {k: {'enabled': k == practice} for k in catalogue['practices']},
             'digests': {k: digest(k) for k in ('sources', 'baseline', 'native_rules')}}
    draft['practices'][practice]['caps'] = {key: 75 for key in catalogue['practices'][practice]['numeric_requirements']}
    policy = resolve_policy(draft, catalogue)
    evidence = {'project_id': 'one', 'active_revision': 0, 'policy_digest': digest(policy),
                'catalogue_digest': catalogue['digest'], 'digests': policy['digests'],
                'status': 'pass', 'kind': 'measured_preview', 'source_verification': 'pass',
                'baseline_verification': 'pass', 'native_rule_verification': 'pass',
                'practices': {practice: {'status': 'pass', 'guidance': 'verified',
                    'adapter_fixtures': 'pass', 'caps': policy['practices'][practice]['caps'],
                    'coverage': catalogue['practices'][practice]['delivered_coverage']}}}
    path = store.project_dir('one') / 'evidence/fixture.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(evidence), encoding='utf-8')
    return catalogue, draft, evidence, path


def test_default_and_project_local_snapshots(setup):
    store, catalogue = setup
    assert store.read('one') is None
    first = activate(store, catalogue)
    assert first['mode'] == 'preview'
    assert first['revision'] == 1
    second = activate(store, catalogue, {'profile': 'external-product'}, project='two')
    assert second['policy']['profile'] == 'external-product'
    assert store.read('one') == first
    assert store.read('two') == second
    assert store.root.name == 'dev_harness' and store.root.parent.name == 'data'


def test_frozen_catalogue_and_policy_ignore_repository_or_release_changes(setup):
    store, catalogue = setup
    record = activate(store, catalogue)
    catalogue['practices']['P01']['title'] = 'changed release'
    assert store.read('one') == record
    record['policy']['profile'] = 'changed caller copy'
    assert store.read('one')['policy']['profile'] == 'local-hobby'


@pytest.mark.parametrize('mode', ['off', 'preview', 'enforce'])
def test_revision_conflict_has_no_write_even_for_mode_changes(setup, mode):
    store, catalogue = setup
    activate(store, catalogue)
    path = store.project_dir('one') / 'activated/active.json'
    before = path.read_bytes()
    with pytest.raises(RevisionConflict):
        activate(store, catalogue, mode=mode)
    assert path.read_bytes() == before


def test_mode_transitions_keep_audit_in_same_atomic_snapshot(setup):
    store, catalogue = setup
    activate(store, catalogue)
    second = activate(store, catalogue, mode='off', revision=1)
    assert [entry['mode'] for entry in second['audit']] == ['preview', 'off']
    assert [entry['revision'] for entry in second['audit']] == [1, 2]
    assert store.read('one')['audit'] == second['audit']


def test_concurrent_activation_is_compare_and_swap(setup):
    store, catalogue = setup
    def attempt(_):
        try:
            return activate(store, catalogue)['revision']
        except RevisionConflict:
            return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt, range(2)), key=str) == [1, 'conflict']
    assert store.read('one')['revision'] == 1


def test_first_activation_is_never_seen_as_a_missing_snapshot(setup, monkeypatch):
    """A reader on another instance (render() builds one per context) must see
    'unconfigured' or the finished snapshot, never 'previously configured but
    missing', at every point of the first write."""
    store, catalogue = setup
    other = HarnessStore(store.root.parent)
    seen = []
    real = dev_harness_store.write_json_atomic

    def probing_write(path, obj, **kw):
        seen.append(other.read('one'))      # after any mkdir, before the write
        real(path, obj, **kw)
        seen.append(other.read('one'))      # file written, not yet in place
    monkeypatch.setattr(dev_harness_store, 'write_json_atomic', probing_write)
    record = activate(store, catalogue)
    assert seen == [None, None]
    assert other.read('one') == record
    assert [p.name for p in store.project_dir('one').iterdir()] == ['activated']


def test_containment_ignores_extended_length_prefix_but_still_rejects_escapes():
    from pathlib import Path
    within = dev_harness_store._within
    prefix = chr(92) * 2 + '?' + chr(92)    # the extended-length prefix
    if Path('C:/').drive:
        base = Path('C:/data/dev_harness/one')
        assert within(Path(prefix + 'C:/data/dev_harness/one/activated/active.json'), base)
        assert not within(Path(prefix + 'C:/data/dev_harness/two'), base)
    assert not within(Path('/data/other'), Path('/data/dev_harness/one'))


def test_failed_first_activation_leaves_project_unconfigured(setup, monkeypatch):
    store, catalogue = setup
    def fail(*args):
        raise OSError('injected replace failure')
    monkeypatch.setattr(atomic_json, '_replace_with_retry', fail)
    with pytest.raises(OSError):
        activate(store, catalogue)
    assert store.read('one') is None
    assert list(store.project_dir('one').iterdir()) == []


def test_replace_failure_preserves_entire_old_snapshot_and_removes_temp(setup, monkeypatch):
    store, catalogue = setup
    activate(store, catalogue)
    path = store.project_dir('one') / 'activated/active.json'
    before = path.read_bytes()
    def fail(*args):
        raise OSError('injected replace failure')
    monkeypatch.setattr(atomic_json, '_replace_with_retry', fail)
    with pytest.raises(OSError):
        activate(store, catalogue, mode='off', revision=1)
    assert path.read_bytes() == before
    assert list(path.parent.iterdir()) == [path]


@pytest.mark.parametrize('bad', ['', '\x00' * 100, '{', '[]', '{"version":1}'])
def test_corrupt_state_is_error_and_cannot_be_overwritten(setup, bad):
    store, catalogue = setup
    activate(store, catalogue)
    path = store.project_dir('one') / 'activated/active.json'
    path.write_text(bad, encoding='utf-8')
    before = path.read_bytes()
    with pytest.raises(StoreError):
        store.read('one')
    with pytest.raises(StoreError):
        activate(store, catalogue, revision=1)
    assert path.read_bytes() == before


def test_missing_enabled_snapshot_is_error_not_unconfigured(setup):
    store, catalogue = setup
    activate(store, catalogue)
    (store.project_dir('one') / 'activated/active.json').unlink()
    with pytest.raises(StoreError):
        store.read('one')


def test_snapshot_tampering_cannot_change_bar(setup):
    store, catalogue = setup
    record = activate(store, catalogue)
    record['mode'] = 'off'
    path = store.project_dir('one') / 'activated/active.json'
    path.write_text(json.dumps(record), encoding='utf-8')
    with pytest.raises(StoreError):
        store.read('one')


def test_supported_subset_activation_uses_stored_exact_evidence(setup):
    store, catalogue = setup
    catalogue, draft, evidence, _ = ready_fixture(store, catalogue)
    record = activate(store, catalogue, draft, mode='enforce', evidence_id='fixture')
    assert record['evidence'] == evidence
    assert record['mode'] == 'enforce'
    assert record['acknowledged_losses'] == [k for k in catalogue['practices'] if k not in {'P02', 'P14'}]
    assert store.read('one') == record


@pytest.mark.parametrize('mutation', ['policy', 'project', 'revision', 'catalogue', 'sources',
                                    'baseline', 'native_rules', 'status', 'kind', 'coverage',
                                    'caps', 'guidance', 'fixtures', 'missing-practice',
                                    'source_verification', 'baseline_verification', 'native_rule_verification'])
def test_bad_preview_evidence_never_creates_active_snapshot(setup, mutation):
    store, catalogue = setup
    catalogue, draft, evidence, path = ready_fixture(store, catalogue)
    if mutation in {'sources', 'baseline', 'native_rules'}:
        evidence['digests'][mutation] = 'b' * 64
    elif mutation in {'coverage', 'caps', 'guidance', 'fixtures'}:
        key = 'adapter_fixtures' if mutation == 'fixtures' else mutation
        evidence['practices']['P02'][key] = 'wrong'
    elif mutation == 'missing-practice':
        evidence['practices'] = {}
    else:
        key = {'policy': 'policy_digest', 'project': 'project_id', 'revision': 'active_revision',
               'catalogue': 'catalogue_digest'}.get(mutation, mutation)
        evidence[key] = 'wrong'
    path.write_text(json.dumps(evidence), encoding='utf-8')
    with pytest.raises(ValueError):
        activate(store, catalogue, draft, mode='enforce', evidence_id='fixture')
    assert not (store.project_dir('one') / 'activated').exists()


@pytest.mark.parametrize('missing', ['sources', 'baseline', 'native_rules', 'caps', 'unsupported'])
def test_missing_activation_prerequisites_fail_without_write(setup, missing):
    store, catalogue = setup
    catalogue, draft, evidence, path = ready_fixture(store, catalogue)
    if missing in draft['digests']:
        del draft['digests'][missing]
    elif missing == 'caps':
        draft['practices']['P02']['caps'] = {}
    else:
        catalogue = load_catalogue()
    # Refresh exact-input binding so the missing prerequisite itself is tested.
    policy = resolve_policy(draft, catalogue)
    evidence.update(policy_digest=digest(policy), digests=policy['digests'], catalogue_digest=catalogue['digest'])
    path.write_text(json.dumps(evidence), encoding='utf-8')
    with pytest.raises(ValueError):
        activate(store, catalogue, draft, mode='enforce', evidence_id='fixture')
    assert not (store.project_dir('one') / 'activated').exists()


def test_full_product_profile_cannot_activate_on_m1(setup):
    store, catalogue = setup
    with pytest.raises(ValueError, match='evidence'):
        activate(store, catalogue, {'profile': 'external-product'}, mode='enforce')
    assert not store.root.exists()


@pytest.mark.parametrize('coverage', ['guidance_only', 'unsupported', 'independent_verdict'])
def test_required_machine_checks_cannot_be_replaced_by_guidance_or_review(setup, coverage):
    store, catalogue = setup
    catalogue, draft, evidence, path = ready_fixture(store, catalogue)
    catalogue['practices']['P02']['delivered_coverage']['deterministic'] = coverage
    catalogue['digest'] = digest({k: v for k, v in catalogue.items() if k != 'digest'})
    policy = resolve_policy(draft, catalogue)
    evidence.update(policy_digest=digest(policy), catalogue_digest=catalogue['digest'])
    evidence['practices']['P02']['coverage'] = catalogue['practices']['P02']['delivered_coverage']
    path.write_text(json.dumps(evidence), encoding='utf-8')
    with pytest.raises(ValueError, match='Unsupported'):
        activate(store, catalogue, draft, mode='enforce', evidence_id='fixture')
    assert not (store.project_dir('one') / 'activated').exists()


def test_evidence_ids_cannot_escape_and_missing_or_corrupt_evidence_is_not_green(setup):
    store, catalogue = setup
    catalogue, draft, _, path = ready_fixture(store, catalogue)
    for identity in ('../fixture', 'absent'):
        with pytest.raises(ValueError):
            activate(store, catalogue, draft, mode='enforce', evidence_id=identity)
    path.write_text('broken', encoding='utf-8')
    with pytest.raises(StoreError):
        activate(store, catalogue, draft, mode='enforce', evidence_id='fixture')
    assert not (store.project_dir('one') / 'activated').exists()


def test_loss_acknowledgement_required_before_weaken_or_subset(setup):
    store, catalogue = setup
    with pytest.raises(ValueError, match='lost protection'):
        store.activate('one', expected_revision=0, draft={}, mode='off', catalogue=catalogue)
    assert not store.root.exists()


@pytest.mark.parametrize('project', ['../escape', 'one/two', '..', '', 'a\\b'])
def test_project_identity_cannot_escape_state_root(setup, project):
    store, _ = setup
    with pytest.raises(ValueError):
        store.read(project)


def test_project_records_directory_is_never_used(tmp_path):
    with pytest.raises(ValueError):
        HarnessStore(tmp_path / 'data/projects')
