"""Model and effort picks for children launched from a typed Desk chat."""
from __future__ import annotations

from mc import agent_runtime, engine_selection, provider_preferences, state
from mc.desk_pieces import PieceError


def _provider(project: dict, character: dict | None) -> str:
    return engine_selection.resolve_provider(
        state.CONFIG, project, character=(character or {}).get('engine'),
        legacy_default='claude')[0]


def _choices(provider: str) -> dict[str, list[str]]:
    runtime = agent_runtime.get_runtime(provider)
    return {
        'models': [model for model, _ in runtime.model_choices()] if runtime.MODEL_SELECTION else [],
        'efforts': list(provider_preferences.effort_choices(provider)),
    }


def validate(body: dict, project: dict, character: dict | None) -> dict[str, str]:
    """Validate only explicit picks. Omitted keys stay out of the launch body."""
    if 'provider' in body:
        raise ValueError('Dispatch uses the hired agent’s own provider. Choose only model or effort.')
    picks = {}
    for key in ('model', 'effort'):
        if key not in body:
            continue
        value = body[key]
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f'Dispatch {key} must be non-empty text. Omit it to inherit the agent’s settings.')
        picks[key] = value.strip()
    if not picks:
        return picks

    provider = _provider(project, character)
    choices = _choices(provider)
    if 'model' in picks and picks['model'] not in choices['models']:
        hint = 'Choose a listed model for this provider.' if choices['models'] else 'Omit model for this provider.'
        raise ValueError(f'{provider} does not offer model {picks["model"]!r}. {hint}')
    if 'effort' in picks and picks['effort'] not in choices['efforts']:
        efforts = ', '.join(choices['efforts'])
        hint = f'Choose {efforts}, or omit effort.' if efforts else 'Omit effort for this provider.'
        raise ValueError(f'{provider} does not offer effort {picks["effort"]!r}. {hint}')
    return picks


def prompt_targets(project: dict, refs: list[str]) -> dict:
    """List character pins per hire and share each provider’s choices once."""
    from mc import desk_story

    targets: dict[str, dict] = {}
    choices: dict[str, dict] = {}
    for ref in refs:
        try:
            character = desk_story._agent(ref, project)
            provider = _provider(project, character)
        except (PieceError, engine_selection.EngineSelectionError) as exc:
            targets[ref] = {'unavailable': str(exc)}
            continue
        engine = (character or {}).get('engine') or {}
        targets[ref] = {'provider': provider, 'model': engine.get('model') or '', 'effort': engine.get('effort') or ''}
        if provider not in choices:
            choices[provider] = _choices(provider)
    return {'agents': targets, 'choices_by_provider': choices}


def launch_entry(project_id: str, session_id: str) -> dict:
    from mc.blueprints import agent_routes

    entry = agent_routes.agent_sessions.get(session_id)
    if entry:
        return entry
    rows = agent_routes._load_agent_log(project_id) or []
    return next((row for row in rows if isinstance(row, dict) and row.get('session_id') == session_id), {})


def launch_settings(entry: dict) -> dict[str, str]:
    """Read the selected launch settings. Older rows may lack the snapshot."""
    settings = {}
    for key in ('model', 'agent_model', 'pinned_model'):
        if key in entry:
            settings['model'] = entry[key] or ''
            break
    if 'requested_effort' in entry:
        settings['effort'] = entry['requested_effort'] or ''
    return settings
