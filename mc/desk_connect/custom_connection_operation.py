"""The operation a person approves for a user-chosen MCP server, and its fingerprint
(docs/DESK_SERVICE_PROFILES_SPEC.md, sections 6.1 and 6.2, slice U2a).

One dictionary, `operation`, holds everything the human Save covers. It is built on the
SERVER from what Review resolved (`custom_npm_artifact`) and the few fields a person edits,
never from a client-supplied command line, and its `fingerprint` is the sha256 of its
canonical JSON. A changed package, version, digest, entry, argument, credential reference,
server name or scope is a different fingerprint, so it is a different approval:

    {schema, protocol: 'stdio', ecosystem: 'npm', package, version, tarball, integrity,
     entry, args, credentials: [{env, vault}], server_name, scope: {kind, project_id},
     strip_env, install_steps: []}

Nothing secret is in it: a credential is an environment variable NAME and a vault entry NAME.
The launch line that is written to the MCP config is derived from it (`custom_connection_
activation`) and holds the same two names, never a value.

`clean_fields` is the whole of what a person may type: a server name, an entry file, extra
arguments, vault references and a scope. It is strict (an unknown key, a wrong type, a hidden
character, a value that looks like a secret are all refusals) because the normalizer in
`mc/mcp.py` would otherwise coerce or drop them quietly, and the card must show exactly what
will run.
"""
from __future__ import annotations

import hashlib
import json
import re

from mc import mcp as _mcp
from mc.desk_connect import parameter_schema as _ps
from mc.desk_connect.mcp_errors import ActivationError

SCHEMA = 'desk-custom-connection/1'
STRIP_ENV = ('NODE_OPTIONS', 'NODE_PATH')
FIELD_KEYS = {'package', 'entry', 'server_name', 'args', 'credentials', 'scope', 'project_id'}
MAX_ARGS = 16
MAX_ARG = 300
MAX_CREDENTIALS = 8
_ENV_RE = re.compile(r'^[A-Z][A-Z0-9_]{0,63}$')
# Names that change how Node, the loader or the shell find code. A vault value must not be able to
# set them (the launch line strips the first two on purpose); an intentional override would be its
# own approved field with its risk shown, which this slice does not have.
_RESERVED_ENV = {'NODE_OPTIONS', 'NODE_PATH', 'NODE_EXTRA_CA_CERTS', 'NODE_TLS_REJECT_UNAUTHORIZED', 'PATH', 'PATHEXT',
                 'HOME', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'TEMP', 'TMP', 'COMSPEC', 'SYSTEMROOT',
                 'LD_PRELOAD', 'LD_LIBRARY_PATH', 'ELECTRON_RUN_AS_NODE', 'CLAYRUNE_HOME', 'MC_DIR'}
_VAULT_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._:/-]{0,119}$')


def _refuse(message: str, code: str = 'invalid', status: int = 400) -> ActivationError:
    return ActivationError(message, code, status)


def _slug(package: str) -> str:
    base = re.sub(r'[^A-Za-z0-9._-]+', '-', package.rsplit('/', 1)[-1]).strip('-._') or 'mcp-server'
    return base[:60]


