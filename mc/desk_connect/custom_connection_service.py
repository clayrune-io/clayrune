"""The Review and the one Save of a user-chosen MCP server (docs/DESK_SERVICE_PROFILES_SPEC.md,
section 6.1, slice U2a). Flask-free: the routes (`mc/blueprints/desk_connect_custom_routes.py`)
hold the human check and the passcode and call these two functions.

    prepare   Review. Reads the package without running it (`custom_npm_artifact.resolve`),
              builds the operation on the server (`custom_connection_operation`), remembers it
              under a server-made `request_id` for 30 minutes and returns the approval card:
              the exact command, the pin, the reach, the risks, what this install cannot do,
              and, for a server that is already approved, exactly what changed.
    commit    Save. Takes ONLY `{request_id, fingerprint}`. The operation that runs is the one
              `prepare` stored, never one the client sends: the fingerprint is a check that the
              person is approving what that Review showed, so a changed command, version, digest,
              argument, credential or scope (a new `prepare`, a new fingerprint) is a new
              approval, and an old fingerprint under a new Review is a 409.

Nothing is written at Review: no vault entry, no MCP config, no record. Save records the
approval, then provisions (`custom_connection_activation.provision`); a limitation on this
install leaves the approval saved as `pending_runtime` and the answer says so.

One Save of one server name at a time (a per-name lock, downloads included): two Saves of the same
server serialize, and the second finds the first's result. A Save that did not finish
(`pending_runtime`, `setup_failed`) is not remembered as done, so the same request can be sent again.
"""
from __future__ import annotations

import re
import secrets
import threading
import time
from collections import OrderedDict

from mc import secrets_store as _vault
from mc.core import _log
from mc.desk_connect import custom_connection_activation as _activation
from mc.desk_connect import custom_connection_operation as _op
from mc.desk_connect import custom_connection_store as _store
from mc.desk_connect import custom_npm_artifact as _artifact
from mc.desk_connect import parameter_parsers as _pp
from mc.desk_connect.mcp_errors import ActivationError

PREPARED_TTL_S = 30 * 60
_MAX_PREPARED = 50
_REMEMBER = 200
_REQUEST_ID = re.compile(r'^[A-Za-z0-9_-]{16,80}$')
_FINGERPRINT = re.compile(r'^sha256:[0-9a-f]{64}$')
_PROJECT_ID = re.compile(r'^[A-Za-z0-9_.-]{1,120}$')

_guard = threading.Lock()
_prepare_busy = threading.Lock()
_prepared: 'OrderedDict[str, dict]' = OrderedDict()
_done: 'OrderedDict[str, tuple[str, dict]]' = OrderedDict()
_name_locks: dict[str, threading.Lock] = {}


def _forget_all_for_tests() -> None:
    with _guard:
        _prepared.clear()
        _done.clear()
        _name_locks.clear()


def visible_vault_names(scope: str, project_id: str | None) -> set[str]:
    """The Secrets entries a server of this reach can resolve: global ones, plus the project's own
    for a project server. Metadata only."""
    try:
        if scope == 'project':
            return {s['name'] for s in _vault.list_secrets(project_id)}
        return {s['name'] for s in _vault.list_secrets() if s.get('scope', 'global') == 'global'}
    except Exception as e:
        _log(f'[desk_connect] vault listing failed: {type(e).__name__}', flush=True)
        return set()


def _remember(request_id: str, rec: dict) -> None:
    with _guard:
        _prepared[request_id] = rec
        _prepared.move_to_end(request_id)
        while len(_prepared) > _MAX_PREPARED:
            _prepared.popitem(last=False)


def _lookup(request_id: str) -> dict | None:
    with _guard:
        rec = _prepared.get(request_id)
        if rec and rec['expires'] < time.monotonic():
            _prepared.pop(request_id, None)
            return None
        return rec


# ── Review ───────────────────────────────────────────────────────────────────

def _risks(op: dict) -> list[dict]:
    out = [{'code': 'user_supplied', 'label': 'User supplied; not reviewed by Clayrune'},
           {'code': 'runs_local_code', 'label': 'Runs code on this computer. It has your account\'s file and network '
                                                'access: Clayrune does not sandbox it.'}]
    if op['credentials']:
        out.append({'code': 'secrets_to_process', 'label': 'The Secrets entries listed are given to that program as '
                                                          'environment variables when it starts.'})
    if op['scope']['kind'] == 'global':
        out.append({'code': 'global_reach', 'label': 'Global: agents in EVERY project can use this server\'s tools.'})
    out.append({'code': 'digest_not_safety', 'label': 'The digest proves the files are the ones you approve here. It '
                                                      'does not prove they are safe.'})
    return out


