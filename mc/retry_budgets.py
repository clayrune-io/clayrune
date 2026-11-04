"""One wait-only config block; no active-turn or session execution deadline."""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

CONFIG_KEY = 'unattended_retry'
# Minutes, converted only at the vendor boundary.
DEFAULTS = {'max_wait': 30, 'max_usage_limit_wait': 120, 'usage_limit_recheck': 5}
CLAUDE_ENV = {
    'max_wait': 'CLAUDE_CODE_RETRY_WATCHDOG_MAX_WAIT_MS',
    'max_usage_limit_wait': 'CLAUDE_CODE_RETRY_WATCHDOG_MAX_USAGE_LIMIT_WAIT_MS',
    'usage_limit_recheck': 'CLAUDE_CODE_RETRY_WATCHDOG_USAGE_LIMIT_RECHECK_MS',
}


def validate_block(value: Any) -> Dict[str, int]:
    if not isinstance(value, dict):
        raise ValueError(f'{CONFIG_KEY} must be an object of durations in minutes')
    unknown = set(value) - set(DEFAULTS)
    if unknown:
        raise ValueError(f'unknown {CONFIG_KEY} field(s): {", ".join(sorted(unknown))}')
    result = dict(DEFAULTS)
    for key, minutes in value.items():
        # Preserve exact milliseconds through JavaScript's safe-integer range.
        if (type(minutes) is not int or minutes <= 0
                or minutes > (2**53 - 1) // 60000):
            raise ValueError(f'{CONFIG_KEY}.{key} must be positive whole minutes')
        if key == 'usage_limit_recheck' and minutes > 180:
            raise ValueError(f'{CONFIG_KEY}.{key} must be between 1 and 180 minutes')
        result[key] = minutes
    return result


def durations(config: Mapping[str, Any]) -> Dict[str, int]:
    try:
        return validate_block(config.get(CONFIG_KEY, {}))
    except ValueError:
        # Hand-edited old config must not create an unlimited wait.
        return dict(DEFAULTS)


def apply_env(engine: str, trigger: Optional[str], env: Dict[str, str],
              config: Mapping[str, Any]) -> Dict[str, str]:
    from mc.launch_marker import UNATTENDED_TRIGGER_TYPES
    if trigger not in UNATTENDED_TRIGGER_TYPES:
        return env
    if engine == 'claude':
        limits = durations(config)
        for field, name in CLAUDE_ENV.items():
            env[name] = str(limits[field] * 60000)
    elif engine == 'qwen':
        if not env.get('QWEN_CODE_UNATTENDED_RETRY', '').strip():
            env['QWEN_CODE_UNATTENDED_RETRY'] = '1'
    return env
