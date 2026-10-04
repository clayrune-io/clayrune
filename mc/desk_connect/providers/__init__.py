"""Known-host adapters, one module each (docs/DESK_CONNECT_BY_URL_SPEC.md, slice 2).

`for_service(service_id)` is the only lookup: a registry service is connectable
exactly when it has a provider here AND the provider supports the method. A
recognised service with no provider (LinkedIn, YouTube, Google Drive/Photos) is
information only, however the registry words it. Adding a service is a new module
plus one line in `_MODULES`; nothing else imports a provider by name.
"""
from __future__ import annotations

from importlib import import_module

from mc.desk_connect.providers.base import Provider

# service id -> (module, class). Data, not behaviour: each module is imported on
# first use, so a broken provider cannot stop the others loading.
_MODULES = {
    'x': ('mc.desk_connect.providers.x', 'XProvider'),
    'higgsfield': ('mc.desk_connect.providers.higgsfield', 'HiggsfieldProvider'),
    'google_ai': ('mc.desk_connect.providers.google_ai', 'GoogleAiProvider'),
    'openai': ('mc.desk_connect.providers.openai_api', 'OpenAiProvider'),
}
_cache: dict[str, Provider] = {}


def for_service(service_id) -> Provider | None:
    if not isinstance(service_id, str) or service_id not in _MODULES:
        return None
    if service_id not in _cache:
        mod, cls = _MODULES[service_id]
        _cache[service_id] = getattr(import_module(mod), cls)()
    return _cache[service_id]


def service_ids() -> tuple:
    return tuple(_MODULES)
