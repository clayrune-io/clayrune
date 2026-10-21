"""Provider-scoped preferences, compatibility views and field-level updates.

Reading never migrates data or executes a CLI. The first scoped write retains
legacy fields for rollback and materializes their current meaning for each
registered provider. An empty scoped map is authoritative (reset to inherit).
"""
from __future__ import annotations

from copy import deepcopy
from functools import wraps
import hashlib
import json
from pathlib import Path
import re
import threading
from typing import Any, Mapping

from mc import agent_runtime

FIELDS = frozenset({'model', 'effort', 'background_model', 'scribe_model',
                    'condense_model', 'distiller_model', 'auto_model_classifier_model'})
ROLE_FIELDS = ('scribe_model', 'condense_model', 'distiller_model', 'auto_model_classifier_model')
_MODEL = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:/@+\-]{0,199}\Z')
_write_lock = threading.RLock()
BACKUP_ROOT = Path.home() / '.clayrune' / 'provider-preferences-backups'
SCHEMA_VERSION = 1


def backup_legacy_preferences(record: Mapping[str, Any], *, identity: str,
                              project: bool = False) -> Path | None:
    """Save only engine preferences outside the repository before cutover.

    An identical snapshot is stored once. Failed backup aborts the caller's
    write. Legacy fields in the live record also remain available for rollback.
    """
    key = 'provider_overrides' if project else 'provider_defaults'
    if isinstance(record.get(key), dict):
        return None
    keys = ('agent_model', 'agent_effort', 'provider', 'default_provider',
            'auto_model_enabled', *ROLE_FIELDS)
    snapshot = {'schema_version': SCHEMA_VERSION, 'scope': 'project' if project else 'global',
                'identity': identity, 'preferences': {key: record[key] for key in keys if key in record}}
    digest = hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()[:16]
    name = re.sub(r'[^A-Za-z0-9_-]', '_', identity)[:80]
    target = BACKUP_ROOT / f'{name}-{digest}.json'
    if not target.exists():
        from mc.atomic_json import write_json_atomic
        BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
        write_json_atomic(target, snapshot, indent=2, ensure_ascii=False)
    return target


def serialized_write(fn):
    """Serialize preference read/merge/persist with all other config writes."""
    @wraps(fn)
    def wrapped(*args, **kwargs):
        with _write_lock:
            return fn(*args, **kwargs)
    return wrapped


def effort_choices(provider: str) -> tuple[str, ...]:
    runtime = agent_runtime.get_runtime(provider)
    return tuple(getattr(runtime, 'EFFORT_LEVELS', ()))


def runtime_metadata(runtime) -> dict:
    """Adapter-owned picker capabilities, without installation/auth probes."""
    latest = getattr(runtime, 'latest_for', None)
    tiers = {tier: latest(tier) for tier in ('best', 'balanced', 'fast')} if callable(latest) else {}
    return {'efforts': list(getattr(runtime, 'EFFORT_LEVELS', ())),
            'model_selection': bool(getattr(runtime, 'MODEL_SELECTION', True)),
            'tiers': {tier: model for tier, model in tiers.items() if model}}


def resolve_tracking_request(runtime, requested: str) -> str:
    """Resolve an explicit saved tier at launch; exact/native snapshots pass through."""
    if not requested.startswith('tier:'):
        return requested
    tier = requested[5:]
    latest = getattr(runtime, 'latest_for', None)
    model = latest(tier) if tier in ('best', 'balanced', 'fast') and callable(latest) else ''
    if not isinstance(model, str) or not model:
        raise ValueError(f'Tracking tier {tier!r} is unavailable for this provider')
    return model


def _compatible(provider: str, value: str) -> bool:
    from mc.engine_selection import model_provider_mismatch
    return not model_provider_mismatch(provider, value)


def scoped_view(record: Mapping[str, Any], *, project: bool = False) -> dict:
    """A non-mutating migration preview, preserving legacy terminal defaults."""
    key = 'provider_overrides' if project else 'provider_defaults'
    current = record.get(key)
    if isinstance(current, dict):
        return deepcopy(current)
    result = {}
    for runtime in agent_runtime.available_runtimes():
        entry = {}
        for field in ('model', 'effort'):
            value = record.get('agent_' + field)
            if isinstance(value, str) and value.strip():
                value = value.strip()
                entry[field] = (value if field == 'effort' or value.startswith('tier:')
                                or _compatible(runtime.name, value) else '')
        if not project:
            for field in ROLE_FIELDS:
                value = record.get(field)
                if isinstance(value, str) and value.strip():
                    entry[field] = _legacy_background_model(record, runtime.name, field)
        if entry:
            result[runtime.name] = entry
    return result


