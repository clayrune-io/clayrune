"""Reference-only API/PyPI metadata (MC-1062/12b), never an executor or a probe.

Only bounded, allowlisted, provenance-labelled parameters are retained. No source
document, instructions, credential literal, command, approval or permission is a
field of this record. Its authority flags are computed here, never accepted from
the browser. Credential references use vault metadata only.
"""
from __future__ import annotations

import re

from mc import secrets_store as vault
from mc.desk_connect import parameter_schema as ps, url_check

SCHEMA = 'desk-reference-draft/1'
LABEL = 'Untrusted draft data; reference only, not approved or executable'
_FIELDS = {'address': 300, 'package': 200, 'auth_type': 30, 'transport': 30,
           'credential_names': 12, 'placements': 12, 'scopes': 20}
_LISTS = {'credential_names', 'placements', 'scopes'}
_PACKAGE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}(?:==[A-Za-z0-9][A-Za-z0-9_.+-]{0,79})?$')


class ReferenceError(ValueError):
    pass


def _text(raw, limit: int) -> str:
    if ps.clean_text(raw, limit) is None or ps.credential_like(raw):
        raise ReferenceError('draft metadata must be bounded non-secret text without hidden characters')
    return raw


def clean(raw, *, own_hosts=()) -> dict:
    """Validate a proposed metadata object; refuse unknown fields, never drop them."""
    if not isinstance(raw, dict) or set(raw) != {'kind', 'fields'}:
        raise ReferenceError('reference_draft must contain only kind and fields')
    kind = raw['kind']
    if kind not in ('api_base', 'api_spec', 'pypi'):
        raise ReferenceError('reference kind must be api_base, api_spec or pypi')
    fields = raw['fields']
    if not isinstance(fields, dict) or set(fields) - set(_FIELDS):
        raise ReferenceError('unknown reference metadata field')
    allowed = set(_FIELDS) - ({'address'} if kind == 'pypi' else {'package'})
    if set(fields) - allowed:
        raise ReferenceError('metadata does not match the reference kind')
    out = {}
    for name, item in fields.items():
        if not isinstance(item, dict) or set(item) != {'value', 'provenance', 'confidence'}:
            raise ReferenceError('each draft field needs value, provenance and confidence')
        if item['provenance'] not in ps.PROVENANCE or item['confidence'] not in ps.CONFIDENCE:
            raise ReferenceError('invalid draft provenance or confidence')
        value = item['value']
        if name in _LISTS:
            if not isinstance(value, list) or len(value) > _FIELDS[name]:
                raise ReferenceError('too many draft list entries')
            value = [_text(v, 120) for v in value]
            if name == 'placements' and any(v not in ps.PLACEMENTS for v in value):
                raise ReferenceError('invalid credential placement')
            if name == 'credential_names' and any(not ps.CREDENTIAL_NAME.fullmatch(v) for v in value):
                raise ReferenceError('invalid parameter name')
        else:
            value = _text(value, _FIELDS[name])
        if name == 'address':
            try:
                value = url_check.check_url(value, own_hosts=own_hosts)['url']
            except url_check.UrlError as e:
                raise ReferenceError('draft address must be a public HTTPS address without credentials') from e
            if ps.url_problem(value):
                raise ReferenceError('draft address contains a credential-like literal')
        if name == 'package' and (not isinstance(value, str) or not _PACKAGE.fullmatch(value)):
            raise ReferenceError('PyPI draft must name a package, optionally with ==version')
        if name == 'auth_type' and value not in ps.AUTH_TYPES:
            raise ReferenceError('invalid draft authentication type')
        if name == 'transport' and value not in ps.TRANSPORTS:
            raise ReferenceError('invalid draft transport')
        out[name] = {**item, 'value': value}
    required = 'package' if kind == 'pypi' else 'address'
    if required not in out:
        raise ReferenceError(f'the reference draft needs {required}')
    return {'schema': SCHEMA, 'kind': kind, 'fields': out, 'label': LABEL,
            'approved': False, 'executable': False, 'publish': False}


def stored(raw) -> dict:
    """A stored record retains the same validation; fixed flags cannot be forged."""
    if not isinstance(raw, dict):
        raise ReferenceError('invalid stored reference draft')
    return clean({'kind': raw.get('kind'), 'fields': raw.get('fields')})


def credential_reference(raw, vault_names=None) -> dict:
    if not isinstance(raw, dict) or set(raw) != {'name', 'existing'} or raw['existing'] is not True:
        raise ReferenceError('an existing credential takes only name and existing:true')
    name = raw['name']
    if not isinstance(name, str) or not vault.valid_name(name) or vault.is_server_internal(name):
        raise ReferenceError('invalid existing vault entry name')
    if vault_names is not None and name not in vault_names:
        raise ReferenceError('that credential is not in Secrets')
    return {'name': name, 'existing': True}
