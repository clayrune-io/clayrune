"""The shape of a detected connection DRAFT (docs/DESK_SERVICE_PROFILES_SPEC.md §5, slice U1).

A draft is what Clayrune thinks a user-chosen MCP server or API needs, read from
evidence a stranger wrote: package-registry metadata, a README, an OpenAPI document, a
pasted configuration, or the user's own input. It is a PROPOSAL the person edits. It is
never approved, never executed and never written anywhere by this slice.

This module is pure data and checks, no network, no model, no store:

  * the enumerations every field draws from (transport, auth type, credential placement,
    purpose, provenance, confidence): values outside them do not exist;
  * the bounds and the hidden-character / credential-like-literal tests every value
    passes before it is shown;
  * `risk_flags`, the labels computed IN CODE from the final values (the model never
    writes one);
  * `field`, the one way a value gets a provenance, an evidence id and a confidence.

`approved` is always False and `adapter_id`, central-review status and publisher trust
are not fields of a draft at all, so evidence cannot claim them.
"""
from __future__ import annotations

import ipaddress
import re
import unicodedata
from urllib.parse import parse_qsl, urlsplit

SCHEMA = 'desk-connect-proposal/1'
SCHEMA_VERSION = 1

KINDS = ('npm', 'pypi', 'remote_mcp', 'api_spec', 'api_base', 'pasted')
ROUTE_TYPES = ('mcp', 'api')
TRANSPORTS = ('stdio', 'streamable_http', 'sse', 'http', 'unknown')
MCP_TRANSPORTS = ('stdio', 'streamable_http', 'sse', 'unknown')
API_TRANSPORTS = ('http', 'unknown')
AUTH_TYPES = ('oauth', 'api_key', 'bearer', 'basic', 'none', 'unknown')
PLACEMENTS = ('env', 'header', 'query', 'body', 'cookie', 'basic_user', 'basic_password', 'unknown')
PURPOSES = ('publish', 'read_own', 'listen_broad', 'generate_image', 'generate_video', 'other')
# Where a value came from. `classifier` is the isolated model; the rest are deterministic
# parsers or the person's own typing.
PROVENANCE = ('user_input', 'registry_metadata', 'openapi', 'readme_example', 'pasted_config', 'classifier')
# `stated`: the text says it, character for character. `inferred`: a rule or the model
# chose an enumerated value from what the text describes. `uncertain`: partial/unclear.
CONFIDENCE = ('stated', 'inferred', 'uncertain')
EVIDENCE_KINDS = ('user_input', 'npm_registry', 'pypi_registry', 'openapi', 'readme', 'docs_page', 'pasted')
# Evidence whose PROSE goes to the isolated classifier. Structured registry/OpenAPI data
# is read by deterministic parsers only and its description fields are never shown.
PROSE_KINDS = ('readme', 'docs_page', 'pasted')

MAX_ALTERNATIVES = 8
MAX_CLASSIFIER_ALTERNATIVES = 4
MAX_ARGS = 20
MAX_ARG = 300
MAX_COMMAND = 200
MAX_URL = 300
MAX_CREDENTIALS = 12
MAX_SCOPES = 20
MAX_SCOPE = 120
MAX_OPERATIONS = 50
MAX_NAME = 80
CREDENTIAL_NAME = re.compile(r'^[A-Za-z_][A-Za-z0-9_.\-]{0,63}$')

WARNING = ('Detected from untrusted evidence: a README, a package listing or a document written by a third '
           'party. Nothing here is approved, installed or connected. Check every field and edit what is wrong.')

_BAD_CATEGORIES = {'Cc', 'Cf', 'Cs', 'Co', 'Cn', 'Zl', 'Zp'}
_SHELLS = frozenset({'sh', 'bash', 'zsh', 'dash', 'fish', 'ksh', 'csh', 'cmd', 'cmd.exe', 'powershell',
                     'powershell.exe', 'pwsh', 'pwsh.exe'})
_INTERPRETERS = frozenset({'python', 'python3', 'python.exe', 'py', 'node', 'node.exe', 'deno', 'bun', 'perl',
                           'ruby', 'php'})
_EVAL_FLAGS = frozenset({'-c', '-e', '-p', '--eval', '--print', '-command', '-encodedcommand', '/c', '/k'})
_DOWNLOADERS = frozenset({'curl', 'wget', 'iwr', 'invoke-webrequest'})
_RUNNERS = frozenset({'npx', 'bunx', 'uvx', 'pipx', 'pnpx', 'dlx'})
_SHELL_META = re.compile(r'[|;&$`<>\n]|\s')
_PIN = re.compile(r'(?:@\d+(?:\.\d+){1,3}[\w.+\-]*$|==\d)')
_TOKEN_PREFIXES = re.compile(
    r'(?:\b(?:sk|pk|rk)[-_](?:live|test|proj|ant|or)?[-_A-Za-z0-9]{16,}|\bgh[pousr]_[A-Za-z0-9]{20,}|'
    r'\bgithub_pat_[A-Za-z0-9_]{20,}|\bxox[abeprs]-[A-Za-z0-9-]{10,}|\bAKIA[0-9A-Z]{16}\b|'
    r'\bAIza[0-9A-Za-z_\-]{30,}|\bey[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{5,}|'
    r'\b(?:Bearer|Basic)\s+[A-Za-z0-9._~+/=\-]{16,})')
