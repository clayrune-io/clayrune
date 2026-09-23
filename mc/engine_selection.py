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
    # '' when `model` is an exact pin (or native default); one of 'best' /
    # 'balanced' / 'fast' when it was chosen by tracking that tier — the UI
    # label ("Opus 5.5 · tracking Best · from Global") reads model_source for
    # "from Global" and this for "tracking Best".
    tracking: str = ''
    effort: str = ''
    effort_source: str = ''


@dataclass(frozen=True)
class ModelResolution:
    model: str
    source: str
    tracking: str


_TIER_PREFIX = 'tier:'
TIERS = ('best', 'balanced', 'fast')


def classify_value(value: Any) -> tuple[str, str]:
    """Split a stored model/effort value into its kind and payload.

    Returns ('inherit', '') for empty/unset, ('tier', <name>) for a
    'tier:best'/'tier:balanced'/'tier:fast' tracking request, or ('pin',
    <value>) for anything else — including a bare runtime alias like 'opus',
    which already self-updates without needing the 'tier:' spelling.
    """
    text = _text(value)
    if not text:
        return 'inherit', ''
    if text.startswith(_TIER_PREFIX):
        name = text[len(_TIER_PREFIX):]
        if name in TIERS:
            return 'tier', name
    return 'pin', text


def _latest_for(runtime: Any, tier: str) -> str:
    """Tolerant call to `runtime.latest_for(tier)`.

    Registered runtimes are duck-typed, not required to subclass
    `AgentRuntime` (tests stub them; a real plugin runtime might too) — a
    runtime with no tier-tracking surface at all must resolve to the native
    default, not crash the caller. See tests/test_authorized_runtime_bridge.py.
    """
    fn = getattr(runtime, 'latest_for', None)
    return str(fn(tier)) if callable(fn) else ''


def is_stale_pin(provider: str, model: str) -> bool:
    """True when `model` is an EXACT pin older than `provider`'s current tier head.

    Detection only — never rewrites a stored pin (migration is a human call;
    see the model-hierarchy-simplification plan). False for anything that
    isn't a concrete pin sitting in a recognized tier family: empty/inherit,
    a 'tier:*' tracking value, a bare self-updating alias (e.g. 'opus'), an
    unknown provider, a runtime with no tier-tracking surface, or a model
    this runtime's tier_family() doesn't place.
    """
    kind, value = classify_value(model)
    if kind != 'pin':
        return False
    try:
        runtime = agent_runtime.get_runtime(provider)
    except KeyError:
        return False
    tier_aliases = getattr(runtime, 'TIER_ALIASES', None) or {}
    if value in tier_aliases.values():
        return False
    tier_family_fn = getattr(runtime, 'tier_family', None)
    family = tier_family_fn(value) if callable(tier_family_fn) else ''
    if not family:
        return False
    catalog_head_fn = getattr(runtime, 'catalog_head_for', None)
    head = catalog_head_fn(family) if callable(catalog_head_fn) else ''
    return bool(head) and head != value


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


def resolve_model_full(
    provider: str, config: Mapping[str, Any],
    project: Mapping[str, Any] | None = None, *, override: str | None = None,
) -> ModelResolution:
    """Resolve a model (or the tier it tracks) without crossing providers.

    `override=None` inherits through project > global; an unset/empty GLOBAL
    value resolves to the CLI native default ('' model, source 'native') —
    an upgraded install that never saw the first-run model question must not
    start sending `--model opus` (model-hierarchy-simplification, 2026-09-22).
    An explicit `tier:*` global or project value still tracks that tier.
    `override=''` is an explicit ask for the native default and never
    inherits. A value is rejected only when it is a KNOWN id belonging to a
    DIFFERENT provider (model_provider_mismatch) — catalog membership is not
    a custom-model ban, so an unrecognized-but-not-foreign string
    (custom/future id) is always accepted. An explicit mismatch raises (the
    caller asked for something incoherent); an inherited one is silently
    omitted, never translated — legacy config was never validated at write
    time.
    """
    runtime = agent_runtime.get_runtime(provider)

    def _tier(name: str, source: str) -> ModelResolution:
        return ModelResolution(_latest_for(runtime, name), source, name)

    def _pin(value: str, source: str, *, explicit: bool) -> ModelResolution:
        owner = model_provider_mismatch(provider, value)
        if owner:
            if explicit:
                raise EngineSelectionError(
                    f"Model '{value}' belongs to provider '{owner}', not '{provider}'. "
                    'Choose a matching provider/model pair.')
            return ModelResolution('', 'native', '')
        return ModelResolution(value, source, '')

    if override is not None:
        kind, value = classify_value(override)
        if kind == 'inherit':
            return ModelResolution('', 'native', '')
        if kind == 'tier':
            return _tier(value, 'explicit')
        return _pin(value, 'explicit', explicit=True)

    kind, value = classify_value((project or {}).get('agent_model'))
    source = 'project'
    if kind == 'inherit':
        kind, value = classify_value(config.get('agent_model'))
        source = 'global'
        if kind == 'inherit':
            return ModelResolution('', 'native', '')
    if kind == 'tier':
        return _tier(value, source)
    return _pin(value, source, explicit=False)


def resolve_model(
    provider: str, config: Mapping[str, Any],
    project: Mapping[str, Any] | None = None, *, override: str | None = None,
) -> tuple[str, str]:
    """Back-compat 2-tuple view of resolve_model_full(): (model, source)."""
    resolved = resolve_model_full(provider, config, project, override=override)
    return resolved.model, resolved.source


def resolve_effort(
    config: Mapping[str, Any], project: Mapping[str, Any] | None = None,
    *, override: str | None = None,
) -> tuple[str, str]:
    """Resolve reasoning effort through project > global.

    No tier concept here — effort levels (low/medium/high/xhigh/max) are
    absolute, not a hierarchy to track the head of. `override=None` inherits;
    an explicit '' selects the native default and never inherits, matching
    resolve_model_full's own explicit-empty contract.
    """
    if override is not None:
        text = _text(override)
        return text, ('explicit' if text else 'native')
    project_value = _text((project or {}).get('agent_effort'))
    if project_value:
        return project_value, 'project'
    global_value = _text(config.get('agent_effort'))
    if global_value:
        return global_value, 'global'
    return '', 'native'


def resolve_engine(
    config: Mapping[str, Any], project: Mapping[str, Any] | None = None,
    *, provider_override: str = '', model_override: str | None = None,
    effort_override: str | None = None,
    character: Mapping[str, Any] | None = None, legacy_default: str = '',
) -> ResolvedEngine:
    provider, provider_source = resolve_provider(
        config, project, override=provider_override, character=character,
        legacy_default=legacy_default)
    character_model = _text((character or {}).get('model'))
    use_character_model = model_override is None and bool(character_model)
    resolved_model = resolve_model_full(
        provider, config, project,
        override=character_model if use_character_model else model_override)
    model_source = 'character' if use_character_model else resolved_model.source
    character_effort = _text((character or {}).get('effort'))
    use_character_effort = effort_override is None and bool(character_effort)
    effort, effort_source = resolve_effort(
        config, project,
        override=character_effort if use_character_effort else effort_override)
    if use_character_effort:
        effort_source = 'character'
    return ResolvedEngine(provider, resolved_model.model, provider_source, model_source,
                          tracking=resolved_model.tracking, effort=effort,
                          effort_source=effort_source)
