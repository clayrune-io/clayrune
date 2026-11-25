"""Resolve the token rollover limit shared by all rollover paths (MC-1072)."""
from collections.abc import Mapping
from functools import lru_cache
from math import sqrt
from typing import Any

from mc.core import _log
from mc.rollover_model_table import ModelInputs, model_inputs

DEFAULT_THRESHOLD = 200_000
MIN_THRESHOLD = 60_000


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


def inputs_for_session(session: Any) -> ModelInputs | None:
    """Prefer the observed model; a present empty model means native default."""
    if not isinstance(session, Mapping):
        return None
    model = session.get('observed_model') or (
        session['model'] if 'model' in session else session.get('agent_model'))
    return model_inputs(model)


@lru_cache(maxsize=256)
def _log_ignored_pin(ref: str, pin: int) -> None:
    """Warn once per character/pin, rather than at every tool boundary."""
    _log(f"[rollover-threshold] ignoring {ref} pin {pin}: below fresh floor + 60000")


def threshold_with_reason(session_or_character: Any, config: Mapping[str, Any]) -> tuple[int, str]:
    """Resolve the legacy limit or the reviewed MC-1178 cost optimum.

    The global nonpositive setting disables all token rollover. With a known
    Claude model and fresh floor, the 50% window/price-step/valid-pin upper
    bound wins over the global lower bound, but never drops below fresh
    floor + 60k: a cap that close to the floor would re-roll every few calls,
    the thrash MC-1178 exists to stop. An unknown model keeps the legacy
    pin/global limit only when it leaves the same room above a known floor.
    """
    global_tokens = _tokens(config.get('context_rollover_tokens', DEFAULT_THRESHOLD))
    if global_tokens is None:
        global_tokens = DEFAULT_THRESHOLD
    if global_tokens <= 0:
        return 0, 'global'
    fallback = max(MIN_THRESHOLD, global_tokens)
    overrides = config.get('context_rollover_by_character')
    ref = _character_ref(session_or_character)
    pin = _tokens(overrides.get(ref)) if ref and isinstance(overrides, Mapping) else None
    inputs = inputs_for_session(session_or_character)
    floor = (_tokens(session_or_character.get('_mt_fresh_floor'))
             if isinstance(session_or_character, Mapping) else None)
    if floor is not None and floor < 0:
        floor = None
    if inputs is None or floor is None:
        legacy = (max(MIN_THRESHOLD, pin), 'pin') if pin is not None else (fallback, 'global')
        if floor is None:
            return legacy
        lower = floor + MIN_THRESHOLD
        if pin is not None and pin < lower:
            _log_ignored_pin(ref, pin)
            return max(fallback, lower), 'pin_ignored_below_floor'
        return legacy if legacy[0] >= lower else (lower, 'fresh_floor')

    lower = max(fallback, floor + MIN_THRESHOLD)
    upper = inputs['window_tokens'] // 2
    cap_reason = 'window_cap'
    step = inputs['price_step_tokens']
    if step is not None and step < upper:
        upper, cap_reason = step, 'price_step'
    ignored_pin = pin is not None and pin < floor + MIN_THRESHOLD
    if pin is not None and ignored_pin:
        _log_ignored_pin(ref, pin)
    elif pin is not None and pin < upper:
        upper, cap_reason = pin, 'pin'
    if upper < floor + MIN_THRESHOLD:
        upper, cap_reason = floor + MIN_THRESHOLD, 'fresh_floor'
    optimum = floor + sqrt(2 * floor * inputs['write_read_ratio'] * inputs['mean_growth_tokens'])
    candidate = max(lower, int(optimum))
    if candidate > upper:
        return upper, cap_reason
    return candidate, 'pin_ignored_below_floor' if ignored_pin else 'cost_optimal'


def threshold_for(session_or_character: Any, config: Mapping[str, Any]) -> int:
    """Integer-only compatibility API shared by all rollover triggers."""
    return threshold_with_reason(session_or_character, config)[0]
