"""The Method step's answer for a pasted address (no network, no model).

`inspect(raw_url)` validates the address (url_check), looks the exact host up in
the registry and returns the rows the screen shows. Recognition is not support:
every row says what Clayrune can really do today, and only `selectable` rows can be
chosen (in this version, "Save for agents").
"""
from __future__ import annotations

from mc.desk_connect import registry, url_check


def inspect(raw_url, own_hosts=()) -> dict:
    """Returns `{url, host, path, service, options}`; raises url_check.UrlError.
    `service` is `{id, label}` for a known host, else None ("Not recognised yet")."""
    checked = url_check.check_url(raw_url, own_hosts=own_hosts)
    svc = registry.lookup(checked['host'])
    return {
        **checked,
        'service': {'id': svc['id'], 'label': svc['label']} if svc else None,
        'options': registry.options_for(svc),
    }