def clean_fields(raw, vault_names, *, default_name: str) -> dict:
    """The person's edits, validated: `{entry, server_name, args, credentials, scope, project_id}`
    (`entry` None when not named). Raises ActivationError before anything is downloaded."""
    if not isinstance(raw, dict):
        raise _refuse('the request must be an object')
    unknown = sorted(set(raw) - FIELD_KEYS)
    if unknown:
        raise _refuse(f'unknown field(s): {", ".join(unknown)}')
    name = raw.get('server_name')
    if name is None or name == '':
        name = _slug(default_name)
    if not isinstance(name, str) or _mcp.validate_name(name) is not None:
        raise _refuse('the server name must start with a letter or digit and use only letters, digits, dots, dashes '
                      'and underscores (64 characters at most)', 'bad_server_name')
    entry = raw.get('entry')
    if entry is not None and (not isinstance(entry, str) or not entry.strip() or len(entry) > 200
                              or _ps.has_hidden_chars(entry)):
        raise _refuse('the start file must be a path inside the package', 'bad_entry')
    args = raw.get('args', [])
    if not isinstance(args, list) or len(args) > MAX_ARGS:
        raise _refuse(f'arguments must be a list of at most {MAX_ARGS} items', 'bad_args')
    clean_args: list[str] = []
    for a in args:
        if not isinstance(a, str) or not a or len(a) > MAX_ARG or _ps.has_hidden_chars(a):
            raise _refuse('each argument must be plain text (1 to 300 characters, no hidden characters)', 'bad_args')
        if _ps.credential_like(a):
            raise _refuse('an argument looks like a secret value. Secrets are never typed into a command: store it in '
                          'Secrets and attach it as a credential instead.', 'secret_in_args')
        clean_args.append(a)
    creds = raw.get('credentials', [])
    if not isinstance(creds, list) or len(creds) > MAX_CREDENTIALS:
        raise _refuse(f'credentials must be a list of at most {MAX_CREDENTIALS} items', 'bad_credentials')
    seen: set[str] = set()
    clean_creds: list[dict] = []
    for c in creds:
        if not isinstance(c, dict) or set(c) != {'env', 'vault'} or not isinstance(c['env'], str) \
                or not isinstance(c['vault'], str):
            raise _refuse('each credential is {env, vault}: the variable the server reads and the Secrets entry that '
                          'holds it', 'bad_credentials')
        env, vault = c['env'], c['vault']
        if not _ENV_RE.match(env) or env in _RESERVED_ENV or env.startswith(('DYLD_', 'LD_')) or env in seen:
            raise _refuse(f'"{env[:64]}" cannot be used as a credential variable here (it must be CAPITALS_AND_DIGITS, '
                          f'once, and not a name that changes how programs are found or loaded)', 'bad_credential_env')
        if not _VAULT_RE.match(vault):
            raise _refuse('that is not a Secrets entry name', 'bad_credential_vault')
        if vault not in vault_names:
            raise _refuse(f'there is no Secrets entry named "{vault}". Store it in Secrets first.', 'unknown_vault_entry')
        seen.add(env)
        clean_creds.append({'env': env, 'vault': vault})
    scope = raw.get('scope', 'project')
    if scope not in ('project', 'global'):
        raise _refuse('scope must be "project" or "global"', 'bad_scope')
    project_id = raw.get('project_id')
    if scope == 'project' and (not isinstance(project_id, str) or not project_id):
        raise _refuse('choose the project this server is for, or choose global', 'project_required')
    if scope == 'global':
        project_id = None
    return {'entry': entry.strip() if isinstance(entry, str) else None, 'server_name': name, 'args': clean_args,
            'credentials': sorted(clean_creds, key=lambda c: c['env']), 'scope': scope, 'project_id': project_id}


def build(artifact: dict, fields: dict) -> dict:
    """The operation for a resolved artifact and validated fields."""
    return {'schema': SCHEMA, 'protocol': 'stdio', 'ecosystem': 'npm',
            'package': artifact['package'], 'version': artifact['version'],
            'integrity': artifact['integrity'], 'tarball': artifact['tarball'], 'entry': artifact['entry'],
            'args': list(fields['args']), 'credentials': [dict(c) for c in fields['credentials']],
            'server_name': fields['server_name'],
            'scope': {'kind': fields['scope'], 'project_id': fields['project_id']},
            'strip_env': list(STRIP_ENV), 'install_steps': []}


def fingerprint(op: dict) -> str:
    body = json.dumps(op, sort_keys=True, separators=(',', ':'), ensure_ascii=True)
    return 'sha256:' + hashlib.sha256(body.encode('ascii')).hexdigest()


_LABELS = (('package', 'package'), ('version', 'version'), ('integrity', 'package digest'), ('entry', 'start file'),
           ('args', 'arguments'), ('credentials', 'credentials'), ('server_name', 'server name'), ('scope', 'reach'))


def changes(old: dict | None, new: dict) -> list[dict]:
    """What differs from an earlier approval of the same server: `[{field, from, to}]`, as text
    (credential entries are names). Empty when there is no earlier approval or nothing changed."""
    if not old:
        return []

    def show(v):
        if isinstance(v, (list, dict)):
            return json.dumps(v, sort_keys=True, ensure_ascii=True)[:300]
        return str(v)[:300]
    return [{'field': label, 'from': show(old.get(key)), 'to': show(new.get(key))}
            for key, label in _LABELS if old.get(key) != new.get(key)]