_TOKEN_SHAPE = re.compile(r'^[A-Za-z0-9_\-+/=]{28,}$')
_LOCAL_SUFFIXES = ('.local', '.internal', '.lan', '.home', '.corp', '.localdomain', '.intranet', '.home.arpa')


# ── value checks ─────────────────────────────────────────────────────────────
def has_hidden_chars(text: str) -> bool:
    """Control, format (bidi overrides/isolates, zero-width, BOM), separator, surrogate or
    unassigned characters. A value containing any is refused, not cleaned: a command that
    changes when it is cleaned is not the command that was read."""
    return any(unicodedata.category(ch) in _BAD_CATEGORIES for ch in text)


def credential_like(text: str) -> bool:
    """True when `text` looks like a real secret value (a known token prefix, a JWT, a
    `Bearer <long token>`, or a long unbroken mixed letter/digit token). Names and
    placeholders such as `NOTION_TOKEN` or `<your-key>` are not."""
    if not isinstance(text, str) or not text:
        return False
    if _TOKEN_PREFIXES.search(text):
        return True
    for piece in re.split(r'[\s=,;:]+', text):
        if (_TOKEN_SHAPE.match(piece) and re.search(r'[A-Za-z]', piece) and re.search(r'\d', piece)
                and '/' not in piece and piece.count('-') < 4 and piece.count('_') < 3):
            return True
    return False


def clean_text(value, limit: int) -> str | None:
    """`value` if it is a non-empty string within `limit` with no hidden characters,
    else None."""
    if not isinstance(value, str) or not value or len(value) > limit or has_hidden_chars(value):
        return None
    return value


def url_problem(url: str) -> str | None:
    """Why `url` may not appear in a draft, or None. http(s) only, no user name or
    password, no credential-like query value, no hidden characters, 300 characters."""
    if clean_text(url, MAX_URL) is None or re.search(r'\s', url):
        return 'not a plain address'
    try:
        parts = urlsplit(url)
        host = parts.hostname
    except ValueError:
        return 'not a valid address'
    if parts.scheme.lower() not in ('http', 'https') or not host:
        return 'not an http(s) address'
    if parts.username or parts.password or '@' in parts.netloc:
        return 'has a user name or password in it'
    if any(credential_like(v) or credential_like(k) for k, v in parse_qsl(parts.query, keep_blank_values=True)):
        return 'has what looks like a secret in its query'
    if credential_like(parts.path.rsplit('/', 1)[-1]):
        return 'has what looks like a secret in its path'
    return None


def local_or_private_host(url: str) -> bool:
    """A literal check on the host NAME in `url` (no DNS): localhost, a non-global IP
    literal, a reserved suffix, or a name without a dot."""
    try:
        host = (urlsplit(url).hostname or '').lower().rstrip('.')
    except ValueError:
        return False
    if not host:
        return False
    try:
        ip = ipaddress.ip_address(host)
        return not ip.is_global
    except ValueError:
        pass
    return host == 'localhost' or '.' not in host or host.endswith(_LOCAL_SUFFIXES)


# ── provenance ───────────────────────────────────────────────────────────────
def field(value, provenance: str, evidence_id: str | None, confidence: str = 'stated') -> dict:
    """One detected value with where it came from. Unknown provenance or confidence is a
    programming error, not data."""
    assert provenance in PROVENANCE, provenance
    assert confidence in CONFIDENCE, confidence
    return {'value': value, 'provenance': provenance, 'evidence_id': evidence_id, 'confidence': confidence}


# ── risk labels, computed in code from the final values ──────────────────────
def _basename(command: str) -> str:
    return re.split(r'[\\/]', command.strip().lower())[-1]


def runs_arbitrary_code(command: str | None, args: list[str]) -> bool:
    """A shell, an interpreter given inline code, a downloader, or a command line smuggled
    into one string. Shown verbatim and labelled; never refused."""
    if not command:
        return False
    base = _basename(command)
    if _SHELL_META.search(command.strip()) and ' ' in command.strip():
        return True
    if base in _SHELLS or base in _DOWNLOADERS:
        return True
    if base in _INTERPRETERS and any(a.lower() in _EVAL_FLAGS for a in args):
        return True
    return False


