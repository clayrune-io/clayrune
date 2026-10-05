"""Deterministic readers for connection evidence (spec §5.1: "deterministic parsers for
package metadata, fenced configuration examples and OpenAPI structure").

Nothing here runs anything, fetches anything or asks a model. Each function takes TEXT or
an already-decoded JSON value and returns draft alternatives (`parameter_schema`). Hostile
input is expected: sizes and depths are bounded, a value with hidden characters, a
credential-like literal or a user name in a URL drops that value (or that alternative)
rather than being cleaned into something else, `$ref` is followed only inside the same
document, and no description text from a document is ever copied into a draft.

    parse_input()          what kind of thing the person typed (or `NeedsKind`)
    parse_config_text()    fenced / pasted MCP server configuration (`mcpServers`, `servers`, a bare server)
    parse_openapi()        servers, security schemes, scopes and operations of an OpenAPI 3 / Swagger 2 document
    parse_npm()            one npm version document
    parse_pypi()           one PyPI project/version document
    remote_alternative()   a remote MCP URL typed by the person (no contact with it)
    api_base_alternative() an API base URL typed by the person (no contact with it)
"""
from __future__ import annotations

import json
import re
from urllib.parse import urlsplit, urljoin

from mc.desk_connect import display_text
from mc.desk_connect import parameter_schema as ps
from mc.desk_connect.parameter_schema import field

MAX_JSON_BYTES = 2_000_000
MAX_NODES = 20_000
MAX_DEPTH = 40
MAX_BLOCKS = 8
MAX_SERVERS = 4
_FENCE = re.compile(r'```[A-Za-z0-9_+-]*[ \t]*\r?\n(.*?)\r?\n[ \t]*```', re.DOTALL)
_NPM_NAME = re.compile(r'^(?:@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]*$')
_NPM_TAG = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$')
_EXACT_NPM = re.compile(r'^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.+-]+)?$')
_PY_NAME = re.compile(r'^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$')
_EXACT_PY = re.compile(r'^\d+(?:\.\d+)*(?:[A-Za-z0-9.+_-]*)$')
_NPM_URL = re.compile(r'^https?://(?:www\.)?npmjs\.com/package/((?:@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]*)(?:/v/([^/?#\s]+))?/?$', re.I)
_PYPI_URL = re.compile(r'^https?://(?:www\.)?pypi\.org/project/([A-Za-z0-9][A-Za-z0-9._-]*)(?:/([^/?#\s]+))?/?$', re.I)
_ENV_NAME = re.compile(r'^[A-Za-z_][A-Za-z0-9_]{0,63}$')
_HEADER_NAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9\-]{0,63}$')


def _d(v) -> dict:
    return v if isinstance(v, dict) else {}


def _l(v) -> list:
    return v if isinstance(v, list) else []


class NeedsKind(ValueError):
    """The input could be more than one thing. The person is asked which; nothing is
    searched and no other service is tried (spec §5.1)."""