def _card(request_id: str, fp: str, op: dict, artifact: dict, project: dict | None, previous: dict | None) -> dict:
    scope = op['scope']['kind']
    reach = ({'scope': 'project', 'project': {'id': project['id'], 'name': project['name']},
              'who': f'Agents working in the project "{project["name"]}" only.'} if project else
             {'scope': 'global', 'project': None, 'who': 'Agents in every project.'})
    changes = _op.changes(previous['operation'] if previous else None, op)
    return {
        'schema': 'desk-custom-card/1', 'request_id': request_id, 'fingerprint': fp,
        'origin': {'code': 'user_supplied', 'label': 'User supplied; not reviewed by Clayrune'},
        'title': f'{op["package"]}@{op["version"]}', 'server_name': op['server_name'], 'protocol': 'stdio',
        'command': _activation.describe_argv(op),
        'install_steps': [],
        'install_note': 'Clayrune downloads this one archive itself, checks its sha512 and unpacks it. No npm, npx or '
                        'install script runs, and nothing it contains runs until an agent session starts the server.',
        'working_directory': 'The folder of the agent session that starts it.',
        'first_start': 'Deferred: nothing is started now.',
        'package': {'ecosystem': 'npm', 'registry': 'registry.npmjs.org', 'source': op['tarball'], 'version': op['version'],
                    'integrity': op['integrity'], 'pinned': True, 'entry': op['entry'],
                    'size_bytes': artifact['size_bytes'], 'unpacked_bytes': artifact['unpacked_bytes'],
                    'licence': artifact['licence'],
                    'publisher': {'name': artifact['publisher_claimed'],
                                  'status': 'claimed' if artifact['publisher_claimed'] else 'unknown'},
                    'registry_stated_digest': artifact['registry_stated_integrity'],
                    'install_scripts_not_run': artifact['install_scripts']},
        'credentials': [{'env': c['env'], 'vault': c['vault'],
                         'placement': 'environment variable of the server process', 'recipient': 'the server process'}
                        for c in op['credentials']],
        'arguments': list(op['args']),
        'reach': {**reach, 'local_code': 'Runs with this account\'s file and network permissions; no sandbox.',
                  'secrets_to': 'the server process' if op['credentials'] else 'none'},
        'scope_default': 'project', 'scope_options': ['project', 'global'], 'scope_chosen': scope,
        'risks': _risks(op), 'limitations': _activation.limitations(),
        'changes': changes, 'reask': bool(previous and previous['fingerprint'] != fp),
        'replaces': previous['fingerprint'] if previous and previous['fingerprint'] != fp else None,
        'approved': False,
    }


def prepare(body, resolve_project) -> dict:
    """The Review card for the package a person typed. `resolve_project(project_id)` returns
    `{id, name, path}` or raises ActivationError (the route supplies it). Raises ActivationError."""
    if not isinstance(body, dict) or not isinstance(body.get('package'), str) or not body['package'].strip():
        raise ActivationError('enter the npm package to add, for example @scope/name or name@1.2.3', 'bad_package', 400)
    try:
        spec = _pp.parse_package_spec('npm', body['package'])
    except _pp.InputError as e:
        raise ActivationError(str(e), e.code, 400) from e
    raw = {k: v for k, v in body.items() if k != 'package'}
    unknown = sorted(set(raw) - (_op.FIELD_KEYS - {'package'}))
    if unknown:
        raise ActivationError(f'unknown field(s): {", ".join(unknown)}', 'invalid', 400)
    scope = raw.get('scope', 'project')
    pid = raw.get('project_id')
    if scope == 'project' and isinstance(pid, str) and not _PROJECT_ID.match(pid):
        raise ActivationError('that project id is not valid', 'bad_project', 400)
    vault_names = visible_vault_names(scope, pid if isinstance(pid, str) else None)
    fields = _op.clean_fields(raw, vault_names, default_name=spec['name'])
    project = resolve_project(fields['project_id']) if fields['scope'] == 'project' else None
    if not _prepare_busy.acquire(blocking=False):
        raise ActivationError('Another package is being read. Wait for it to finish.', 'busy', 429)
    try:
        artifact = _artifact.resolve(body['package'], entry=fields['entry'])
    finally:
        _prepare_busy.release()
    op = _op.build(artifact, fields)
    fp = _op.fingerprint(op)
    path = project['path'] if project else None
    clash = _activation.conflict(op, path)
    if clash:
        raise clash
    previous = _store.get(op['scope']['kind'], op['scope']['project_id'], op['server_name'])
    request_id = secrets.token_urlsafe(24)
    _remember(request_id, {'op': op, 'fingerprint': fp, 'project': project, 'expires': time.monotonic() + PREPARED_TTL_S})
    return _card(request_id, fp, op, artifact, project, previous)