def unpinned_runner(command: str | None, args: list[str]) -> bool:
    """`npx -y pkg` / `uvx pkg` without an exact version: it would fetch whatever is
    current at launch."""
    if not command or _basename(command).removesuffix('.cmd').removesuffix('.exe') not in _RUNNERS:
        return False
    names = [a for a in args if a and not a.startswith('-')]
    return bool(names) and not _PIN.search(names[0])


def risk_flags(alt: dict) -> list[str]:
    """Plain-language flag ids for an alternative, derived from its values. The shown
    sentences live in the front end; the ids are stable."""
    f = alt['fields']
    flags: list[str] = []
    command = (f.get('command') or {}).get('value')
    args = (f.get('args') or {}).get('value') or []
    url = (f.get('url') or {}).get('value')
    if runs_arbitrary_code(command, args):
        flags.append('runs_arbitrary_code')
    if unpinned_runner(command, args):
        flags.append('unpinned_package')
    if (alt.get('package') or {}).get('pin') == 'unpinned':
        flags.append('unpinned_package')
    if url:
        if urlsplit(url).scheme.lower() == 'http':
            flags.append('unencrypted_connection')
        if local_or_private_host(url):
            flags.append('local_or_private_target')
    if alt['route_type'] == 'mcp' and alt['transport'] in ('streamable_http', 'sse'):
        flags.append('remote_server_can_change')
    if alt.get('redacted'):
        flags.append('credential_like_text_not_copied')
    if (alt.get('package') or {}).get('install_scripts'):
        flags.append('install_scripts')
    if (alt.get('package') or {}).get('source_build_required'):
        flags.append('source_build_required')
    seen = {p['provenance'] for p in _all_fields(f)}
    if seen & {'classifier', 'readme_example', 'openapi', 'registry_metadata', 'pasted_config'}:
        flags.append('detected_from_untrusted_evidence')
    return list(dict.fromkeys(flags))


def _all_fields(fields: dict):
    for v in fields.values():
        if isinstance(v, dict) and 'provenance' in v:
            yield v
        elif isinstance(v, list):
            for item in v:
                if isinstance(item, dict) and 'provenance' in item:
                    yield item


# ── building an alternative ──────────────────────────────────────────────────
def new_alternative(route_type: str, transport: str) -> dict:
    assert route_type in ROUTE_TYPES and transport in TRANSPORTS
    return {'route_type': route_type, 'transport': transport, 'fields': {}, 'credentials': [], 'scopes': [],
            'purposes': [], 'operations': [], 'redacted': False, 'notes': []}


def dedupe_key(alt: dict) -> tuple:
    f = alt['fields']
    val = lambda k: (f.get(k) or {}).get('value')        # noqa: E731
    return (alt['route_type'], alt['transport'], val('command'), tuple(val('args') or ()), val('url'),
            (alt.get('package') or {}).get('name'), alt.get('scheme'))


def merge_duplicate(first: dict, dup: dict) -> None:
    """Fold what `dup` (the same connection found in another source) knows into `first`:
    fields `first` lacks, an auth type `first` left unknown, credential names and scopes it
    does not list. Every value keeps its own provenance; nothing in `first` is overwritten."""
    for k, v in dup['fields'].items():
        cur = first['fields'].get(k)
        if cur is None or (k == 'auth_type' and cur['value'] == 'unknown' and v['value'] != 'unknown'):
            first['fields'][k] = v
    have = {(c['name'], c['placement']) for c in first['credentials']}
    first['credentials'] += [c for c in dup['credentials'] if (c['name'], c['placement']) not in have][:MAX_CREDENTIALS]
    for k, cap in (('scopes', MAX_SCOPES), ('purposes', len(PURPOSES))):
        first[k] = list(dict.fromkeys(first[k] + dup[k]))[:cap]
    first['redacted'] = first['redacted'] or dup['redacted']
    if dup.get('package') and not first.get('package'):
        first['package'] = dup['package']


def missing_fields(alt: dict) -> list[str]:
    """The parameters a person must still supply for this alternative to be usable."""
    f = alt['fields']
    need = []
    if alt['route_type'] == 'mcp' and alt['transport'] == 'stdio':
        if not (f.get('command') or {}).get('value'):
            need.append('command')
    elif not (f.get('url') or {}).get('value'):
        need.append('url')
    if alt['transport'] == 'unknown':
        need.append('transport')
    if (f.get('auth_type') or {}).get('value') in (None, 'unknown'):
        need.append('auth_type')
    return need


def finish_alternative(alt: dict, index: int) -> dict:
    """The alternative as returned: ids, derived labels, and the fixed statements that it
    is editable and unapproved."""
    out = dict(alt)
    out['id'] = f'a{index}'
    out['missing'] = missing_fields(alt)
    out['risk_flags'] = risk_flags(alt)
    out['editable'] = True
    out['incomplete'] = bool(out['missing'])
    out['approved'] = False
    out['label'] = 'Detected from untrusted evidence'
    return out
