"""Resolve the token rollover limit shared by all rollover paths (MC-1072)."""
from collections.abc import Mapping
from typing import Any

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


def threshold_for(session_or_character: Any, config: Mapping[str, Any]) -> int:
    """Character override, else global/default; active limits are at least 60k.

    Invalid overrides fall back to the global limit. The existing nonpositive
    global setting still disables token rollover; character overrides always
    clamp to the floor. No config values are rewritten.
    """
    global_tokens = _tokens(config.get('context_rollover_tokens', DEFAULT_THRESHOLD))
    if global_tokens is None:
        global_tokens = DEFAULT_THRESHOLD
    fallback = max(MIN_THRESHOLD, global_tokens) if global_tokens > 0 else 0
    overrides = config.get('context_rollover_by_character')
    if not isinstance(overrides, Mapping):
        return fallback
    ref = _character_ref(session_or_character)
    override = _tokens(overrides.get(ref)) if ref else None
    return max(MIN_THRESHOLD, override) if override is not None else fallback
