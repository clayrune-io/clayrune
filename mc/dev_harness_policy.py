"""Pure project policy resolution and activation prerequisite validation (DH-M1)."""
from __future__ import annotations

import copy
import math
import re
from typing import Any

from mc.dev_harness_catalogue import digest

MODES = {'off', 'preview', 'enforce'}
DIGEST_KEYS = ('sources', 'baseline', 'native_rules')


def require_digest(value: Any) -> None:
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{64}', value):
        raise ValueError('Expected a SHA-256 digest')


def resolve_policy(draft: dict[str, Any], catalogue: dict[str, Any]) -> dict[str, Any]:
    """Profiles and overrides produce fresh project-local data, never mutate presets.

    Mode is intentionally absent: importing a draft has no activation authority.
    Caps stay unset until the owner supplies measured values.
    """
    if not isinstance(draft, dict) or set(draft) - {'profile', 'practices', 'digests'}:
        raise ValueError('Invalid policy fields')
    profile_id = draft.get('profile', 'local-hobby')
    if not isinstance(profile_id, str):
        raise ValueError('Invalid profile identity')
    profile = catalogue['profiles'].get(profile_id)
    if profile is None:
        raise ValueError('Unknown profile')
    selections = copy.deepcopy(profile['practices'])
    overrides = draft.get('practices', {})
    if not isinstance(overrides, dict) or set(overrides) - set(selections):
        raise ValueError('Unknown practice toggle')
    for practice_id, selection in selections.items():
        selection['caps'] = {}
        override = overrides.get(practice_id, {})
        if not isinstance(override, dict) or set(override) - {'enabled', 'severity', 'caps'}:
            raise ValueError('Invalid practice override')
        selection.update(copy.deepcopy(override))
        if (type(selection['enabled']) is not bool or not isinstance(selection['severity'], str)
                or selection['severity'] not in {'warning', 'required'}):
            raise ValueError('Invalid practice toggle/severity')
        caps = selection['caps']
        allowed = catalogue['practices'][practice_id]['numeric_requirements']
        if not isinstance(caps, dict) or set(caps) - set(allowed):
            raise ValueError('Unknown numeric cap')
        for key, value in caps.items():
            if type(value) not in (float, int) or not math.isfinite(value) or value < 0:
                raise ValueError('Caps must be finite nonnegative numbers')
            if key.endswith('_percent') and value > 100:
                raise ValueError('Percentage must be between 0 and 100')
    pins = draft.get('digests', {})
    if not isinstance(pins, dict) or set(pins) - set(DIGEST_KEYS):
        raise ValueError('Unknown policy digests')
    for pin in pins.values():
        require_digest(pin)
    return {'version': 1, 'profile': profile['id'], 'practices': selections,
            'digests': copy.deepcopy(pins), 'catalogue_digest': catalogue['digest']}


def lost_protections(old: dict[str, Any] | None, new: dict[str, Any], mode: str,
                     catalogue: dict[str, Any]) -> list[str]:
    """Explicit omissions relative to the proposal AND the previously active bar."""
    lost = []
    for key, selection in new['practices'].items():
        proposed = catalogue['profiles'][new['profile']]['practices'][key]
        prior = (old or {}).get('practices', {}).get(key, {})
        was_required = proposed['severity'] == 'required' or (
            prior.get('enabled') and prior.get('severity') == 'required')
        if was_required and (not selection['enabled'] or selection['severity'] != 'required' or mode != 'enforce'):
            lost.append(key)
        elif prior.get('caps') and prior['caps'] != selection['caps']:
            lost.append(key)
    return sorted(lost)


def validate_enforcement(policy: dict[str, Any], evidence: dict[str, Any],
                         catalogue: dict[str, Any], project_id: str,
                         revision: int) -> None:
    """Only a stored, exact-input measured Preview can satisfy prerequisites.

    The runner supplies this record server-side in DH-M3. Client assertions
    about tool availability/results are never used as trusted evidence.
    """
    if set(policy['digests']) != set(DIGEST_KEYS):
        raise ValueError('Source, measured baseline and native-rule digests are required')
    if (evidence.get('project_id') != project_id or evidence.get('active_revision') != revision
            or evidence.get('policy_digest') != digest(policy)
            or evidence.get('digests') != policy['digests']
            or evidence.get('catalogue_digest') != catalogue['digest']
            or evidence.get('status') != 'pass' or evidence.get('kind') != 'measured_preview'):
        raise ValueError('Missing, stale or incompatible measured Preview evidence')
    for key in ('source_verification', 'baseline_verification', 'native_rule_verification'):
        if evidence.get(key) != 'pass':
            raise ValueError(f'Missing {key}')
    outcomes = evidence.get('practices')
    if not isinstance(outcomes, dict):
        raise ValueError('Missing per-practice evidence')
    for key, selection in policy['practices'].items():
        if not selection['enabled'] or selection['severity'] != 'required':
            continue
        descriptor = catalogue['practices'][key]
        delivered = descriptor['delivered_coverage']
        if (delivered['deterministic'] != 'native_command'
                or delivered['reviewer'] != 'independent_verdict'):
            raise ValueError(f'{key}: required target checks are Unsupported')
        if not set(descriptor['numeric_requirements']) <= set(selection['caps']):
            raise ValueError(f'{key}: measured human-selected caps are required')
        result = outcomes.get(key)
        if not isinstance(result, dict) or result.get('status') != 'pass':
            raise ValueError(f'{key}: missing passing practice evidence')
        if result.get('coverage') != delivered or result.get('caps') != selection['caps']:
            raise ValueError(f'{key}: evidence coverage/caps differ')
        if result.get('guidance') != 'verified' or result.get('adapter_fixtures') != 'pass':
            raise ValueError(f'{key}: guidance/resources or adapter fixtures unverified')
