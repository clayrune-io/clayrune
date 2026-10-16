"""DH-M1 catalogue data contracts; no tools are downloaded or executed."""
from __future__ import annotations

import copy
import json
import shutil

import pytest

from mc.dev_harness_catalogue import (CATALOGUE_ROOT, PRACTICE_IDS, load_catalogue,
                                     validate_practice, validate_profile)
from mc.dev_harness_policy import resolve_policy


def test_complete_separate_entries_and_honest_delivery():
    catalogue = load_catalogue()
    assert tuple(catalogue['practices']) == PRACTICE_IDS
    assert len(list((CATALOGUE_ROOT / 'practices').glob('*.json'))) == 22
    assert len(list((CATALOGUE_ROOT / 'profiles').glob('*.json'))) == 2
    for key, descriptor in catalogue['practices'].items():
        validate_practice(descriptor)
        assert descriptor['id'] == key
        assert descriptor['delivered_coverage']['deterministic'] == 'unsupported'
        assert descriptor['delivered_coverage']['reviewer'] == 'unsupported'
        assert descriptor['numeric_requirements'] == [] or key in {'P01', 'P02', 'P14'}
    for profile in catalogue['profiles'].values():
        validate_profile(profile)
        assert profile['proposal'] is True


@pytest.mark.parametrize('field', ['guidance', 'target_check', 'delivered_coverage',
                                  'expected_evidence', 'repair', 'baseline_policy', 'problem'])
@pytest.mark.parametrize('practice_id', PRACTICE_IDS)
def test_every_descriptor_rejects_missing_contract(practice_id, field):
    descriptor = load_catalogue()['practices'][practice_id]
    descriptor.pop(field)
    with pytest.raises(ValueError):
        validate_practice(descriptor)


@pytest.mark.parametrize('field,value', [('pin', 'main'), ('licence', ''),
                                        ('subpath', '../escape'), ('disposition', 'installed')])
def test_source_provenance_is_pinned_and_not_an_install_claim(field, value):
    descriptor = load_catalogue()['practices']['P01']
    descriptor['guidance'][0][field] = value
    with pytest.raises(ValueError):
        validate_practice(descriptor)


@pytest.mark.parametrize('field,value', [('stages', ['merge', 'merge']), ('stages', [[]]),
    ('numeric_requirements', [None]), ('severity', []), ('version', True)])
def test_malformed_descriptor_types_are_rejected(field, value):
    descriptor = load_catalogue()['practices']['P01']
    descriptor[field] = value
    with pytest.raises(ValueError):
        validate_practice(descriptor)


def test_registry_rejects_missing_extra_and_misnamed_entries(tmp_path):
    shutil.copytree(CATALOGUE_ROOT, tmp_path / 'catalogue')
    root = tmp_path / 'catalogue'
    path = root / 'practices/P01.json'
    original = path.read_text(encoding='utf-8')
    path.unlink()
    with pytest.raises(ValueError):
        load_catalogue(root)
    path.write_text(original, encoding='utf-8')
    extra = root / 'practices/P23.json'
    extra.write_text(original, encoding='utf-8')
    with pytest.raises(ValueError):
        load_catalogue(root)
    extra.unlink()
    descriptor = json.loads(original)
    descriptor['id'] = 'P02'
    path.write_text(json.dumps(descriptor), encoding='utf-8')
    with pytest.raises(ValueError):
        load_catalogue(root)


def test_profiles_and_project_overrides_cannot_mutate_each_other():
    catalogue = load_catalogue()
    original = copy.deepcopy(catalogue)
    first = resolve_policy({'profile': 'local-hobby', 'practices': {
        'P02': {'enabled': False, 'caps': {'changed_line_floor_percent': 75}}}}, catalogue)
    second = resolve_policy({'profile': 'external-product'}, catalogue)
    third = resolve_policy({'profile': 'local-hobby'}, catalogue)
    assert first['practices']['P02']['enabled'] is False
    assert second['practices']['P02']['enabled'] is True
    assert third['practices']['P02']['caps'] == {}
    assert catalogue == original
    assert 'mode' not in first


@pytest.mark.parametrize('draft', [None, [], {'profile': []}, {'mode': 'enforce'},
    {'profile': 'unknown'}, {'practices': {'P23': {}}},
    {'practices': {'P02': {'enabled': 1}}}, {'practices': {'P02': {'severity': []}}},
    {'practices': {'P02': {'caps': {'changed_line_floor_percent': True}}}},
    {'practices': {'P02': {'caps': {'changed_line_floor_percent': 101}}}},
    {'practices': {'P02': {'caps': {'changed_line_floor_percent': float('nan')}}}},
    {'practices': {'P01': {'caps': {'file_lines': -1}}}},
    {'digests': {'sources': 'latest'}}, {'digests': {'unapproved': 'a' * 64}}])
def test_invalid_policy_never_resolves(draft):
    with pytest.raises(ValueError):
        resolve_policy(draft, load_catalogue())