def selected_field(config: Mapping[str, Any], project: Mapping[str, Any] | None,
                   provider: str, field: str) -> tuple[bool, str, str, bool]:
    """(present, value, source, scoped). Explicit empty stops inheritance."""
    for source, record, key in (('project', project or {}, 'provider_overrides'),
                                ('global', config, 'provider_defaults')):
        entries = record.get(key)
        if isinstance(entries, dict):
            entry = entries.get(provider) or {}
            if field in entry:
                return True, entry[field], source, True
            continue
        legacy = record.get('agent_' + field)
        if isinstance(legacy, str) and legacy.strip():
            return True, legacy.strip(), source, False
    return False, '', 'native', False


def patch_preferences(record: Mapping[str, Any], patch: Any,
                      *, project: bool = False) -> dict:
    """Merge only named fields. Null field removes an override; '' is native.

    Null provider resets its entire entry. Unknown runtime/field, foreign model
    and unsupported new effort requests fail before the caller persists anything.
    Legacy values are retained verbatim in the source record for rollback.
    """
    if not isinstance(patch, dict):
        raise ValueError('Provider preferences must be an object')
    result = scoped_view(record, project=project)
    known = {r.name for r in agent_runtime.available_runtimes()}
    for provider, changes in patch.items():
        if provider not in known:
            raise ValueError(f'Unknown provider {provider!r}')
        if changes is None:
            result.pop(provider, None)
            continue
        if not isinstance(changes, dict):
            raise ValueError(f'{provider}: preferences must be an object or null')
        entry = dict(result.get(provider) or {})
        for field, value in changes.items():
            if field not in FIELDS or (project and field not in ('model', 'effort')):
                raise ValueError(f'{provider}: unknown preference {field!r}')
            if value is None:
                entry.pop(field, None)
                continue
            if not isinstance(value, str):
                raise ValueError(f'{provider} {field}: expected a string or null')
            value = value.strip()
            if field == 'effort':
                if value and value not in effort_choices(provider):
                    raise ValueError(f'{provider}: effort {value!r} is not supported')
            elif value:
                if not getattr(agent_runtime.get_runtime(provider), 'MODEL_SELECTION', True):
                    raise ValueError(f'{provider}: model selection is managed by the provider')
                if not _MODEL.fullmatch(value):
                    raise ValueError(f'{provider}: invalid model selection')
                if value.startswith('tier:'):
                    tier = value[5:]
                    runtime = agent_runtime.get_runtime(provider)
                    if tier not in ('best', 'balanced', 'fast') or not runtime.latest_for(tier):
                        raise ValueError(f'{provider}: tier {tier!r} is not available')
                elif not _compatible(provider, value):
                    raise ValueError(f'{provider}: model {value!r} belongs to another provider')
            entry[field] = value
        if entry:
            result[provider] = entry
        else:
            result.pop(provider, None)
    return result


def background_model(config: Mapping[str, Any], provider: str, role: str,
                     *, default: str = '') -> str:
    """Role pin > provider background preference > compatible legacy role."""
    entry = (config.get('provider_defaults') or {}).get(provider) or {}
    for field in (role, 'background_model'):
        if field in entry:
            value = entry[field]
            if value.startswith('tier:'):
                return agent_runtime.get_runtime(provider).latest_for(value[5:])
            return value
    if isinstance(config.get('provider_defaults'), dict):
        return default if provider == 'claude' else ''
    return _legacy_background_model(config, provider, role, default=default)


def _legacy_background_model(config: Mapping[str, Any], provider: str, role: str,
                             *, default: str = '') -> str:
    """Keep the stricter historic compatibility rule for unscoped role pins."""
    value = str(config.get(role) or '').strip()
    if provider == 'claude':
        return value or default
    if not value or value.lower() in ('haiku', 'sonnet', 'opus') or value.lower().startswith('claude-'):
        return ''
    try:
        supported = getattr(agent_runtime.get_runtime(provider), 'model_supported', None)
        return value if callable(supported) and supported(value) else ''
    except Exception as exc:
        from mc.core import _log
        _log(f'[providers] legacy background model compatibility failed for {provider}: {exc}')
        return ''
