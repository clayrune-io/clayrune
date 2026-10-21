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
    runtime = agent_runtime.get_runtime(provider)
    latest = getattr(runtime, 'latest_for', None)
    if not getattr(runtime, 'MODEL_SELECTION', True) or not callable(latest):
        return ''
    model = latest('fast')
    return model if isinstance(model, str) else ''
