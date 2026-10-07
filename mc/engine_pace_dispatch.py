"""Thin MC-1071 adapter: read existing usage, resolve a configured alternate."""

from datetime import datetime, timezone
from typing import Callable

from mc import engine_pace, engine_selection
from mc.core import _log


def provider_usage(provider: str) -> dict:
    # Reuse the cached real readings, without calling the HTTP route or its
    # unrelated token-history aggregation. Never synthesize usage at 0%.
    from mc.blueprints import system_routes
    if provider == 'claude':
        entry = (system_routes._fetch_oauth_usage_limits() or {}).get('seven_day')
    elif provider == 'codex':
        entry = system_routes._fetch_codex_weekly_usage()
    else:
        entry = None
    return {provider: entry} if isinstance(entry, dict) else {}


def prepare(project_id: str, character: str, *, resume_id: str,
            provider_override: str, model_override: str, effort_override,
            config: dict, load_project: Callable,
            resolve_character: Callable) -> tuple[str, dict]:
    """Fresh named-character dispatches only; explicit engine pins stay pinned.

    Bad/missing usage or a stale alternate keeps the original ask. The
    existing dispatch then performs its normal strict resolution and gates.
    """
    if (resume_id or provider_override or model_override or effort_override is not None
            or config.get('engine_pace_enabled', True) is False
            or not config.get('engine_pace_alternates')):
        return character, {}
    try:
        project = load_project(project_id) or {}
        pp = project.get('project_path', '')
        meta, _ = resolve_character(pp, character, project=project)
        if not meta:
            return character, {}
        provider, _ = engine_selection.resolve_provider(
            config, project, character=meta.get('engine'), legacy_default='claude')
        effective = dict(meta, engine=dict(meta.get('engine') or {}, provider=provider))
        if not engine_pace.alternate_for(effective, config):
            return character, {}
        usage = provider_usage(provider)
        now = datetime.now(timezone.utc)
        if engine_pace.weekly_reading(effective, usage, now) is None:
            _log(f'[engine_pace] {provider} weekly usage missing, invalid or reset; keeping {engine_pace.character_ref(meta)}')
            return character, {}
        selected, reason = engine_pace.decide(effective, usage, now, config)
        if reason is None:
            return character, {}
        alternate, _ = resolve_character(pp, selected, project=project)
        if not alternate:
            _log(f'[engine_pace] alternate {selected!r} unavailable; keeping {engine_pace.character_ref(meta)}')
            return character, {}
        return selected, {'rerouted_from': engine_pace.character_ref(meta),
                          'reroute_reason': reason}
    except Exception as e:
        _log(f'[engine_pace] routing check failed; keeping {character!r}: {e}', flush=True)
        return character, {}


def disclosure(character: str, record: dict) -> str:
    return (f"[Pace routing: {record['rerouted_from']} -> {character}; "
            f"{record['reroute_reason']}] ")
