"""The Method step's answer for what the Service step was given (no network, no model).

`inspect(text)` resolves a name or an address (resolve.py), looks the exact host up
in the registry and returns the rows the screen shows. Recognition is not support:
every row says what Clayrune can really do today, and only `selectable` rows can be
chosen (in this version, "Save for agents").
"""
from __future__ import annotations

from mc.desk_connect import registry, resolve


def inspect(raw, own_hosts=()) -> dict:
    """Returns `{url, host, path, input_kind, service, options}`; raises
    url_check.UrlError (resolve.UnknownNameError for a name nobody knows).
    `service` is `{id, label}` for a known host or name, else None ("Not recognised
    yet")."""
    got = resolve.resolve(raw, own_hosts=own_hosts)
    svc = got.pop('service') or registry.lookup(got['host'])
    return {
        **got,
        'service': {'id': svc['id'], 'label': svc['label']} if svc else None,
        'options': registry.options_for(svc),
    }
