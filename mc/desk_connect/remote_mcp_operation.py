"""The operation a person approves for a user-chosen REMOTE MCP server, Streamable HTTP or SSE
(docs/DESK_SERVICE_PROFILES_SPEC.md sections 6.1 and 6.3, slice U2d). The remote counterpart of
`custom_connection_operation`; it shares that module's fingerprint, so one Save approves either.

    {schema, protocol: 'streamable_http' | 'sse', ecosystem: 'remote', url, origin,
     auth: {type: 'none' | 'header' | 'oauth', issuer, scopes},
     credentials: [{header, vault, prefix, env}],
     exposure: {unencrypted, local_or_private, acknowledged},
     server_name, scope: {kind, project_id}}

Nothing secret is in it: a credential is an HTTP header NAME, a text prefix such as "Bearer ", and
a Secrets entry NAME. `env` is the fixed variable the credential wrapper fills for the bridge; it
is derived here so the launch line comes from the operation alone.

Strict by design (spec 6.3: `mc/mcp.py`'s normalizer coerces and drops quietly): an unknown key,
a wrong type, a hidden character, a credential-looking value in the address, an address that
carries a user name, a `{{...}}` placeholder (the credential wrapper would resolve one inside an
argument, which would let an address spend a vault entry) are all refusals.

Exposure: an `http://` address and a local or private host are valid choices, but each needs its
own explicit acknowledgement inside the operation (so it is fingerprinted), and Save refuses until
every flag that applies is acknowledged.
"""
from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlsplit

from mc import mcp as _mcp
from mc.desk_connect import custom_connection_operation as _npm_op
from mc.desk_connect import parameter_schema as _ps
from mc.desk_connect import remote_mcp_transport as _transport
from mc.desk_connect.mcp_errors import ActivationError

SCHEMA = 'desk-custom-connection/1'
FIELD_KEYS = {'url', 'protocol', 'server_name', 'auth', 'credentials', 'issuer', 'scopes', 'acknowledge', 'scope',
              'project_id'}
AUTH_TYPES = ('none', 'header', 'oauth')
EXPOSURE_FLAGS = ('unencrypted_connection', 'local_or_private_target')
MAX_URL = 500
MAX_CREDENTIALS = 4
MAX_SCOPES = 20
_PREFIX_RE = re.compile(r'^[A-Za-z0-9._ -]{0,40}$')
_SCOPE_RE = re.compile(r'^[A-Za-z0-9._:/@-]{1,100}$')
_SECRET_QUERY_KEYS = re.compile(r'(token|secret|passw|apikey|api_key|api-key|auth|bearer|credential|signature|sig|session|'
                                r'access_key|private_key)|^(key|pwd|pass|code)$', re.I)
_ENV_PREFIX = 'CLAYRUNE_REMOTE_CRED_'
_VAULT_RE = _npm_op._VAULT_RE


def _refuse(message: str, code: str = 'invalid', status: int = 400) -> ActivationError:
    return ActivationError(message, code, status)


def _placeholder(text: str) -> bool:
    return '{{' in text or '}}' in text


def clean_url(raw) -> str:
    """The address exactly as it will be used, or ActivationError. No network, no name lookup."""
    if not isinstance(raw, str) or not raw.strip():
        raise _refuse('enter the address of the remote MCP server, for example https://mcp.example.com/mcp', 'bad_url')
    text = raw.strip()
    if len(text) > MAX_URL or not text.isascii() or not text.isprintable() or ' ' in text or _ps.has_hidden_chars(text):
        raise _refuse('the address must be plain ASCII text without spaces (use the punycode form of a non-English name)',
                      'bad_url')
    if _placeholder(text):
        raise _refuse('the address cannot hold a {{ }} placeholder', 'bad_url')
    try:
        p = urlsplit(text)
        port = p.port
    except ValueError:
        raise _refuse('that is not a valid address', 'bad_url') from None
    if p.scheme not in ('http', 'https') or not p.hostname:
        raise _refuse('the address must start with https:// (or http://, which needs its own acknowledgement)', 'bad_url')
    if p.username is not None or p.password is not None or '@' in p.netloc:
        raise _refuse('the address cannot carry a user name or password. Put a credential in the Vault and attach it as a '
                      'header instead.', 'secret_in_url')
    if p.fragment:
        raise _refuse('the address cannot have a #fragment', 'bad_url')
    if port is not None and not 1 <= port <= 65535:
        raise _refuse('that port is not valid', 'bad_url')
    for k, v in parse_qsl(p.query, keep_blank_values=True):
        if _SECRET_QUERY_KEYS.search(k) or _ps.credential_like(v):
            raise _refuse('the address carries something that looks like a secret in its query. Secret values are never put in '
                          'an address: store it in the Vault and attach it as a header instead.', 'secret_in_url')
    if _ps.credential_like(text):
        raise _refuse('the address looks like it holds a secret value', 'secret_in_url')
    return text