class InputError(ValueError):
    """A refused input. `code` is machine-readable."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


# ── bounded JSON ─────────────────────────────────────────────────────────────
def _no_dupes(pairs):
    d = {}
    for k, v in pairs:
        if k in d:
            raise ValueError('duplicate key')
        d[k] = v
    return d


def load_json(text, *, allow_dupes: bool = False):
    """The JSON value in `text`, or None. Oversized, malformed, over-deep or over-wide
    input yields None; a duplicate key is refused unless `allow_dupes` (registry
    documents sometimes repeat a key; a person's pasted configuration must not)."""
    if isinstance(text, (bytes, bytearray)):
        if len(text) > MAX_JSON_BYTES:
            return None
        try:
            text = bytes(text).decode('utf-8')
        except UnicodeDecodeError:
            return None
    if not isinstance(text, str) or len(text) > MAX_JSON_BYTES:
        return None
    try:
        data = json.loads(text) if allow_dupes else json.loads(text, object_pairs_hook=_no_dupes)
    except (ValueError, RecursionError):
        return None
    return data if _within_bounds(data) else None


def _within_bounds(data) -> bool:
    stack, nodes = [(data, 1)], 0
    while stack:
        node, depth = stack.pop()
        nodes += 1
        if nodes > MAX_NODES or depth > MAX_DEPTH:
            return False
        if isinstance(node, dict):
            stack.extend((v, depth + 1) for v in node.values())
        elif isinstance(node, list):
            stack.extend((v, depth + 1) for v in node)
    return True


# ── what was typed ───────────────────────────────────────────────────────────
def parse_package_spec(kind: str, raw: str) -> dict:
    """`{name, version, tag, extras}` for an npm or PyPI spec, or InputError. Only an exact
    version or (npm) a dist-tag is accepted: resolving a RANGE would need the whole
    release list, which this slice does not download (`version_range_unsupported`)."""
    text = (raw or '').strip()
    if kind == 'npm':
        text = re.sub(r'^npm:', '', text, flags=re.I)
        m = _NPM_URL.match(text)
        if m:
            name, ver = m.group(1), m.group(2)
        else:
            name, ver = text, None
            at = text.rfind('@')
            if at > 0:
                name, ver = text[:at], text[at + 1:]
        if not _NPM_NAME.match(name or '') or len(name) > 214:
            raise InputError('bad_package', 'That is not an npm package name.')
        if ver in (None, ''):
            return {'name': name, 'version': None, 'tag': 'latest', 'extras': []}
        if _EXACT_NPM.match(ver):
            return {'name': name, 'version': ver, 'tag': None, 'extras': []}
        if _NPM_TAG.match(ver) and not re.search(r'\d+\.\d+', ver):
            return {'name': name, 'version': None, 'tag': ver, 'extras': []}
        raise InputError('version_range_unsupported',
                         'Clayrune reads one exact version or a tag such as latest here, not a version range. '
                         'Name the exact version you want.')
    text = re.sub(r'^(?:pypi|pip):', '', text, flags=re.I)
    m = _PYPI_URL.match(text)
    extras: list[str] = []
    if m:
        name, ver = m.group(1), m.group(2)
    else:
        name, ver = text, None
        if '==' in text:
            name, ver = text.split('==', 1)
        elif re.search(r'[<>!~]=?|;|\s', text.split('[', 1)[0]):
            raise InputError('version_range_unsupported',
                             'Clayrune reads one exact version (name==1.2.3) or the latest release here, not a range.')
        em = re.match(r'^([^\[\]]+)\[([A-Za-z0-9._,\- ]*)\]$', name)
        if em:
            name = em.group(1)
            extras = [e.strip() for e in em.group(2).split(',') if e.strip()][:10]
    if not _PY_NAME.match(name or '') or len(name) > 100:
        raise InputError('bad_package', 'That is not a PyPI package name.')
    if ver and not _EXACT_PY.match(ver):
        raise InputError('version_range_unsupported', 'Clayrune reads one exact version (name==1.2.3) or the latest release here.')
    return {'name': re.sub(r'[-_.]+', '-', name).lower(), 'version': ver or None, 'tag': None, 'extras': extras}


def parse_input(kind: str | None, raw, text: str | None = None) -> dict:
    """`{kind, input}` normalised. Without an explicit `kind`, only unambiguous input is
    classified (`npm:`/`pypi:` prefixes, npmjs.com and pypi.org links, pasted JSON or
    multi-line text); anything else raises NeedsKind."""
    s = raw.strip() if isinstance(raw, str) else ''
    if kind is not None:
        if kind not in ps.KINDS:
            raise InputError('bad_kind', 'The type must be one of ' + ', '.join(ps.KINDS) + '.')
        if kind == 'pasted':
            body = s or (text or '')
            if not body.strip():
                raise InputError('empty', 'Paste the configuration, document or README text.')
            return {'kind': kind, 'input': body}
        if not s:
            raise InputError('empty', 'Enter a package name or address.')
        return {'kind': kind, 'input': s}
    if not s:
        raise InputError('empty', 'Enter a package name or address, or paste text.')
    if re.match(r'^npm:', s, re.I) or _NPM_URL.match(s):
        return {'kind': 'npm', 'input': s}
    if re.match(r'^(?:pypi|pip):', s, re.I) or _PYPI_URL.match(s):
        return {'kind': 'pypi', 'input': s}
    if s.startswith('{') or '\n' in s or '```' in s:
        return {'kind': 'pasted', 'input': s}
    raise NeedsKind('That could be an npm package, a PyPI package, a remote MCP address, an API spec or an API base address. Say which.')


# ── typed addresses ──────────────────────────────────────────────────────────
def _transport_hint(url: str) -> tuple[str, str]:
    path = (urlsplit(url).path or '').rstrip('/').lower()
    if path.endswith('/sse'):
        return 'sse', 'inferred'
    if path.endswith('/mcp'):
        return 'streamable_http', 'inferred'
    return 'unknown', 'uncertain'


def remote_alternative(url: str, evidence_id: str) -> dict:
    """A remote MCP server at the address the person typed. The address is never contacted:
    the transport is read from its path (`/sse`, `/mcp`) or left `unknown` and editable."""
    why = ps.url_problem(url)
    if why:
        raise InputError('bad_url', f'That address {why}.')
    transport, conf = _transport_hint(url)
    alt = ps.new_alternative('mcp', transport)
    alt['fields']['url'] = field(url, 'user_input', evidence_id, 'stated')
    alt['fields']['transport'] = field(transport, 'user_input', evidence_id, conf)
    alt['fields']['auth_type'] = field('unknown', 'user_input', evidence_id, 'uncertain')
    return alt


def api_base_alternative(url: str, evidence_id: str) -> dict:
    why = ps.url_problem(url)
    if why:
        raise InputError('bad_url', f'That address {why}.')
    alt = ps.new_alternative('api', 'http')
    alt['fields']['url'] = field(url, 'user_input', evidence_id, 'stated')
    alt['fields']['transport'] = field('http', 'user_input', evidence_id, 'stated')
    alt['fields']['auth_type'] = field('unknown', 'user_input', evidence_id, 'uncertain')
    return alt


# ── MCP configuration blocks ─────────────────────────────────────────────────
def _candidate_blocks(text: str) -> list:
    out = []
    whole = load_json(text)
    if whole is not None:
        out.append(whole)
    for m in _FENCE.finditer(text or ''):
        block = m.group(1).strip()
        if not re.search(r'mcpServers|"servers"|"command"|"url"|"serverUrl"|"httpUrl"', block):
            continue
        stripped = re.sub(r'(?m)^\s*//.*$', '', block)
        data = load_json(stripped)
        if data is not None:
            out.append(data)
        if len(out) >= MAX_BLOCKS:
            break
    return out


def _server_dicts(obj) -> list[tuple[str, dict]]:
    """(name, server) pairs: under `mcpServers` / `servers` anywhere in the value, or the
    value itself when it is one server."""
    found: list[tuple[str, dict]] = []
    stack = [obj]
    while stack and len(found) < MAX_SERVERS:
        node = stack.pop(0)
        if isinstance(node, dict):
            for key in ('mcpServers', 'servers', 'context_servers'):
                group = node.get(key)
                if isinstance(group, dict):
                    for name, srv in group.items():
                        if isinstance(srv, dict) and len(found) < MAX_SERVERS:
                            found.append((str(name), srv))
            if not found and ('command' in node or 'url' in node or 'serverUrl' in node or 'httpUrl' in node):
                found.append(('server', node))
            if not found:
                stack.extend(v for v in node.values() if isinstance(v, (dict, list)))
        elif isinstance(node, list):
            stack.extend(v for v in node if isinstance(v, (dict, list)))
    return found


def _cred(name: str, placement: str, provenance: str, evidence_id: str) -> dict:
    return {'name': name, 'placement': placement, 'provenance': provenance, 'evidence_id': evidence_id,
            'confidence': 'stated'}


def _config_alternative(name: str, srv: dict, provenance: str, evidence_id: str) -> dict | None:
    """One draft from one server object, or None when it states neither a command nor an
    address. Env VALUES and header VALUES are never copied; only their names are."""
    command = srv.get('command') if isinstance(srv.get('command'), str) else None
    url = next((srv[k] for k in ('url', 'serverUrl', 'httpUrl') if isinstance(srv.get(k), str)), None)
    redacted = False
    if command is not None:
        if ps.clean_text(command, ps.MAX_COMMAND) is None or ps.credential_like(command):
            return None
        alt = ps.new_alternative('mcp', 'stdio')
        alt['fields']['command'] = field(command, provenance, evidence_id)
        alt['fields']['transport'] = field('stdio', provenance, evidence_id)
        raw_args = srv.get('args', [])
        if not isinstance(raw_args, list) or len(raw_args) > ps.MAX_ARGS or not all(isinstance(a, str) for a in raw_args):
            return None
        args = []
        for a in raw_args:
            if ps.has_hidden_chars(a) or len(a) > ps.MAX_ARG:
                return None
            if ps.credential_like(a):
                args.append('<credential not copied>')
                redacted = True
            else:
                args.append(a)
        alt['fields']['args'] = field(args, provenance, evidence_id)
    elif url is not None:
        if ps.url_problem(url):
            return None
        ty = str(srv.get('type') or srv.get('transport') or '').lower().replace('-', '_')
        if ty in ('sse',):
            transport, conf = 'sse', 'stated'
        elif ty in ('http', 'streamable_http', 'streamablehttp'):
            transport, conf = 'streamable_http', 'stated'
        else:
            transport, conf = _transport_hint(url)
        alt = ps.new_alternative('mcp', transport)
        alt['fields']['url'] = field(url, provenance, evidence_id)
        alt['fields']['transport'] = field(transport, provenance, evidence_id, conf)
    else:
        return None
    env = srv.get('env')
    if isinstance(env, dict):
        for k, v in list(env.items())[:ps.MAX_CREDENTIALS]:
            if isinstance(k, str) and _ENV_NAME.match(k):
                alt['credentials'].append(_cred(k, 'env', provenance, evidence_id))
                if isinstance(v, str) and ps.credential_like(v):
                    redacted = True
    headers = srv.get('headers')
    auth = None
    if isinstance(headers, dict):
        for k, v in list(headers.items())[:ps.MAX_CREDENTIALS]:
            if isinstance(k, str) and _HEADER_NAME.match(k):
                alt['credentials'].append(_cred(k, 'header', provenance, evidence_id))
                if k.lower() == 'authorization':
                    auth = 'bearer' if isinstance(v, str) and v.lower().startswith('bearer') else 'unknown'
                if isinstance(v, str) and ps.credential_like(v):
                    redacted = True
    alt['fields']['auth_type'] = field(auth or ('api_key' if alt['credentials'] else 'unknown'), provenance, evidence_id,
                                       'stated' if auth else 'uncertain')
    alt['redacted'] = redacted
    shown = display_text.clean(name, ps.MAX_NAME)
    if shown:
        alt['name_hint'] = field(shown, provenance, evidence_id)
    return alt


def parse_config_text(text: str, evidence_id: str, provenance: str = 'readme_example') -> list[dict]:
    """Draft alternatives from the JSON configuration in `text` (whole text or fenced
    blocks). Nothing in a block is executed or even interpreted beyond reading the keys
    above."""
    alts: list[dict] = []
    for block in _candidate_blocks(text):
        for name, srv in _server_dicts(block):
            alt = _config_alternative(name, srv, provenance, evidence_id)
            if alt is not None:
                alts.append(alt)
    return alts


# ── OpenAPI ──────────────────────────────────────────────────────────────────
_HTTP_METHODS = ('get', 'put', 'post', 'delete', 'options', 'head', 'patch')
_READ_ONLY = ('get', 'head', 'options')


def is_openapi(doc) -> bool:
    return isinstance(doc, dict) and (isinstance(doc.get('openapi'), str) or isinstance(doc.get('swagger'), str))


def _resolve_ref(doc: dict, node, depth: int = 0):
    """`node`, or the same-document target of its `$ref`. External references are not
    followed (they would be a fetch)."""
    if isinstance(node, dict) and isinstance(node.get('$ref'), str) and depth < 5:
        ref = node['$ref']
        if not ref.startswith('#/'):
            return None
        cur = doc
        for part in ref[2:].split('/'):
            part = part.replace('~1', '/').replace('~0', '~')
            if not isinstance(cur, dict) or part not in cur:
                return None
            cur = cur[part]
        return _resolve_ref(doc, cur, depth + 1)
    return node


def _server_url(doc: dict, spec_url: str | None) -> str | None:
    url = None
    if isinstance(doc.get('servers'), list) and doc['servers']:
        first = doc['servers'][0]
        if isinstance(first, dict) and isinstance(first.get('url'), str):
            url = first['url']
            variables = _d(first.get('variables'))
            for k, v in variables.items():
                if isinstance(v, dict) and isinstance(v.get('default'), str):
                    url = url.replace('{' + str(k) + '}', v['default'])
    elif isinstance(doc.get('host'), str):                     # Swagger 2
        schemes = _l(doc.get('schemes')) or ['https']
        scheme = 'https' if 'https' in schemes else str(schemes[0] if schemes else 'https')
        url = f"{scheme}://{doc['host']}{doc.get('basePath') or ''}"
    if url is None:
        return None
    if url.startswith('/') and spec_url:
        url = urljoin(spec_url, url)
    return url


def _scheme_alternative(name: str, scheme: dict, base: dict, evidence_id: str) -> dict:
    alt = {k: (list(v) if isinstance(v, list) else v) for k, v in base.items()}
    alt['fields'] = dict(base['fields'])
    alt['credentials'], alt['scopes'] = [], []
    ty = str(scheme.get('type') or '').lower()
    if ty == 'apikey':
        where = str(scheme.get('in') or '').lower()
        placement = where if where in ('header', 'query', 'cookie') else 'unknown'
        nm = scheme.get('name')
        if isinstance(nm, str) and ps.CREDENTIAL_NAME.match(nm):
            alt['credentials'].append(_cred(nm, placement, 'openapi', evidence_id))
        alt['fields']['auth_type'] = field('api_key', 'openapi', evidence_id)
    elif ty == 'http' or ty == 'basic':
        s = str(scheme.get('scheme') or ty).lower()
        if s == 'bearer':
            alt['credentials'].append(_cred('Authorization', 'header', 'openapi', evidence_id))
            alt['fields']['auth_type'] = field('bearer', 'openapi', evidence_id)
        elif s == 'basic':
            alt['credentials'].append(_cred('username', 'basic_user', 'openapi', evidence_id))
            alt['credentials'].append(_cred('password', 'basic_password', 'openapi', evidence_id))
            alt['fields']['auth_type'] = field('basic', 'openapi', evidence_id)
        else:
            alt['fields']['auth_type'] = field('unknown', 'openapi', evidence_id, 'uncertain')
    elif ty == 'oauth2':
        flows = _d(scheme.get('flows'))
        if not flows and 'flow' in scheme:                       # Swagger 2: one flow inline
            flows = {str(scheme.get('flow')): scheme}
        auth_url = token_url = None
        scopes: list[str] = []
        for flow in flows.values():
            if not isinstance(flow, dict):
                continue
            for key, var in (('authorizationUrl', 'a'), ('tokenUrl', 't')):
                u = flow.get(key)
                if isinstance(u, str) and not ps.url_problem(u):
                    if var == 'a' and not auth_url:
                        auth_url = u
                    if var == 't' and not token_url:
                        token_url = u
            sc = flow.get('scopes')
            if isinstance(sc, dict):
                scopes += [k for k in sc if isinstance(k, str) and ps.clean_text(k, ps.MAX_SCOPE)]
        alt['scopes'] = list(dict.fromkeys(scopes))[:ps.MAX_SCOPES]
        alt['fields']['auth_type'] = field('oauth', 'openapi', evidence_id)
        if auth_url:
            alt['fields']['authorization_url'] = field(auth_url, 'openapi', evidence_id)
        if token_url:
            alt['fields']['token_url'] = field(token_url, 'openapi', evidence_id)
    elif ty == 'openidconnect':
        u = scheme.get('openIdConnectUrl')
        alt['fields']['auth_type'] = field('oauth', 'openapi', evidence_id, 'inferred')
        if isinstance(u, str) and not ps.url_problem(u):
            alt['fields']['issuer'] = field(u, 'openapi', evidence_id)
    else:
        alt['fields']['auth_type'] = field('unknown', 'openapi', evidence_id, 'uncertain')
    shown = display_text.clean(name, ps.MAX_NAME)
    if shown:
        alt['name_hint'] = field(shown, 'openapi', evidence_id)
        alt['scheme'] = shown
    return alt


def parse_openapi(doc, evidence_id: str, spec_url: str | None = None) -> tuple[list[dict], dict]:
    """(alternatives, notes). One alternative per security scheme the document declares
    (the globally required ones first), or a single `unknown`-auth one when it declares
    none. The operation list is capped at 50 and says so. External `$ref`s are counted,
    never fetched."""
    notes = {'operations_total': 0, 'operations_shown': 0, 'external_refs_ignored': 0}
    if not is_openapi(doc):
        return [], notes
    notes['external_refs_ignored'] = _count_external_refs(doc)
    base = ps.new_alternative('api', 'http')
    url = _server_url(doc, spec_url)
    if url and not ps.url_problem(url) and '{' not in url:
        base['fields']['url'] = field(url, 'openapi', evidence_id)
    elif url:
        base['notes'].append('server_address_templated_or_refused')
    base['fields']['transport'] = field('http', 'openapi', evidence_id)
    ops, total = [], 0
    paths = _d(doc.get('paths'))
    for path, item in paths.items():
        if not isinstance(item, dict) or not isinstance(path, str):
            continue
        for method in _HTTP_METHODS:
            op = item.get(method)
            if not isinstance(op, dict):
                continue
            total += 1
            if len(ops) < ps.MAX_OPERATIONS and ps.clean_text(path, 200):
                ops.append({'method': method.upper(), 'path': path, 'read_only': method in _READ_ONLY,
                            'provenance': 'openapi', 'evidence_id': evidence_id})
    base['operations'] = ops
    notes['operations_total'], notes['operations_shown'] = total, len(ops)
    schemes = {}
    comp = _d(doc.get('components'))
    raw_schemes = _d(comp.get('securitySchemes')) or _d(doc.get('securityDefinitions'))
    if raw_schemes:
        for k, v in raw_schemes.items():
            v = _resolve_ref(doc, v)
            if isinstance(k, str) and isinstance(v, dict):
                schemes[k] = v
    required: list[str] = []
    if isinstance(doc.get('security'), list):
        for req in doc['security']:
            if isinstance(req, dict):
                required += [k for k in req if k in schemes]
    order = list(dict.fromkeys(required + list(schemes)))[:ps.MAX_CLASSIFIER_ALTERNATIVES * 2]
    alts = [_scheme_alternative(n, schemes[n], base, evidence_id) for n in order]
    if not alts:
        none_declared = isinstance(doc.get('security'), list) and not doc['security'] and not schemes
        base['fields']['auth_type'] = field('none' if none_declared else 'unknown', 'openapi', evidence_id,
                                            'stated' if none_declared else 'uncertain')
        alts = [base]
    return alts, notes


def _count_external_refs(doc) -> int:
    n, stack = 0, [doc]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            r = node.get('$ref')
            if isinstance(r, str) and not r.startswith('#'):
                n += 1
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return n


# ── package metadata ─────────────────────────────────────────────────────────
_INSTALL_SCRIPTS = ('preinstall', 'install', 'postinstall', 'prepare')


def parse_npm(doc, evidence_id: str) -> tuple[dict | None, str]:
    """(alternative, readme_text) from one npm version document. Facts are what the
    REGISTRY says (`dist.integrity` is a claim until the bytes are fetched and hashed in a
    later slice). The command is inferred from the package's `bin` only when it has exactly
    one; otherwise it stays missing. Returns (None, '') for a document without a name."""
    if not isinstance(doc, dict) or not isinstance(doc.get('name'), str) or not _NPM_NAME.match(doc['name']):
        return None, ''
    name = doc['name']
    version = doc.get('version') if isinstance(doc.get('version'), str) and _EXACT_NPM.match(doc.get('version', '')) else None
    bin_ = doc.get('bin')
    bins = [name.split('/')[-1]] if isinstance(bin_, str) else [k for k in bin_ if isinstance(k, str)] if isinstance(bin_, dict) else []
    scripts = _d(doc.get('scripts'))
    dist = _d(doc.get('dist'))
    deps = _d(doc.get('dependencies'))
    lic = doc.get('license') if isinstance(doc.get('license'), str) else None
    publisher = _d(doc.get('_npmUser')).get('name')
    package = {
        'ecosystem': 'npm', 'name': name, 'resolved_version': version,
        'pin': 'resolved_unverified' if version else 'unpinned',
        'integrity_claimed': dist.get('integrity') if isinstance(dist.get('integrity'), str) and len(dist['integrity']) < 200 else None,
        'bin': [b for b in bins if ps.clean_text(b, 100)][:5],
        'install_scripts': [s for s in _INSTALL_SCRIPTS if isinstance(scripts.get(s), str)],
        'dependency_count': len(deps), 'license': display_text.clean(lic, 60) if lic else None,
        'publisher_claimed': display_text.clean(publisher, 60) if isinstance(publisher, str) else None,
        'provenance': 'registry_metadata', 'evidence_id': evidence_id,
        'bytes_fetched': False,
    }
    alt = ps.new_alternative('mcp', 'stdio')
    alt['package'] = package
    alt['fields']['transport'] = field('stdio', 'registry_metadata', evidence_id, 'inferred')
    if len(package['bin']) == 1:
        alt['fields']['command'] = field(package['bin'][0], 'registry_metadata', evidence_id, 'inferred')
        alt['fields']['args'] = field([], 'registry_metadata', evidence_id, 'inferred')
    alt['fields']['auth_type'] = field('unknown', 'registry_metadata', evidence_id, 'uncertain')
    readme = doc.get('readme')
    return alt, readme if isinstance(readme, str) else ''


def parse_pypi(doc, evidence_id: str, wanted_version: str | None = None) -> tuple[dict | None, str]:
    """(alternative, description_text) from a PyPI JSON document. Artifacts and their
    sha256 digests are the registry's claim; nothing is downloaded. A release with no
    wheel is flagged as needing a source build (which runs code in a later slice)."""
    if not isinstance(doc, dict) or not isinstance(doc.get('info'), dict) or not isinstance(doc['info'].get('name'), str):
        return None, ''
    info = doc['info']
    name = re.sub(r'[-_.]+', '-', info['name']).lower()
    if not _PY_NAME.match(name):
        return None, ''
    version = info.get('version') if isinstance(info.get('version'), str) and _EXACT_PY.match(info.get('version', '')) else None
    files = _l(doc.get('urls'))
    arts = []
    for f in files[:30]:
        if not isinstance(f, dict) or f.get('yanked'):
            continue
        digest = _d(f.get('digests')).get('sha256')
        ptype = f.get('packagetype')
        if ptype in ('bdist_wheel', 'sdist') and isinstance(digest, str) and re.fullmatch(r'[0-9a-f]{64}', digest):
            arts.append({'type': ptype, 'sha256_claimed': digest,
                         'file': display_text.clean(str(f.get('filename') or ''), 120)})
    reqs = _l(info.get('requires_dist'))
    lic = info.get('license') if isinstance(info.get('license'), str) else None
    package = {
        'ecosystem': 'pypi', 'name': name, 'resolved_version': version,
        'pin': 'resolved_unverified' if version else 'unpinned',
        'artifacts_claimed': arts[:6],
        'source_build_required': bool(arts) and not any(a['type'] == 'bdist_wheel' for a in arts),
        'install_scripts': [], 'dependency_count': len(reqs),
        'license': display_text.clean(lic, 60) if lic and len(lic) < 200 else None,
        'requires_python': display_text.clean(str(info.get('requires_python') or ''), 40) or None,
        'provenance': 'registry_metadata', 'evidence_id': evidence_id, 'bytes_fetched': False,
    }
    alt = ps.new_alternative('mcp', 'stdio')
    alt['package'] = package
    alt['fields']['transport'] = field('stdio', 'registry_metadata', evidence_id, 'inferred')
    alt['fields']['auth_type'] = field('unknown', 'registry_metadata', evidence_id, 'uncertain')
    desc = info.get('description')
    return alt, desc if isinstance(desc, str) else ''
