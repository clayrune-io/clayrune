"""Keep setup's unrelated choices when its optional model selection is refused."""
from typing import Any, Mapping

from mc import provider_preferences


_SETUP_FIELDS = frozenset({'setup_save', 'passcode', 'setup_completed',
                          'backup_dest_dir', 'backup_schedule', 'default_provider',
                          'provider_defaults'})


def prepare_preferences(config: Mapping[str, Any], data: dict) -> dict[str, str]:
    """Validate normally; only setup's model-only patch can fail independently.

    The caller must enforce the usual human gate first and validate/persist all
    remaining fields normally. Refused models are never applied or migrated.
    """
    if 'provider_defaults' not in data:
        return {}
    patch = data['provider_defaults']
    try:
        data['provider_defaults'] = provider_preferences.patch_preferences(config, patch)
    except ValueError as exc:
        model_only = (isinstance(patch, dict) and bool(patch)
                      and all(isinstance(fields, dict) and set(fields) == {'model'}
                              for fields in patch.values()))
        if data.get('setup_save') is not True or not set(data) <= _SETUP_FIELDS or not model_only:
            raise
        del data['provider_defaults']
        return {'provider_defaults': str(exc)}
    return {}
