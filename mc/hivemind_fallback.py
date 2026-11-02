"""HR-9 character fallback policy. No filesystem, runtime probes or dispatch.

Lookups and reference parsing are injected by the roster boundary. Engine
resolution remains in engine_selection; an unusable engine never advances
this policy to another character or provider.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

WORK_TYPES = ('code', 'review', 'research', 'ui', 'analysis', 'other')
COMPLEXITIES = ('simple', 'standard', 'hard')
ROLE_KEY = 'hivemind_role_fallbacks'
TIERS_KEY = 'hivemind_complexity_tiers'
DEFAULT_TIERS = {'simple': 'tier:fast', 'standard': 'tier:balanced', 'hard': 'tier:best'}
DISCLOSURE_KEYS = ('requested_character', 'substituted', 'substitution_step', 'substitution_reason')


def planner_fields(unit: Mapping[str, Any]) -> dict[str, str]:
    """Optional planner inputs have stable defaults; invalid values refuse."""
    work_type = unit.get('work_type', 'other')
    complexity = unit.get('complexity', 'standard')
    if not isinstance(work_type, str) or work_type not in WORK_TYPES:
        raise ValueError(f'work_type must be one of {WORK_TYPES}')
    if not isinstance(complexity, str) or complexity not in COMPLEXITIES:
        raise ValueError(f'complexity must be one of {COMPLEXITIES}')
    return {'work_type': work_type, 'complexity': complexity}


def validate_config(config: Mapping[str, Any], parse_ref: Callable[[Any], str]) -> None:
    roles = config.get(ROLE_KEY, {})
    tiers = config.get(TIERS_KEY, {})
    if not isinstance(roles, dict) or any(k not in WORK_TYPES for k in roles):
        raise ValueError(f'{ROLE_KEY} must map work types to character references')
    for ref in roles.values():
        parse_ref(ref)
    if (not isinstance(tiers, dict) or any(k not in COMPLEXITIES for k in tiers)
            or any(v not in DEFAULT_TIERS.values() for v in tiers.values())):
        raise ValueError(f'{TIERS_KEY} must map complexities to tier:fast/balanced/best')


@dataclass(frozen=True)
class Choice:
    character_ref: str
    record: dict[str, Any] | None
    tier: str
    disclosure: dict[str, Any]


def choose(ref: str, config: Mapping[str, Any], *, work_type: str = 'other',
           complexity: str = 'standard', previously_defined: bool = False,
           lookup: Callable[[str], dict[str, Any] | None],
           parse_ref: Callable[[Any], str]) -> Choice:
    fields = planner_fields({'work_type': work_type, 'complexity': complexity})
    disclosure: dict[str, Any] = {
        'requested_character': ref, 'substituted': False,
        'substitution_step': '', 'substitution_reason': '',
    }
    record = lookup(ref.partition(':')[2])
    if record:
        return Choice(ref, record, '', disclosure)
    disclosure.update(substituted=True,
                      substitution_reason='missing_at_attempt' if previously_defined else 'never_defined')
    roles = config.get(ROLE_KEY) or {}
    mapped = roles.get(fields['work_type']) if isinstance(roles, Mapping) else None
    if mapped:
        mapped = parse_ref(mapped)
        record = lookup(mapped.partition(':')[2]) if mapped != ref else None
        if record:
            disclosure['substitution_step'] = 'role_fallback'
            return Choice(mapped, record, '', disclosure)
    tiers = config.get(TIERS_KEY) or {}
    tier = tiers.get(complexity, DEFAULT_TIERS[complexity]) if isinstance(tiers, Mapping) else DEFAULT_TIERS[complexity]
    if tier not in DEFAULT_TIERS.values():
        raise ValueError(f'{TIERS_KEY}: invalid tier {tier!r}')
    disclosure['substitution_step'] = 'tier_fallback'
    return Choice('', None, tier, disclosure)