def _clean_issuer(raw) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise _refuse('an OAuth server needs the issuer address it signs in with (https://...)', 'issuer_required')
    text = raw.strip()
    try:
        p = urlsplit(text)
        p.port
    except ValueError:
        raise _refuse('the issuer is not a valid address', 'bad_issuer') from None
    if len(text) > 300 or not text.isascii() or not text.isprintable() or ' ' in text or _placeholder(text) \
            or p.scheme != 'https' or not p.hostname or p.username or p.password or p.query or p.fragment:
        raise _refuse('the issuer must be a plain https:// address with no user name, query or fragment', 'bad_issuer')
    return text


def _clean_scopes(raw) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > MAX_SCOPES:
        raise _refuse(f'scopes must be a list of at most {MAX_SCOPES} words', 'bad_scopes')
    out: list[str] = []
    for s in raw:
        if not isinstance(s, str) or not _SCOPE_RE.match(s):
            raise _refuse('a scope is one word of letters, digits and . _ : / @ -', 'bad_scopes')
        if s not in out:
            out.append(s)
    return sorted(out)


def propose_protocol(url: str) -> dict:
    """The protocol to pre-select, from the address alone (no request is made, so it can be wrong:
    the person edits it). A path that ends in `/sse` is the legacy event-stream endpoint."""
    last = (urlsplit(url).path or '/').rstrip('/').rsplit('/', 1)[-1].lower()
    if last == 'sse':
        return {'value': 'sse', 'basis': 'the address ends in /sse', 'editable': True}
    return {'value': 'streamable_http', 'basis': 'the usual protocol for a new server; the address gives no sign of the '
                                                 'older SSE one', 'editable': True}


def exposure_of(url: str) -> dict:
    return {'unencrypted': urlsplit(url).scheme == 'http', 'local_or_private': _ps.local_or_private_host(url)}


def required_ack(op: dict) -> list[str]:
    e = op['exposure']
    return [f for f, on in (('unencrypted_connection', e['unencrypted']), ('local_or_private_target', e['local_or_private']))
            if on]


def missing_ack(op: dict) -> list[str]:
    return [f for f in required_ack(op) if f not in op['exposure']['acknowledged']]


_ACK_TEXT = {'unencrypted_connection': 'the address is http://, so the connection is not encrypted and anyone on the path '
                                        'can read or change it, including any token sent to it',
             'local_or_private_target': 'the address is on this computer or a private network, so the server could '
                                        'reach things the internet cannot'}


def require_ack(op: dict) -> None:
    """Refuse a Save whose exposure flags were not each acknowledged in the reviewed operation."""
    missing = missing_ack(op)
    if missing:
        raise _refuse('Tick the box for each exposure on the card before saving: ' + '; '.join(_ACK_TEXT[m] for m in missing)
                      + '.', 'acknowledgement_required', 409)


