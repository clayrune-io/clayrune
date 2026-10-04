"""The Method step's answer for what the Service step was given (no network, no model).

`inspect(text)` resolves a name or an address (resolve.py), looks the exact host up
in the registry and returns the rows the screen shows. Recognition is not support:
every row says what Clayrune can really do today, and only `selectable` rows can be
chosen. A row is selectable when it is "Save for agents", or when the registry
calls it `available` AND the service has a provider that supports that method
(`providers/`): a service the registry knows and no provider connects (LinkedIn,
YouTube, Google Drive/Photos) stays information only, however it is worded.

A selectable provider row carries what the Details step needs: the provider's
`fields` (specs only, with `present` for a vault entry that already exists so it is
not asked twice), its `guide` steps and its one-line `summary`. Reading which vault
entries exist is metadata; nothing is decrypted and nothing is written here.
"""
from __future__ import annotations

from mc import secrets_store as _vault
from mc.desk_connect import providers, registry, resolve


def is_connectable(service: dict | None, method) -> bool:
    """True when `method` is an `available` registry option of this service AND its
    provider supports it. The one test the Method step and the commit share."""
    if not service or not isinstance(method, str):
        return False
    prov = providers.for_service(service['id'])
    if prov is None or not prov.supports(method):
        return False
    return any(o['method'] == method and o['support'] == 'available' for o in service['options'])


def _vault_names() -> set:
    try:
        return {s['name'] for s in _vault.list_secrets()}
    except _vault.SecretsError:
        return set()


def _with_connectors(svc: dict | None, rows: list[dict]) -> list[dict]:
    if not svc:
        return rows
    prov = providers.for_service(svc['id'])
    names = None
    for row in rows:
        if row['method'] == 'save_for_agents' or not is_connectable(svc, row['method']):
            continue
        if names is None:
            names = _vault_names()
        row['selectable'] = True
        row['connector'] = {'service': svc['id'], 'summary': prov.summaries[row['method']],
                            'guide': prov.guide(row['method']), 'fields': prov.fields(row['method'], names),
                            'signs_in': row['method'] in prov.signs_in}
    return rows


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
        'options': _with_connectors(svc, registry.options_for(svc)),
    }
