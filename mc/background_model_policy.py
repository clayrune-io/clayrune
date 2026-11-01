"""CR-owned lightweight choices for internal tasks, independent of chat pins."""
from __future__ import annotations

from mc import agent_runtime


def automatic_model(provider: str, *, default: str = '') -> str:
    """Keep explicit Claude task policies; otherwise prefer a known fast tier.

    An empty result means the runtime has no verified lightweight mapping.
    It is a native fallback, never a claim about that provider's cost.
    """
    if provider == 'claude' and default:
        return default
    try:
        runtime = agent_runtime.get_runtime(provider)
        latest = getattr(runtime, 'latest_for', None)
        if not getattr(runtime, 'MODEL_SELECTION', True) or not callable(latest):
            return ''
        model = latest('fast')
    except Exception as exc:
        # An unresolvable runtime must not break a background write
        # (Scribe/condense); '' lets the provider pick its native model.
        from mc.core import _log
        _log(f'[providers] automatic background model failed for {provider}: {exc}')
        return ''
    return model if isinstance(model, str) else ''
