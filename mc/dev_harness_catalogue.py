"""Versioned practice/profile data. No executable rules or installed-tool claims.

The catalogue describes the full target; delivered coverage is deliberately
separate and starts Unsupported until its owning implementation lands.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

CATALOGUE_ROOT = Path(__file__).resolve().parent.parent / 'data' / 'dev_harness_catalogue'
PRACTICE_IDS = tuple(f'P{i:02}' for i in range(1, 23))
PROFILE_IDS = ('local-hobby', 'external-product')
COVERAGE = {'native_command', 'independent_verdict', 'guidance_only', 'unsupported'}


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode('utf-8')).hexdigest()


def _text(value: Any, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'Catalogue requires {name}')


def _strings(value: Any, name: str, *, allow_empty: bool = False) -> None:
    if (not isinstance(value, list) or (not value and not allow_empty)
            or any(not isinstance(item, str) or not item.strip() for item in value)
            or len(set(value)) != len(value)):
        raise ValueError(f'Invalid catalogue {name}')


def validate_practice(entry: dict[str, Any]) -> None:
    if (entry.get('id') not in PRACTICE_IDS or type(entry.get('version')) is not int
            or entry.get('version') != 1):
        raise ValueError('Unknown practice identity/version')
    for key in ('title', 'problem', 'rationale', 'applicability', 'baseline_policy',
                'repair', 'expected_evidence'):
        _text(entry.get(key), key)
    if entry.get('severity') not in ('warning', 'required'):
        raise ValueError('Invalid practice severity')
    _strings(entry.get('stages'), 'stages')
    if not set(entry['stages']) <= {'setup', 'diff', 'merge', 'release'}:
        raise ValueError('Invalid practice stages')
    guidance = entry.get('guidance')
    if not isinstance(guidance, list) or not guidance:
        raise ValueError('Missing guidance provenance')
    for source in guidance:
        if not isinstance(source, dict):
            raise ValueError('Malformed guidance provenance')
        for key in ('source', 'subpath', 'pin', 'licence', 'origin', 'disposition'):
            _text(source.get(key), key)
        if not re.fullmatch(r'[0-9a-f]{40}', source['pin']):
            raise ValueError('Guidance must use an immutable commit pin')
        subpath = source['subpath']
        if ('\\' in subpath or ':' in subpath
                or any(part in ('', '.', '..') for part in subpath.split('/'))):
            raise ValueError('Unsafe guidance subpath')
        if source['disposition'] not in {'proposed', 'reference'}:
            raise ValueError('Catalogue provenance cannot claim installation')
    target = entry.get('target_check')
    if not isinstance(target, dict):
        raise ValueError('Missing target check')
    for key in ('deterministic', 'reviewer', 'limits', 'configuration'):
        _text(target.get(key), key)
    _strings(target.get('adapters'), 'tool adapters')
    delivered = entry.get('delivered_coverage')
    if not isinstance(delivered, dict) or set(delivered) != {'deterministic', 'reviewer', 'guidance', 'reason'}:
        raise ValueError('Missing delivered coverage')
    for key in ('deterministic', 'reviewer', 'guidance'):
        if not isinstance(delivered[key], str) or delivered[key] not in COVERAGE:
            raise ValueError('Invalid delivered coverage')
    _text(delivered['reason'], 'coverage reason')
    _strings(entry.get('numeric_requirements'), 'numeric requirements', allow_empty=True)


def validate_profile(entry: dict[str, Any]) -> None:
    if (entry.get('id') not in PROFILE_IDS or type(entry.get('version')) is not int
            or entry.get('version') != 1 or entry.get('mode') != 'preview'):
        raise ValueError('Profiles must be versioned Preview proposals')
    _text(entry.get('title'), 'profile title')
    selections = entry.get('practices')
    if not isinstance(selections, dict) or set(selections) != set(PRACTICE_IDS):
        raise ValueError('Profile must explicitly select all 22 practices')
    for selection in selections.values():
        if (not isinstance(selection, dict) or set(selection) != {'enabled', 'severity'}
                or type(selection['enabled']) is not bool
                or selection['severity'] not in ('required', 'warning')):
            raise ValueError('Invalid profile selection')


def load_catalogue(root: Path | None = None) -> dict[str, Any]:
    root = root or CATALOGUE_ROOT
    practices, profiles = {}, {}
    for folder, ids, target, validator in (
        ('practices', PRACTICE_IDS, practices, validate_practice),
        ('profiles', PROFILE_IDS, profiles, validate_profile),
    ):
        if {p.stem for p in (root / folder).glob('*.json')} != set(ids):
            raise ValueError(f'Catalogue {folder} entries differ from registry')
        for entry_id in ids:
            entry = json.loads((root / folder / f'{entry_id}.json').read_text(encoding='utf-8'))
            if not isinstance(entry, dict) or entry.get('id') != entry_id:
                raise ValueError('Catalogue file/entry identity mismatch')
            validator(entry)
            target[entry_id] = entry
    data = {'version': 1, 'practices': practices, 'profiles': profiles,
            'languages': ['python', 'javascript', 'typescript']}
    return {**data, 'digest': digest(data)}