# ── Save ─────────────────────────────────────────────────────────────────────

def clean_submission(body) -> tuple[str, str]:
    """`(request_id, fingerprint)` from a Save request, shape-checked. Raises ActivationError."""
    if not isinstance(body, dict) or set(body) - {'request_id', 'fingerprint', 'passcode'}:
        raise ActivationError('a Save carries only the request id, the fingerprint and the passcode', 'invalid', 400)
    rid, fp = body.get('request_id'), body.get('fingerprint')
    if not isinstance(rid, str) or not _REQUEST_ID.match(rid):
        raise ActivationError('request_id is not valid', 'invalid', 400)
    if not isinstance(fp, str) or not _FINGERPRINT.match(fp):
        raise ActivationError('fingerprint is not valid', 'invalid', 400)
    return rid, fp


def check_submission(request_id: str, fingerprint: str) -> dict:
    """The stored Review this Save approves, or ActivationError. Called BEFORE the passcode, so a stale
    or wrong approval costs no guess: `review_expired` (404; nothing stored under that id) or
    `changed_since_review` (409; the fingerprint is not the one that Review showed)."""
    with _guard:
        done = _done.get(request_id)
    if done is not None and done[0] != fingerprint:
        raise ActivationError('that approval was already used for a different configuration. Review it again.',
                              'changed_since_review', 409)
    rec = _lookup(request_id)
    if rec is None:
        if done is not None:
            return {'done': done[1]}
        raise ActivationError('that review expired or was never made. Review the server again.', 'review_expired', 404)
    if rec['fingerprint'] != fingerprint or _op.fingerprint(rec['op']) != fingerprint:
        raise ActivationError('the configuration changed since you reviewed it. Review it again.',
                              'changed_since_review', 409)
    return rec


def _name_lock(key: str) -> threading.Lock:
    with _guard:
        return _name_locks.setdefault(key, threading.Lock())


def commit(request_id: str, fingerprint: str) -> tuple[dict, bool]:
    """Record the approval and provision the server. Returns `(result, duplicate)`. The caller has
    checked the human and the passcode and called `check_submission`. Raises ActivationError only for
    a refusal before anything was written; a setup problem is a result with `state` not `registered`."""
    rec = check_submission(request_id, fingerprint)
    if 'done' in rec:
        return rec['done'], True
    op, project = rec['op'], rec['project']
    path = project['path'] if project else None
    key = _store.key(op['scope']['kind'], op['scope']['project_id'], op['server_name'])
    with _name_lock(key):
        with _guard:
            done = _done.get(request_id)
        if done is not None:
            return done[1], True
        clash = _activation.conflict(op, path)
        if clash:
            raise clash
        _store.put(op, fingerprint, 'saved', project_path=path)
        _log(f'[desk_connect] custom MCP approved: {op["package"]}@{op["version"]} as {op["server_name"]} '
             f'({op["scope"]["kind"]})', flush=True)
        outcome = _activation.provision(op, path)
        _store.set_state(op['scope']['kind'], op['scope']['project_id'], op['server_name'],
                         outcome['state'], outcome.get('code', ''))
        result = {'ok': True, 'approved': True, 'fingerprint': fingerprint, 'server_name': op['server_name'],
                  'scope': op['scope']['kind'], 'project_id': op['scope']['project_id'], **outcome}
        if _activation._base.passphrase_backed():
            result['notice'] = _activation._base.PASSPHRASE_NOTICE
        if outcome['state'] == 'registered':
            with _guard:
                _done[request_id] = (fingerprint, result)
                while len(_done) > _REMEMBER:
                    _done.popitem(last=False)
    return result, False


# ── State ────────────────────────────────────────────────────────────────────

def connections(path_of) -> list[dict]:
    """Every approved server with its truthful state now. `path_of(project_id)` returns the project's
    folder or None. Metadata only: vault entry names, never a value."""
    out = []
    for rec in _store.all_records():
        path = path_of(rec['project_id']) if rec['scope'] == 'project' else None
        state = _activation.derive_state(rec, path)
        op = rec['operation']
        out.append({'server_name': rec['server_name'], 'scope': rec['scope'], 'project_id': rec['project_id'],
                    'package': op['package'], 'version': op['version'], 'fingerprint': rec['fingerprint'],
                    'approved_at': rec['approved_at'], 'credentials': op['credentials'], **state})
    return out
