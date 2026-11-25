"""Resolve the token rollover limit shared by all rollover paths (MC-1072)."""
from collections.abc import Mapping
from functools import lru_cache
from math import isfinite
from typing import Any, NamedTuple

from mc.core import _log
from mc.rollover_cost import continuity_target
from mc.rollover_growth_snapshot import GROWTH_ROWS
from mc.rollover_model_table import MODEL_TABLE, ModelInputs, canonical_model_id
from mc.rollover_request import effort_name

DEFAULT_THRESHOLD = 200_000
MIN_THRESHOLD = 60_000
_SELECTORS = {'model': 4, 'character': 2, 'effort': 1}


class Resolution(NamedTuple):
    tokens: int
    reason: str
    mean_growth: float | None = None
    growth_source: str | None = None


def _tokens(value: Any) -> int | None:
    """Accept integer token counts, including integer config strings."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        tokens = int(value)
    except (ValueError, OverflowError):
        return None
    if isinstance(value, float) and value != tokens:
        return None
    return tokens


def _character_ref(session_or_character: Any) -> str:
    character = session_or_character
    if isinstance(character, Mapping) and 'character' in character:
        character = character.get('character')
    if isinstance(character, str):
        return character
    if isinstance(character, Mapping):
        name = character.get('name')
        scope = character.get('scope') or 'global'
        if isinstance(name, str) and name and isinstance(scope, str):
            return f'{scope}:{name}'
    return ''


def model_for_session(session: Any) -> str | None:
    """Prefer the observed model; a present empty model means native default."""
    if not isinstance(session, Mapping):
        return None
    model = session.get('observed_model') or (
        session['model'] if 'model' in session else session.get('agent_model'))
    return canonical_model_id(model)


@lru_cache(maxsize=256)
def _log_ignored_override(selectors: tuple[tuple[str, str], ...], tokens: int) -> None:
    scope = ', '.join(f'{key}={value}' for key, value in selectors)
    _log(f'[rollover-threshold] ignoring {scope} override {tokens}: below fresh floor + 60000')


def _explicit_override(
    config: Mapping[str, Any], character: str, model: str | None,
    effort: str | None, fresh: int | None,
) -> tuple[int, str] | None:
    values = {'model': model, 'character': character, 'effort': effort}
    entries = config.get('context_rollover_overrides')
    entries = list(entries) if isinstance(entries, list) else []
    legacy = config.get('context_rollover_by_character')
    if character and isinstance(legacy, Mapping) and character in legacy:
        entries.append({'character': character, 'tokens': legacy[character]})
    matches: list[tuple[tuple[tuple[str, str], ...], int]] = []
    for entry in entries:
        if not isinstance(entry, Mapping) or entry.keys() - (values.keys() | {'tokens'}):
            continue
        tokens = _tokens(entry.get('tokens'))
        if tokens is None:
            continue
        selectors: list[tuple[str, str]] = []
        for key in _SELECTORS:
            if key not in entry:
                continue
            value = entry[key]
            if key == 'model':
                value = canonical_model_id(value)
            elif key == 'effort':
                value = effort_name(value)
            if not isinstance(value, str) or not value or value != values[key]:
                break
            selectors.append((key, value))
        else:
            if selectors:
                matches.append((tuple(selectors), tokens))
    matches.sort(key=lambda item: (
        len(item[0]), sum(_SELECTORS[key] for key, _ in item[0])), reverse=True)
    for selected, tokens in matches:
        if fresh is not None and tokens < fresh + MIN_THRESHOLD:
            _log_ignored_override(selected, tokens)
            continue
        return max(MIN_THRESHOLD, tokens), 'override:' + '+'.join(key for key, _ in selected)
    return None


def _growth(
    character: str, model: str, effort: str | None, inputs: ModelInputs,
) -> tuple[float, str] | None:
    candidates = [
        ((character, model, effort), 'cell'),
        ((None, model, effort), 'model_effort'),
        ((None, model, None), 'model'),
    ]
    for key, source in candidates:
        if source != 'model' and not effort:
            continue
        sample = GROWTH_ROWS.get(key)
        if (sample and sample.coverage_met and sample.mean_growth
                and isfinite(sample.mean_growth) and sample.mean_growth > 0):
            return sample.mean_growth, source
    reviewed = inputs.get('mean_growth_tokens')
    if reviewed is not None and reviewed > 0 and isfinite(reviewed):
        return reviewed, 'reviewed_' + inputs['growth_source']
    return None


def _with_guards(resolved: Resolution, fresh: int | None, inputs: ModelInputs | None) -> Resolution:
    if fresh is None:
        return resolved
    minimum = fresh + MIN_THRESHOLD
    if inputs is not None:
        cap, guard = inputs['window_tokens'] // 2, 'window_cap'
        step = inputs['price_step_tokens']
        if step is not None and step < cap:
            cap, guard = step, 'price_step'
        if cap < minimum:
            return resolved._replace(tokens=minimum, reason=resolved.reason + '/fresh_floor')
        if resolved.tokens > cap:
            return resolved._replace(tokens=cap, reason=resolved.reason + '/' + guard)
    if resolved.tokens < minimum:
        return resolved._replace(tokens=minimum, reason=resolved.reason + '/fresh_floor')
    return resolved


def resolve(session_or_character: Any, config: Mapping[str, Any]) -> Resolution:
    """Global disable, explicit target, measured default, then base fallback."""
    global_tokens = _tokens(config.get('context_rollover_tokens', DEFAULT_THRESHOLD))
    if global_tokens is None:
        global_tokens = DEFAULT_THRESHOLD
    if global_tokens <= 0:
        return Resolution(0, 'disabled')
    source = session_or_character if isinstance(session_or_character, Mapping) else {}
    ref = _character_ref(session_or_character)
    model = model_for_session(source)
    inputs = MODEL_TABLE.get(model) if model else None
    effort = effort_name(source.get('effective_effort'))
    fresh = _tokens(source.get('_mt_fresh_floor'))
    if fresh is not None and fresh <= 0:
        fresh = None

    override = _explicit_override(config, ref, model, effort, fresh)
    if override is not None:
        return _with_guards(Resolution(*override), fresh, inputs)

    if model and inputs is not None and fresh is not None:
        ratio = inputs.get('write_read_ratio')
        growth = _growth(ref, model, effort, inputs)
        if growth is not None and ratio is not None and ratio > 0 and isfinite(ratio):
            mean, growth_source = growth
            minimum = fresh + MIN_THRESHOLD
            # An oversized prefix already needs the full room exception.
            target = (minimum if minimum >= inputs['window_tokens'] else
                      continuity_target(fresh, mean, ratio, minimum))
            return _with_guards(Resolution(target, 'computed:' + growth_source, mean,
                                           growth_source), fresh, inputs)
    return _with_guards(Resolution(max(MIN_THRESHOLD, global_tokens), 'base'), fresh, inputs)


def threshold_with_reason(session_or_character: Any, config: Mapping[str, Any]) -> tuple[int, str]:
    result = resolve(session_or_character, config)
    return result.tokens, result.reason


def threshold_for(session_or_character: Any, config: Mapping[str, Any]) -> int:
    """Integer-only compatibility API shared by all rollover triggers."""
    return threshold_with_reason(session_or_character, config)[0]
