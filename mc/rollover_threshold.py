"""Resolve the token rollover limit shared by all rollover paths (MC-1072)."""
import re
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
def _log_ignored_override(
    selectors: tuple[tuple[str, str], ...], tokens: int, why: str,
) -> None:
    scope = ', '.join(f'{key}={value}' for key, value in selectors)
    _log(f'[rollover-threshold] ignoring {scope} override {tokens}: {why}')


@lru_cache(maxsize=256)
def _log_unusable_override(entry: str, why: str) -> None:
    _log(f'[rollover-threshold] ignoring override entry {entry}: {why}')


def _unusable(entry: Any) -> tuple[str, bool] | None:
    """(why, skip) for an override entry that cannot do what it was written to
    do, else None. These used to be passed over without a word: a misspelled
    key, or `{"model": "fable"}`, which no chat reporting a full model id ever
    matches (2026-11-25 review, F10). `skip` is False for the one case that was
    never skipped and still is not: a bare model word can still equal a chat's
    requested model before its first call reports the real id."""
    if not isinstance(entry, Mapping):
        return 'not an object', True
    unknown = sorted(str(key) for key in entry.keys() - (_SELECTORS.keys() | {'tokens'}))
    if unknown:
        return (f"unknown key {', '.join(unknown)} "
                '(expected model, character, effort, tokens)'), True
    if _tokens(entry.get('tokens')) is None:
        return 'tokens is not an integer', True
    if not entry.keys() & _SELECTORS.keys():
        return 'no model, character or effort to match', True
    if 'effort' in entry and effort_name(entry['effort']) is None:
        return f"effort {entry['effort']!r} is not one of low, medium, high, xhigh, max", True
    if 'character' in entry and not (isinstance(entry['character'], str) and entry['character']):
        return 'character is not a scope:name reference', True
    if 'model' in entry:
        model = canonical_model_id(entry['model'])
        if not model:
            return 'model is empty', True
        if re.fullmatch(r'[a-z]+', model):
            return (f"model {entry['model']!r} is not a full model id or one of the "
                    'aliases haiku, sonnet, opus'), False
    return None


def _explicit_override(
    config: Mapping[str, Any], character: str, model: str | None,
    effort: str | None, fresh: int | None, base: int | None,
) -> tuple[int, str] | None:
    """`base` is set while a floor is still owed (see `_floor_owed`)."""
    values = {'model': model, 'character': character, 'effort': effort}
    entries = config.get('context_rollover_overrides')
    entries = list(entries) if isinstance(entries, list) else []
    legacy = config.get('context_rollover_by_character')
    if character and isinstance(legacy, Mapping) and character in legacy:
        entries.append({'character': character, 'tokens': legacy[character]})
    matches: list[tuple[tuple[tuple[str, str], ...], int]] = []
    for entry in entries:
        unusable = _unusable(entry)
        if unusable is not None:
            _log_unusable_override(repr(entry)[:200], unusable[0])
            if unusable[1]:
                continue
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
        # With no verified floor the working room a target leaves is unknown,
        # so one under the base cannot be trusted: after a restart the legacy
        # 100k pin came back over an 89k start (2026-11-25 review, F1).
        if base is not None and tokens < base:
            _log_ignored_override(
                selected, tokens, f'below the base {base} with no verified fresh floor')
            continue
        if fresh is not None and tokens < fresh + MIN_THRESHOLD:
            _log_ignored_override(selected, tokens, 'below fresh floor + 60000')
            continue
        return max(MIN_THRESHOLD, tokens), 'override:' + '+'.join(key for key, _ in selected)
    return None


def _floor_owed(source: Mapping[str, Any], fresh: int | None) -> bool:
    """True while a Claude chat has no verified floor yet. Only the Claude
    readers measure one (mc/rollover_fresh_floor.py); another provider never
    will, so its targets keep applying as written (MC-1072)."""
    provider = source.get('provider')
    claude = not isinstance(provider, str) or provider.strip().lower() in ('', 'claude')
    return fresh is None and claude


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
    """Window and price-step caps, then the 60k of room over a known floor.
    The caps are facts about the model and apply with or without a floor: an
    unknown floor used to return before them, so a 900k target stood on a 1M
    window and the 200k base was the whole window of a 200k model
    (2026-11-25 review, F8). The room rule needs the floor and waits for it."""
    minimum = None if fresh is None else fresh + MIN_THRESHOLD
    if inputs is not None:
        cap, guard = inputs['window_tokens'] // 2, 'window_cap'
        step = inputs['price_step_tokens']
        if step is not None and step < cap:
            cap, guard = step, 'price_step'
        if minimum is not None and cap < minimum:
            return resolved._replace(tokens=minimum, reason=resolved.reason + '/fresh_floor')
        if resolved.tokens > cap:
            return resolved._replace(tokens=cap, reason=resolved.reason + '/' + guard)
    if minimum is not None and resolved.tokens < minimum:
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

    base = max(MIN_THRESHOLD, global_tokens)
    override = _explicit_override(
        config, ref, model, effort, fresh, base if _floor_owed(source, fresh) else None)
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
    return _with_guards(Resolution(base, 'base'), fresh, inputs)


def threshold_with_reason(session_or_character: Any, config: Mapping[str, Any]) -> tuple[int, str]:
    result = resolve(session_or_character, config)
    return result.tokens, result.reason


def threshold_for(session_or_character: Any, config: Mapping[str, Any]) -> int:
    """Integer-only compatibility API shared by all rollover triggers."""
    return threshold_with_reason(session_or_character, config)[0]
