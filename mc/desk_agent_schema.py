"""Record schemas, identities and limits for the Desk agent channel (MC-1151 P1).

Spec: `docs/desk_v1/AGENT_CHANNEL_SPEC.md` section 4 (data model). This module
is pure: it validates and normalizes record SHAPES and mints IDs. It never
reads or writes a file -- that is `mc/desk_agent_store.py` -- and it renders
nothing and lints nothing (P2/P3 own those).

Every limit below is published as a constant and refused, never truncated:
the backlog-note API once cut text at 2000 bytes in silence and lost weeks
of findings (CLAUDE.md, 2026-08-15). An over-limit value raises
`SchemaError`; the caller saves nothing.

One whole campaign record lives in one file, `checklist.json`, shaped:

    {
      "schema_version": 1,
      "kind": "desk_agent_checklist",
      "checklist":        {...},          # campaign checklist
      "entries":          {id: {...}},    # listing / documentation entries
      "revisions":        {id: {...}},    # prepared revisions (append-only)
      "evidence":         {id: {...}},    # fact evidence (append-only)
      "lint_results":     {id: {...}},    # P2 writes these (append-only)
      "submission_events": [{...}, ...]   # human-reported outcomes (append-only)
    }
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from typing import Any

from mc.desk_agent_public_data import (
    PublicDataRefused, require_no_credential, validate_public_url,
)

SCHEMA_VERSION = 1
RECORD_KIND = 'desk_agent_checklist'

# -- published limits (spec section 4: "explicit, refuse, never truncate") ----
MAX_ID_LEN = 64
MAX_SHORT_TEXT = 200          # names, identities, versions, field names
MAX_FIELD_TEXT = 8_000        # one typed factual field of a revision
MAX_RENDERED_BYTES = 64_000   # exact rendered text of one revision, UTF-8
MAX_EXCERPT = 4_000           # one evidence excerpt
MAX_REASON = 500              # applicability / outcome reasons
MAX_ARTIFACTS = 50
MAX_FIELDS_PER_REVISION = 64
MAX_FINDINGS = 500
MAX_RECORD_BYTES = 8 * 1024 * 1024   # whole checklist.json; refuse beyond

# -- stable vocabularies ------------------------------------------------------
# Section 3's five core surfaces, plus the two documentation cards that are not
# also a listing surface. Context7 is ONE surface shared by both sections
# ("linked rather than duplicated"), so it is not repeated as a docs id.
LISTING_SURFACE_IDS = (
    'mcp_registry', 'claude_plugin_directory', 'chatgpt_directory',
    'context7', 'skills_sh',
)
DOCS_SURFACE_IDS = ('llms_txt', 'markdown_for_agents')
SURFACE_IDS = LISTING_SURFACE_IDS + DOCS_SURFACE_IDS

ARTIFACT_KINDS = ('mcp_server', 'plugin', 'skill', 'library', 'app_integration')
APPLICABILITY = ('applicable', 'not_applicable', 'needs_information')
ENTRY_LIFECYCLE = ('active', 'removed')   # removed keeps its history and id
EVIDENCE_SOURCE_KINDS = ('owner_provided', 'browser_read')
LINT_RESULTS = ('pass', 'refuse', 'unreviewed')
SUBMISSION_OUTCOMES = ('submitted', 'listed', 'rejected', 'withdrawn')

ID_PREFIX = {
    'checklist': 'agck', 'channel': 'agch', 'entry': 'agen',
    'revision': 'agrv', 'evidence': 'agev', 'lint': 'agli',
    'submission': 'agsb', 'artifact': 'agar',
}

# External IDs (project, campaign) and our own minted IDs share one shape. No
# dot, slash, backslash, colon or leading separator, so a validated id can be
# joined onto a path without escaping it.
_ID_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$')
_FIELD_NAME_RE = re.compile(r'^[a-z][a-z0-9_]{0,63}$')
_HASH_RE = re.compile(r'^sha256:[0-9a-f]{64}$')
_VERSION_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$')


class SchemaError(ValueError):
    """A record or input does not match the agent-channel schema.

    Messages name fields and rules; they never quote a submitted value.
    """


def valid_id(value: Any) -> bool:
    return isinstance(value, str) and bool(_ID_RE.fullmatch(value))


def require_id(name: str, value: Any) -> str:
    if not valid_id(value):
        raise SchemaError(f'{name}: invalid id (letters, digits, _ and - only, '
                          f'1-{MAX_ID_LEN} chars, no path separators)')
    return value


def require_own_id(kind: str, value: Any) -> str:
    require_id(f'{kind}_id', value)
    if not value.startswith(ID_PREFIX[kind] + '-'):
        raise SchemaError(f'{kind}_id: not a {kind} id')
    return value


def new_id(kind: str) -> str:
    return f'{ID_PREFIX[kind]}-{uuid.uuid4().hex[:16]}'


def content_hash(text: str) -> str:
    return 'sha256:' + hashlib.sha256(text.encode('utf-8')).hexdigest()


def canonical_hash(obj: Any) -> str:
    """Stable hash of a JSON-able value (sorted keys, no whitespace)."""
    blob = json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    return content_hash(blob)


def _text(name: str, value: Any, limit: int, *, required: bool = True,
          allow_newlines: bool = False) -> str:
    if value is None:
        value = ''
    if not isinstance(value, str):
        raise SchemaError(f'{name}: must be text')
    if required and not value.strip():
        raise SchemaError(f'{name}: required')
    if len(value) > limit:
        raise SchemaError(f'{name}: longer than {limit} characters')
    if not allow_newlines and ('\n' in value or '\r' in value):
        raise SchemaError(f'{name}: must be a single line')
    if '\x00' in value:
        raise SchemaError(f'{name}: contains a NUL byte')
    try:
        require_no_credential(name, value)
    except PublicDataRefused as e:
        raise SchemaError(str(e))
    return value


def _url(name: str, value: Any, *, required: bool) -> str | None:
    if value in (None, '') and not required:
        return None
    if not isinstance(value, str):
        raise SchemaError(f'{name}: a public URL is required')
    try:
        return validate_public_url(name, value)
    except PublicDataRefused as e:
        raise SchemaError(str(e))


def _choice(name: str, value: Any, allowed: tuple[str, ...]) -> str:
    if value not in allowed:
        raise SchemaError(f'{name}: must be one of {", ".join(allowed)}')
    return value


def require_choice(name: str, value: Any, allowed: tuple[str, ...]) -> str:
    return _choice(name, value, allowed)


def optional_url(name: str, value: Any, *, required: bool = False) -> str | None:
    return _url(name, value, required=required)


# -- input normalizers (owner/agent-supplied data -> stored shape) ------------

def normalize_artifact(raw: Any, *, artifact_id: str | None = None) -> dict:
    """Owner-provided artifact facts. `identity` is the exact package / server
    / skill name; it stays owner-provided until a later phase checks it."""
    if not isinstance(raw, dict):
        raise SchemaError('artifact: must be an object')
    aid = artifact_id or raw.get('id') or new_id('artifact')
    require_own_id('artifact', aid)
    version = raw.get('version')
    if version not in (None, ''):
        if not isinstance(version, str) or not _VERSION_RE.fullmatch(version):
            raise SchemaError('artifact.version: invalid version string')
    else:
        version = None
    return {
        'id': aid,
        'kind': _choice('artifact.kind', raw.get('kind'), ARTIFACT_KINDS),
        'name': _text('artifact.name', raw.get('name'), MAX_SHORT_TEXT),
        'public_url': _url('artifact.public_url', raw.get('public_url'), required=True),
        'identity': _text('artifact.identity', raw.get('identity'), MAX_SHORT_TEXT,
                          required=False) or None,
        'version': version,
        'source': 'owner_provided',
    }


def normalize_evidence(raw: Any, *, now: str) -> dict:
    if not isinstance(raw, dict):
        raise SchemaError('evidence: must be an object')
    excerpt = _text('evidence.excerpt', raw.get('excerpt'), MAX_EXCERPT,
                    allow_newlines=True)
    bindings = raw.get('field_bindings') or []
    if not isinstance(bindings, list) or len(bindings) > MAX_FIELDS_PER_REVISION:
        raise SchemaError('evidence.field_bindings: must be a list of field names')
    for b in bindings:
        if not isinstance(b, str) or not _FIELD_NAME_RE.fullmatch(b):
            raise SchemaError('evidence.field_bindings: invalid field name')
    return {
        'id': new_id('evidence'),
        'excerpt': excerpt,
        'source_url': _url('evidence.source_url', raw.get('source_url'), required=True),
        'source_kind': _choice('evidence.source_kind', raw.get('source_kind'),
                               EVIDENCE_SOURCE_KINDS),
        'captured_at': now,
        'content_hash': content_hash(excerpt),
        'field_bindings': sorted(set(bindings)),
    }


def normalize_revision_fields(fields: Any) -> dict:
    if not isinstance(fields, dict):
        raise SchemaError('fields: must be an object of field name to text')
    if len(fields) > MAX_FIELDS_PER_REVISION:
        raise SchemaError(f'fields: more than {MAX_FIELDS_PER_REVISION} fields')
    out = {}
    for k, v in fields.items():
        if not isinstance(k, str) or not _FIELD_NAME_RE.fullmatch(k):
            raise SchemaError('fields: invalid field name')
        out[k] = _text(f'fields.{k}', v, MAX_FIELD_TEXT, required=False,
                       allow_newlines=True)
    return out


def normalize_rendered_text(text: Any) -> str:
    if not isinstance(text, str):
        raise SchemaError('rendered_text: must be text')
    if len(text.encode('utf-8')) > MAX_RENDERED_BYTES:
        raise SchemaError(f'rendered_text: larger than {MAX_RENDERED_BYTES} bytes')
    if '\x00' in text:
        raise SchemaError('rendered_text: contains a NUL byte')
    try:
        require_no_credential('rendered_text', text)
    except PublicDataRefused as e:
        raise SchemaError(str(e))
    return text


def normalize_template_version(value: Any) -> str:
    if not isinstance(value, str) or not _VERSION_RE.fullmatch(value):
        raise SchemaError('template_version: invalid version string')
    return value


def normalize_reason(name: str, value: Any) -> str:
    return _text(name, value, MAX_REASON, required=False)


def normalize_findings(findings: Any) -> list:
    if not isinstance(findings, list) or len(findings) > MAX_FINDINGS:
        raise SchemaError(f'findings: must be a list of at most {MAX_FINDINGS}')
    out = []
    for f in findings:
        if not isinstance(f, dict):
            raise SchemaError('findings: each finding must be an object')
        span = f.get('span')
        if (not isinstance(span, list) or len(span) != 2
                or not all(isinstance(n, int) and n >= 0 for n in span)
                or span[0] > span[1]):
            raise SchemaError('findings.span: must be [start, end] offsets')
        field = f.get('field')
        if not isinstance(field, str) or not _FIELD_NAME_RE.fullmatch(field):
            raise SchemaError('findings.field: invalid field name')
        rule = f.get('rule_id')
        if not isinstance(rule, str) or not _VERSION_RE.fullmatch(rule):
            raise SchemaError('findings.rule_id: invalid rule id')
        out.append({'rule_id': rule, 'field': field, 'span': [span[0], span[1]]})
    return out


def evidence_hash(evidence_records: list[dict]) -> str:
    """Hash over the exact evidence a revision cites: content hashes, source
    URLs, kinds and bindings. Any evidence change changes this value."""
    return canonical_hash(sorted(
        [e['id'], e['content_hash'], e['source_url'], e['source_kind'],
         e['field_bindings']] for e in evidence_records))


# -- whole-record validation (what was read back from disk) -------------------

def _req_keys(name: str, obj: Any, keys: tuple[str, ...]) -> dict:
    if not isinstance(obj, dict):
        raise SchemaError(f'{name}: must be an object')
    missing = [k for k in keys if k not in obj]
    if missing:
        raise SchemaError(f'{name}: missing {", ".join(missing)}')
    return obj


CHECKLIST_KEYS = ('schema_version', 'id', 'project_id', 'campaign_id', 'channel_id',
                  'revision', 'artifacts', 'entry_ids', 'created_at', 'updated_at')
ENTRY_KEYS = ('id', 'checklist_id', 'surface_id', 'artifact_id', 'applicability',
              'applicability_reason', 'draft_revision', 'lifecycle', 'link_ids')
REVISION_KEYS = ('id', 'entry_id', 'number', 'fields', 'field_evidence',
                 'rendered_text', 'content_hash', 'template_version', 'created_at')
EVIDENCE_KEYS = ('id', 'excerpt', 'source_url', 'source_kind', 'captured_at',
                 'content_hash', 'field_bindings')
LINT_KEYS = ('id', 'revision_id', 'content_hash', 'evidence_hash', 'linter_version',
             'rules_version', 'template_version', 'created_at', 'result', 'findings')
SUBMISSION_KEYS = ('id', 'entry_id', 'revision_id', 'content_hash', 'outcome',
                   'created_at', 'reported_by', 'listing_url', 'reason', 'supersedes')


def validate_document(doc: Any, *, project_id: str, campaign_id: str) -> dict:
    """Validate a checklist document read from disk. Raises SchemaError; the
    store turns that into an observable corruption error, never an empty
    checklist."""
    _req_keys('document', doc, ('schema_version', 'kind', 'checklist', 'entries',
                                'revisions', 'evidence', 'lint_results',
                                'submission_events'))
    if doc['kind'] != RECORD_KIND:
        raise SchemaError('document.kind: not an agent-channel checklist')
    if doc['schema_version'] != SCHEMA_VERSION:
        raise SchemaError(f'document.schema_version: unsupported '
                          f'(this build reads {SCHEMA_VERSION})')
    ck = _req_keys('checklist', doc['checklist'], CHECKLIST_KEYS)
    require_own_id('checklist', ck['id'])
    require_own_id('channel', ck['channel_id'])
    if ck['project_id'] != project_id or ck['campaign_id'] != campaign_id:
        raise SchemaError('checklist: stored project/campaign does not match its path')
    if not isinstance(ck['revision'], int) or ck['revision'] < 1:
        raise SchemaError('checklist.revision: must be a positive integer')
    if not isinstance(ck['artifacts'], list) or not isinstance(ck['entry_ids'], list):
        raise SchemaError('checklist: artifacts and entry_ids must be lists')
    for name, keys, own in (('entries', ENTRY_KEYS, 'entry'),
                            ('revisions', REVISION_KEYS, 'revision'),
                            ('evidence', EVIDENCE_KEYS, 'evidence'),
                            ('lint_results', LINT_KEYS, 'lint')):
        table = doc[name]
        if not isinstance(table, dict):
            raise SchemaError(f'{name}: must be an object keyed by id')
        for rid, rec in table.items():
            _req_keys(f'{name}[{rid}]', rec, keys)
            require_own_id(own, rid)
            if rec['id'] != rid:
                raise SchemaError(f'{name}: record id does not match its key')
    for eid in ck['entry_ids']:
        if eid not in doc['entries']:
            raise SchemaError('checklist.entry_ids: references a missing entry')
        if doc['entries'][eid]['checklist_id'] != ck['id']:
            raise SchemaError('entries: entry belongs to another checklist')
    for rev in doc['revisions'].values():
        if rev['entry_id'] not in doc['entries']:
            raise SchemaError('revisions: revision references a missing entry')
        if content_hash(rev['rendered_text']) != rev['content_hash']:
            raise SchemaError('revisions: stored content hash does not match text')
    if not isinstance(doc['submission_events'], list):
        raise SchemaError('submission_events: must be a list')
    for ev in doc['submission_events']:
        _req_keys('submission_events[]', ev, SUBMISSION_KEYS)
        require_own_id('submission', ev['id'])
        if ev['reported_by'] != 'human':
            raise SchemaError('submission_events: reported_by must be human')
    return doc


def hash_ok(value: Any) -> bool:
    return isinstance(value, str) and bool(_HASH_RE.fullmatch(value))