def clean_fields(raw, vault_names, *, default_name: str | None = None) -> dict:
    """The person's edits, validated: `{url, protocol, server_name, auth, issuer, scopes, credentials,
    acknowledge, scope, project_id}`. Raises ActivationError before anything is read or sent."""
    if not isinstance(raw, dict):
        raise _refuse('the request must be an object')
    unknown = sorted(set(raw) - FIELD_KEYS)
    if unknown:
        raise _refuse(f'unknown field(s): {", ".join(unknown)}')
    url = clean_url(raw.get('url'))
    protocol = raw.get('protocol', 'streamable_http')
    if protocol not in _transport.PROTOCOLS:
        raise _refuse('the protocol must be "streamable_http" or "sse"', 'bad_protocol')
    host = (urlsplit(url).hostname or 'remote-mcp').lower()
    name = raw.get('server_name')
    if name is None or name == '':
        name = re.sub(r'[^A-Za-z0-9._-]+', '-', default_name or host).strip('-._')[:60] or 'remote-mcp'
    if not isinstance(name, str) or _mcp.validate_name(name) is not None:
        raise _refuse('the server name must start with a letter or digit and use only letters, digits, dots, dashes '
                      'and underscores (64 characters at most)', 'bad_server_name')
    creds_raw = raw.get('credentials', [])
    if not isinstance(creds_raw, list) or len(creds_raw) > MAX_CREDENTIALS:
        raise _refuse(f'credentials must be a list of at most {MAX_CREDENTIALS} items', 'bad_credentials')
    auth = raw.get('auth', 'header' if creds_raw else 'none')
    if auth not in AUTH_TYPES:
        raise _refuse('auth must be "none", "header" or "oauth"', 'bad_auth')
    if auth == 'header' and not creds_raw:
        raise _refuse('choose the Vault entry that holds the token, and the header it goes in', 'credential_required')
    if auth != 'header' and creds_raw:
        raise _refuse('credentials are only used with auth "header"', 'bad_credentials')
    issuer = raw.get('issuer')
    if auth == 'oauth':
        issuer = _clean_issuer(issuer)
    elif issuer not in (None, ''):
        raise _refuse('an issuer is only used with auth "oauth"', 'bad_issuer')
    else:
        issuer = None
    creds: list[dict] = []
    seen: set[str] = set()
    for c in creds_raw:
        if not isinstance(c, dict) or not set(c) <= {'header', 'vault', 'prefix'} or not {'header', 'vault'} <= set(c) \
                or not isinstance(c['header'], str) or not isinstance(c['vault'], str):
            raise _refuse('each credential is {header, vault, prefix?}: the HTTP header it goes in, the Vault entry that '
                          'holds it and the text before it (such as "Bearer ")', 'bad_credentials')
        header, vault = c['header'].strip(), c['vault']
        try:
            _transport.check_headers({header: 'x'})
        except ValueError:
            raise _refuse(f'"{header[:40]}" cannot be used as a credential header here (it must be a plain header name that '
                          f'is not one the connection sets itself)', 'bad_credential_header') from None
        if header.lower() in seen:
            raise _refuse('each header can carry one credential', 'bad_credential_header')
        prefix = c.get('prefix')
        if prefix is None:
            prefix = 'Bearer ' if header.lower() == 'authorization' else ''
        if not isinstance(prefix, str) or not _PREFIX_RE.match(prefix) or _placeholder(prefix):
            raise _refuse('the text before the token is at most 40 plain characters, for example "Bearer "', 'bad_prefix')
        if not _VAULT_RE.match(vault):
            raise _refuse('that is not a Vault entry name', 'bad_credential_vault')
        if vault not in vault_names:
            raise _refuse(f'there is no Vault entry named "{vault}". Store it in the Vault first.', 'unknown_vault_entry')
        seen.add(header.lower())
        creds.append({'header': header, 'vault': vault, 'prefix': prefix})
    scopes = _clean_scopes(raw.get('scopes'))
    ack = raw.get('acknowledge', [])
    if not isinstance(ack, list) or any(a not in EXPOSURE_FLAGS for a in ack):
        raise _refuse(f'acknowledge may hold only: {", ".join(EXPOSURE_FLAGS)}', 'bad_acknowledge')
    scope = raw.get('scope', 'project')
    if scope not in ('project', 'global'):
        raise _refuse('scope must be "project" or "global"', 'bad_scope')
    project_id = raw.get('project_id')
    if scope == 'project' and (not isinstance(project_id, str) or not project_id):
        raise _refuse('choose the project this server is for, or choose global', 'project_required')
    return {'url': url, 'protocol': protocol, 'server_name': name, 'auth': auth, 'issuer': issuer, 'scopes': scopes,
            'credentials': sorted(creds, key=lambda c: (c['header'].lower(), c['vault'])),
            'acknowledge': sorted(set(ack)), 'scope': scope, 'project_id': None if scope == 'global' else project_id}


def build(fields: dict) -> dict:
    """The operation for validated fields."""
    url = fields['url']
    p = urlsplit(url)
    port = p.port or (443 if p.scheme == 'https' else 80)
    exposure = exposure_of(url)
    op = {'schema': SCHEMA, 'protocol': fields['protocol'], 'ecosystem': 'remote', 'url': url,
          'origin': f'{p.scheme}://{(p.hostname or "").lower()}:{port}',
          'auth': {'type': fields['auth'], 'issuer': fields['issuer'], 'scopes': list(fields['scopes'])},
          'credentials': [{**c, 'env': f'{_ENV_PREFIX}{i}'} for i, c in enumerate(fields['credentials'])],
          'exposure': {**exposure, 'acknowledged': []},
          'server_name': fields['server_name'],
          'scope': {'kind': fields['scope'], 'project_id': fields['project_id']}}
    op['exposure']['acknowledged'] = sorted(set(fields['acknowledge']) & set(required_ack(op)))
    return op


fingerprint = _npm_op.fingerprint

_LABELS = (('protocol', 'protocol'), ('url', 'address'), ('auth', 'sign-in and scopes'), ('credentials', 'credentials'),
           ('exposure', 'exposure'), ('server_name', 'server name'), ('scope', 'reach'))


def changes(old: dict | None, new: dict) -> list[dict]:
    """What differs from an earlier approval of the same server name, as text. Empty when there is
    no earlier approval, it was a different kind of server, or nothing changed."""
    if not old or old.get('ecosystem') != 'remote':
        return []
    import json

    def show(v):
        return (json.dumps(v, sort_keys=True, ensure_ascii=True) if isinstance(v, (list, dict)) else str(v))[:300]
    return [{'field': label, 'from': show(old.get(key)), 'to': show(new.get(key))}
            for key, label in _LABELS if old.get(key) != new.get(key)]
