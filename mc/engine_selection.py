"""Provider/model resolution without execution, account probes or fallback.

Keep explicit selections separate from legacy inherited model defaults. The
former must never silently change; the latter may be Claude-shaped on an older
install and must not leak into another provider's command line.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from mc import agent_runtime


class EngineSelectionError(ValueError):
    """An engine cannot be selected without changing the user's intent."""


@dataclass(frozen=True)
class ResolvedEngine:
    provider: str
    model: str
    provider_source: str
    model_source: str


def _text(value: Any) -> str:
    if value is None:
        return ''
    if not isinstance(value, str):
        raise EngineSelectionError('Provider and model selections must be strings')
    return value.strip()


def resolve_provider(
    config: Mapping[str, Any], project: Mapping[str, Any] | None = None,
    *, override: str = '', character: Mapping[str, Any] | None = None,
    legacy_default: str = '',
) -> tuple[str, str]:
    """Return (provider, source). Unconfigured/unknown is an error by default.

    Existing call sites can explicitly opt into their pre-onboarding legacy
    default while setup migration is in progress. Never use this to recover
    an existing conversation: its stored owner must be passed as override.
    """
    candidates = (
        ('explicit', override),
        ('character', (character or {}).get('provider')),
        ('project', (project or {}).get('provider')),
        ('global', config.get('default_provider')),
        ('legacy', legacy_default),
    )
    for source, value in candidates:
        name = _text(value).lower()
        if not name:
            continue
        try:
            agent_runtime.get_runtime(name)
        except KeyError as exc:
            raise EngineSelectionError(
                f"Unknown provider '{name}'; choose a configured provider. "
                'No fallback was attempted.') from exc
        return name, source
    raise EngineSelectionError('No provider selected; choose one in Settings')


def model_provider_mismatch(provider: str, model: str) -> str:
    """Known foreign owner, or empty for accepted/custom/unknown model IDs.

    Runtime support takes precedence: multi-vendor runtimes can legitimately
    accept a model from another vendor. Catalogs are not a custom-model ban.
    """
    try:
        runtime = agent_runtime.get_runtime(provider)
    except KeyError:
        # Provider resolution owns the unknown-provider error. This helper is
        # only a cross-provider model check and stays non-throwing here.
        return ''
    # Claude's native tier aliases overlap multi-provider catalogs (e.g.
    # Aider). Catalog membership alone must not reassign their ownership.
    if provider == 'claude' and model in ('haiku', 'sonnet', 'opus'):
        return ''
    if runtime.model_supported(model):
        return ''
    for other in agent_runtime.available_runtimes():
        if other.name != provider and any(m == model for m, _ in other.model_choices()):
            return other.name
    for prefix, owner in (('gemini-', 'gemini'), ('claude-', 'claude')):
        if model.startswith(prefix) and owner != provider:
            return owner
    return ''


def resolve_model(
    provider: str, config: Mapping[str, Any],
    project: Mapping[str, Any] | None = None, *, override: str | None = None,
) -> tuple[str, str]:
    """Resolve a model without crossing providers.

    None means inherit; explicit '' means use the native provider default.
    Unknown custom explicit IDs remain allowed, known foreign IDs are errors.
    Incompatible *legacy inherited* defaults are omitted, never translated.
    """
    runtime = agent_runtime.get_runtime(provider)
    if override is not None:
        model = _text(override)
        owner = model_provider_mismatch(provider, model) if model else ''
        if owner:
            raise EngineSelectionError(
                f"Model '{model}' belongs to provider '{owner}', not '{provider}'. "
                'Choose a matching provider/model pair.')
        return model, 'explicit' if model else 'native'
    inherited = _text((project or {}).get('agent_model'))
    source = 'project'
    if not inherited:
        inherited = _text(config.get('agent_model'))
        source = 'global'
    if inherited and (runtime.model_supported(inherited) or
                      (provider == 'claude' and inherited in ('haiku', 'sonnet', 'opus'))):
        return inherited, source
    return '', 'native'


def resolve_engine(
    config: Mapping[str, Any], project: Mapping[str, Any] | None = None,
    *, provider_override: str = '', model_override: str | None = None,
    character: Mapping[str, Any] | None = None, legacy_default: str = '',
) -> ResolvedEngine:
    provider, provider_source = resolve_provider(
        config, project, override=provider_override, character=character,
        legacy_default=legacy_default)
    character_model = _text((character or {}).get('model'))
    use_character_model = model_override is None and bool(character_model)
    model, model_source = resolve_model(
        provider, config, project,
        override=character_model if use_character_model else model_override)
    if use_character_model:
        model_source = 'character'
    return ResolvedEngine(provider, model, provider_source, model_source)
