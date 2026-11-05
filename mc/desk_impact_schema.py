"""Record schemas, identities and limits for Desk channel impact (MC-1152 P1).

Spec: `docs/desk_v1/CHANNEL_IMPACT_SPEC.md` sections 4 and 5. This module is
pure: it validates and normalizes record SHAPES and mints IDs. It never reads
or writes a file (`mc/desk_impact_store.py` does) and never talks to a source
(`mc/desk_impact_sources/` does). Nothing imports it yet: P1 is dark.

Every limit below is published as a constant and refused, never truncated: the
backlog-note API once cut text at 2000 bytes in silence and lost weeks of
findings (CLAUDE.md, 2026-08-15). An over-limit value raises `SchemaError`; the
caller saves nothing. Messages name fields and rules, never a submitted value.

State lives in `data/desk_impact/<project_id>/`, one directory per project:

    definitions.json   {schema_version, kind, project_id, campaigns: {cid: {...}}}
    links.json         {schema_version, kind, project_id, links: {link_id: {...}}}
    coverage.json      {schema_version, kind, project_id, entries: {key: {...}}}
    snapshots.jsonl    one snapshot per line, append-only

MEASUREMENT RULES the validators enforce (spec section 4):

  * A successful, fully covered zero is `0`. Anything the source could not
    establish -- unsupported field, partial pagination, missing day, failed
    request, unknown attribution -- is `null` plus a reason. A snapshot with a
    value must therefore carry COMPLETE coverage.
  * Counts are finite and nonnegative. A negative figure is never stored: it is
    a broken counter (asset replaced, counter reset), and the adapter reports it
    as `null` with a reason instead of clamping it to a successful zero.
  * All stored times are UTC.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from mc.desk_agent_public_data import (
    PublicDataRefused, carries_credential, validate_public_url,
)

SCHEMA_VERSION = 1
KIND_DEFINITIONS = 'desk_impact_definitions'
KIND_LINKS = 'desk_impact_links'
KIND_COVERAGE = 'desk_impact_coverage'

# -- published limits (refuse, never truncate) --------------------------------
MAX_ID_LEN = 64
MAX_LABEL = 200
MAX_DETAIL = 500              # reasons, error detail
MAX_TARGET_BYTES = 4_096      # canonical JSON of a source-specific target
MAX_DIMENSIONS = 16
MAX_GAPS = 5_000              # recorded historical gaps per source/target
MAX_RECORD_BYTES = 8 * 1024 * 1024    # one JSON state file; refuse beyond
MAX_SNAPSHOT_LINE_BYTES = 64_000      # one snapshots.jsonl line

# -- stable vocabularies --------------------------------------------------------
COUNTER_KINDS = ('cumulative', 'interval', 'window')
SCOPES = ('repository', 'asset', 'site', 'campaign', 'channel', 'post')
GRANULARITIES = ('instant', 'hour', 'day', 'rolling_window')
MEDIUMS = ('social', 'email', 'agent')
ERROR_CODES = ('auth', 'permission', 'not_found', 'rate_limited', 'timeout',
               'partial', 'malformed', 'unavailable', 'unsupported', 'other')
UTM_KEYS = ('utm_source', 'utm_medium', 'utm_campaign', 'utm_content')

ID_PREFIX = {'definition': 'imdf', 'link': 'imlk', 'snapshot': 'imsn'}

# External IDs (project, campaign, account, connection) and our own minted IDs
# share one shape. No dot, slash, backslash, colon or leading separator, so a
# validated id can be joined onto a path without escaping it.
_ID_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$')
_SLUG_RE = re.compile(r'^[a-z][a-z0-9_]{0,63}$')          # metric, unit, source ids
_DIM_KEY_RE = re.compile(r'^[a-z][a-z0-9_]{0,63}$')
_UTM_VALUE_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._~-]{0,199}$')
_KEY_RE = re.compile(r'^sha256:[0-9a-f]{64}$')


class SchemaError(ValueError):
    """A record or input does not match the channel-impact schema."""


# -- ids ---------------------------------------------------------------------------

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


def require_slug(name: str, value: Any) -> str:
    if not isinstance(value, str) or not _SLUG_RE.fullmatch(value):
        raise SchemaError(f'{name}: lowercase letters, digits and _ only, '
                          f'starting with a letter, at most 64 characters')
    return value


# -- scalars -------------------------------------------------------------------------

def canonical_hash(obj: Any) -> str:
    """Stable hash of a JSON-able value (sorted keys, no whitespace)."""
    blob = json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    return 'sha256:' + hashlib.sha256(blob.encode('utf-8')).hexdigest()


def text(name: str, value: Any, limit: int, *, required: bool = True) -> str:
    if value is None:
        value = ''
    if not isinstance(value, str):
        raise SchemaError(f'{name}: must be text')
    if required and not value.strip():
        raise SchemaError(f'{name}: required')
    if len(value) > limit:
        raise SchemaError(f'{name}: longer than {limit} characters')
    if '\n' in value or '\r' in value:
        raise SchemaError(f'{name}: must be a single line')
    if '\x00' in value:
        raise SchemaError(f'{name}: contains a NUL byte')
    if carries_credential(value):
        raise SchemaError(f'{name}: looks like a credential; credentials are '
                          f'never stored in channel-impact records')
    return value


def choice(name: str, value: Any, allowed: tuple[str, ...]) -> str:
    if value not in allowed:
        raise SchemaError(f'{name}: must be one of {", ".join(allowed)}')
    return value


def parse_utc(name: str, value: Any) -> datetime:
    """Strict: an ISO-8601 string with an explicit UTC offset (`Z` or +00:00)."""
    if not isinstance(value, str):
        raise SchemaError(f'{name}: must be an ISO-8601 UTC timestamp')
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        raise SchemaError(f'{name}: not an ISO-8601 timestamp')
    if dt.tzinfo is None or dt.utcoffset() != timedelta(0):
        raise SchemaError(f'{name}: must be UTC (end with Z or +00:00)')
    return dt.astimezone(timezone.utc)


def utc(name: str, value: Any) -> str:
    parse_utc(name, value)
    return value


def utc_or_none(name: str, value: Any) -> str | None:
    return None if value is None else utc(name, value)


def count(name: str, value: Any) -> int | float:
    """A finite, nonnegative number (bool is not a number here)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SchemaError(f'{name}: must be a number')
    if isinstance(value, float) and not math.isfinite(value):
        raise SchemaError(f'{name}: must be finite')
    if value < 0:
        raise SchemaError(f'{name}: must not be negative (a decreasing counter is '
                          f'null with a reason, never a clamped zero)')
    return value


def require_int(name: str, value: Any, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise SchemaError(f'{name}: required integer >= {minimum}')
    return value


def scalar_map(name: str, value: Any, *, max_items: int = MAX_DIMENSIONS) -> dict:
    """A flat {key: short text/int/bool/None} map (attribution dimensions)."""
    if not isinstance(value, dict):
        raise SchemaError(f'{name}: must be an object')
    if len(value) > max_items:
        raise SchemaError(f'{name}: more than {max_items} entries')
    out: dict = {}
    for k, v in value.items():
        if not isinstance(k, str) or not _DIM_KEY_RE.fullmatch(k):
            raise SchemaError(f'{name}: invalid key')
        if isinstance(v, str):
            text(f'{name}.{k}', v, MAX_LABEL, required=False)
        elif v is not None and not isinstance(v, (bool, int)):
            raise SchemaError(f'{name}.{k}: must be text, integer, boolean or null')
        out[k] = v
    return out


def target(name: str, value: Any) -> dict:
    """A source-specific target: a small JSON object of scalars and lists of
    text. The SOURCE decides what its fields mean (`validate_definition`); this
    only bounds the shape and keeps credentials out of it."""
    if not isinstance(value, dict) or not value:
        raise SchemaError(f'{name}: required object')
    out: dict = {}
    for k, v in value.items():
        if not isinstance(k, str) or not _DIM_KEY_RE.fullmatch(k):
            raise SchemaError(f'{name}: invalid key')
        if isinstance(v, str):
            text(f'{name}.{k}', v, MAX_LABEL, required=False)
        elif isinstance(v, list):
            if len(v) > MAX_DIMENSIONS:
                raise SchemaError(f'{name}.{k}: more than {MAX_DIMENSIONS} items')
            for item in v:
                if not isinstance(item, str):
                    raise SchemaError(f'{name}.{k}: items must be text')
                text(f'{name}.{k}[]', item, MAX_LABEL, required=False)
        elif v is not None and not isinstance(v, (bool, int)):
            raise SchemaError(f'{name}.{k}: must be text, integer, boolean, '
                              f'list of text or null')
        out[k] = v
    size = len(json.dumps(out, sort_keys=True).encode('utf-8'))
    if size > MAX_TARGET_BYTES:
        raise SchemaError(f'{name}: {size} bytes; limit is {MAX_TARGET_BYTES}')
    return out


# -- conversion definition ---------------------------------------------------------------

DEFINITION_SPEC_FIELDS = ('label', 'source_id', 'connection_id', 'metric', 'unit',
                          'target', 'effective_at')


def validate_definition_spec(spec: Any) -> dict:
    """The caller-supplied part of a definition revision. `effective_at` is
    optional (defaults to the revision's creation time). Unknown fields are
    refused, not ignored: a second metric smuggled in as `metrics` must not
    pass for the single selected one."""
    if not isinstance(spec, dict):
        raise SchemaError('definition: must be an object')
    unknown = sorted(set(spec) - set(DEFINITION_SPEC_FIELDS))
    if unknown:
        raise SchemaError(f'definition: unknown field(s): {", ".join(unknown)}')
    return {
        'label': text('label', spec.get('label'), MAX_LABEL),
        'source_id': require_slug('source_id', spec.get('source_id')),
        'connection_id': require_id('connection_id', spec.get('connection_id')),
        'metric': require_slug('metric', spec.get('metric')),
        'unit': require_slug('unit', spec.get('unit')),
        'target': target('target', spec.get('target')),
        'effective_at': utc_or_none('effective_at', spec.get('effective_at')),
    }


def validate_definition_record(rec: Any, *, campaign_id: str | None = None) -> dict:
    if not isinstance(rec, dict):
        raise SchemaError('definition revision: must be an object')
    require_own_id('definition', rec.get('id'))
    require_int('revision', rec.get('revision'), minimum=1)
    require_id('campaign_id', rec.get('campaign_id'))
    if campaign_id is not None and rec['campaign_id'] != campaign_id:
        raise SchemaError('definition revision: campaign_id mismatch')
    body = validate_definition_spec({k: rec.get(k) for k in DEFINITION_SPEC_FIELDS})
    if body['effective_at'] is None:
        raise SchemaError('effective_at: required on a stored revision')
    utc('created_at', rec.get('created_at'))
    utc_or_none('retired_at', rec.get('retired_at'))
    return rec


def definition_content(spec: dict) -> dict:
    """What makes two specs the same measurement: everything but the effective
    time. Identical content is an idempotent save, not a new series."""
    return {k: spec[k] for k in DEFINITION_SPEC_FIELDS if k != 'effective_at'}


def validate_definitions_document(doc: Any, *, project_id: str) -> dict:
    if not isinstance(doc, dict):
        raise SchemaError('definitions: not an object')
    if doc.get('schema_version') != SCHEMA_VERSION:
        raise SchemaError(f'definitions: unsupported schema_version '
                          f'{doc.get("schema_version")!r}')
    if doc.get('kind') != KIND_DEFINITIONS:
        raise SchemaError('definitions: wrong record kind')
    if doc.get('project_id') != project_id:
        raise SchemaError('definitions: project_id mismatch')
    camps = doc.get('campaigns')
    if not isinstance(camps, dict):
        raise SchemaError('definitions: campaigns must be an object')
    for cid, entry in camps.items():
        require_id('campaign_id', cid)
        if not isinstance(entry, dict):
            raise SchemaError('definitions: campaign entry must be an object')
        did = require_own_id('definition', entry.get('definition_id'))
        revs = entry.get('revisions')
        if not isinstance(revs, list) or not revs:
            raise SchemaError('definitions: revisions must be a non-empty list')
        for i, rec in enumerate(revs, 1):
            validate_definition_record(rec, campaign_id=cid)
            if rec['id'] != did or rec['revision'] != i:
                raise SchemaError('definitions: revisions must be numbered 1..n '
                                  'under one definition id')
        active = entry.get('active_revision')
        if active is not None:
            require_int('active_revision', active, minimum=1)
            if active > len(revs) or revs[active - 1].get('retired_at'):
                raise SchemaError('definitions: active_revision is not a live revision')
        live = [r for r in revs if not r.get('retired_at')]
        if len(live) > 1 or (live and active != live[0]['revision']) or (not live and active):
            raise SchemaError('definitions: exactly one active revision, or none')
    return doc


# -- link manifest -----------------------------------------------------------------------

LINK_FIELDS = ('link_id', 'campaign_id', 'account_id', 'post_id', 'version_id',
               'original_destination', 'final_url', 'utm', 'created_at')


def validate_utm(utm: Any) -> dict:
    if not isinstance(utm, dict) or set(utm) != set(UTM_KEYS):
        raise SchemaError(f'utm: exactly {", ".join(UTM_KEYS)} are required')
    for k in UTM_KEYS:
        v = utm[k]
        if not isinstance(v, str) or not _UTM_VALUE_RE.fullmatch(v):
            raise SchemaError(f'{k}: URL-safe text, 1-200 characters')
    choice('utm_medium', utm['utm_medium'], MEDIUMS)
    return dict(utm)


def validate_link_record(rec: Any) -> dict:
    """One manifest row. It binds the stable identifiers (spec section 6): the
    campaign id rides in `utm_campaign`, the link id in `utm_content`, and the
    stored `final_url` must actually carry that exact map -- a manifest that
    disagrees with the URL the publisher receives would reverse-look-up wrong."""
    if not isinstance(rec, dict):
        raise SchemaError('link: must be an object')
    unknown = sorted(set(rec) - set(LINK_FIELDS))
    if unknown:
        raise SchemaError(f'link: unknown field(s): {", ".join(unknown)}')
    link_id = require_own_id('link', rec.get('link_id'))
    cid = require_id('campaign_id', rec.get('campaign_id'))
    require_id('account_id', rec.get('account_id'))
    require_id('post_id', rec.get('post_id'))
    require_id('version_id', rec.get('version_id'))
    utc('created_at', rec.get('created_at'))
    for name in ('original_destination', 'final_url'):
        url = rec.get(name)
        if not isinstance(url, str):
            raise SchemaError(f'{name}: a URL is required')
        try:
            validate_public_url(name, url)
        except PublicDataRefused as e:
            raise SchemaError(str(e))
    utm = validate_utm(rec.get('utm'))
    if utm['utm_campaign'] != cid:
        raise SchemaError('utm_campaign: must be the campaign id')
    if utm['utm_content'] != link_id:
        raise SchemaError('utm_content: must be the link id')
    try:
        pairs = parse_qsl(urlsplit(rec['final_url']).query, keep_blank_values=True)
    except ValueError:
        raise SchemaError('final_url: query could not be parsed')
    carried = {k: [v for kk, v in pairs if kk == k] for k in UTM_KEYS}
    if any(carried[k] != [utm[k]] for k in UTM_KEYS):
        raise SchemaError('final_url: does not carry exactly the stored UTM map')
    return rec


def validate_links_document(doc: Any, *, project_id: str) -> dict:
    if not isinstance(doc, dict):
        raise SchemaError('links: not an object')
    if doc.get('schema_version') != SCHEMA_VERSION or doc.get('kind') != KIND_LINKS:
        raise SchemaError('links: wrong schema_version or record kind')
    if doc.get('project_id') != project_id:
        raise SchemaError('links: project_id mismatch')
    links = doc.get('links')
    if not isinstance(links, dict):
        raise SchemaError('links: links must be an object')
    for lid, rec in links.items():
        validate_link_record(rec)
        if rec['link_id'] != lid:
            raise SchemaError('links: key does not match link_id')
    return doc


# -- snapshots ---------------------------------------------------------------------------

def validate_window(w: Any) -> dict:
    """Half-open [start, end). A cumulative counter has no start: it is the
    counter AS OF `end`, so `start` is null and each sample is its own window."""
    if not isinstance(w, dict) or set(w) != {'start', 'end', 'granularity', 'timezone'}:
        raise SchemaError('window: requires start, end, granularity, timezone')
    start = utc_or_none('window.start', w['start'])
    end = utc('window.end', w['end'])
    if start is not None and parse_utc('window.start', start) >= parse_utc('window.end', end):
        raise SchemaError('window: start must be before end (half-open [start, end))')
    choice('window.granularity', w['granularity'], GRANULARITIES)
    text('window.timezone', w['timezone'], 64)
    return {'start': start, 'end': end, 'granularity': w['granularity'],
            'timezone': w['timezone']}


def validate_coverage(c: Any) -> dict:
    if not isinstance(c, dict) or set(c) != {'complete', 'gaps', 'incomplete_pages'}:
        raise SchemaError('coverage: requires complete, gaps, incomplete_pages')
    if not isinstance(c['complete'], bool) or not isinstance(c['incomplete_pages'], bool):
        raise SchemaError('coverage: complete and incomplete_pages must be boolean')
    gaps = c['gaps']
    if not isinstance(gaps, list) or len(gaps) > MAX_GAPS:
        raise SchemaError(f'coverage.gaps: list of at most {MAX_GAPS}')
    for g in gaps:
        text('coverage.gaps[]', g, MAX_LABEL)
    if c['complete'] and (gaps or c['incomplete_pages']):
        raise SchemaError('coverage: cannot be complete with gaps or incomplete pages')
    return {'complete': c['complete'], 'gaps': list(gaps),
            'incomplete_pages': c['incomplete_pages']}


def validate_provenance(p: Any) -> dict:
    if not isinstance(p, dict) or set(p) != {'adapter', 'request_count'}:
        raise SchemaError('provenance: requires adapter and request_count')
    require_slug('provenance.adapter', p['adapter'])
    require_int('provenance.request_count', p['request_count'])
    return dict(p)


SNAPSHOT_INPUT_FIELDS = (
    'source_id', 'connection_id', 'target', 'definition_id', 'definition_revision',
    'window', 'observed_at', 'effective_at', 'metric', 'unit', 'value',
    'null_reason', 'counter_kind', 'scope', 'attribution', 'coverage', 'provenance')


_SNAPSHOT_STORE_FIELDS = frozenset(('schema_version', 'snapshot_id', 'key', 'recorded_at'))


def validate_snapshot_input(obs: Any) -> dict:
    """An observation as an adapter's `normalize` returns it. The store adds
    `schema_version`, `snapshot_id`, `key` and `recorded_at`."""
    if not isinstance(obs, dict):
        raise SchemaError('snapshot: must be an object')
    unknown = sorted(set(obs) - set(SNAPSHOT_INPUT_FIELDS))
    if unknown:
        raise SchemaError(f'snapshot: unknown field(s): {", ".join(unknown)}')
    missing = [f for f in SNAPSHOT_INPUT_FIELDS if f not in obs]
    if missing:
        raise SchemaError(f'snapshot: missing field(s): {", ".join(missing)}')
    out: dict = {
        'source_id': require_slug('source_id', obs['source_id']),
        'connection_id': require_id('connection_id', obs['connection_id']),
        'target': target('target', obs['target']),
        'window': validate_window(obs['window']),
        'observed_at': utc('observed_at', obs['observed_at']),
        'effective_at': utc('effective_at', obs['effective_at']),
        'metric': require_slug('metric', obs['metric']),
        'unit': require_slug('unit', obs['unit']),
        'counter_kind': choice('counter_kind', obs['counter_kind'], COUNTER_KINDS),
        'scope': choice('scope', obs['scope'], SCOPES),
        'coverage': validate_coverage(obs['coverage']),
        'provenance': validate_provenance(obs['provenance']),
    }
    did, rev = obs['definition_id'], obs['definition_revision']
    if (did is None) != (rev is None):
        raise SchemaError('definition_id and definition_revision go together')
    out['definition_id'] = None if did is None else require_own_id('definition', did)
    out['definition_revision'] = None if rev is None else require_int(
        'definition_revision', rev, minimum=1)
    attr = obs['attribution']
    if not isinstance(attr, dict) or set(attr) != {'supported', 'dimensions'} \
            or not isinstance(attr['supported'], bool):
        raise SchemaError('attribution: requires supported (boolean) and dimensions')
    dims = scalar_map('attribution.dimensions', attr['dimensions'])
    if dims and not attr['supported']:
        raise SchemaError('attribution: dimensions given for an unsupported attribution')
    out['attribution'] = {'supported': attr['supported'], 'dimensions': dims}
    value, reason = obs['value'], obs['null_reason']
    if value is None:
        out['value'] = None
        out['null_reason'] = text('null_reason', reason, MAX_DETAIL)
    else:
        out['value'] = count('value', value)
        if reason is not None:
            raise SchemaError('null_reason: only allowed when value is null')
        out['null_reason'] = None
        if not out['coverage']['complete']:
            raise SchemaError('value: a number requires complete coverage; '
                              'partial data is null with a reason')
    return out


def snapshot_key(snap: dict) -> str:
    """Identity of a measurement for retry idempotency and corrections (spec
    section 5): source/connection/target + metric + exact window + attribution
    dimensions + definition revision. Value, observation time and coverage are
    deliberately NOT part of it."""
    return canonical_hash({
        'source_id': snap['source_id'], 'connection_id': snap['connection_id'],
        'target': snap['target'], 'metric': snap['metric'], 'window': snap['window'],
        'dimensions': snap['attribution']['dimensions'],
        'definition_id': snap['definition_id'],
        'definition_revision': snap['definition_revision'],
    })


def snapshot_content_hash(snap: dict) -> str:
    """Same key + same content = a retry (no-op); same key + different content
    = a correction (new row, older one kept as history)."""
    return canonical_hash({
        'key': snapshot_key(snap), 'value': snap['value'],
        'null_reason': snap['null_reason'], 'coverage': snap['coverage'],
        'observed_at': snap['observed_at']})


def validate_snapshot_record(rec: Any) -> dict:
    """A stored snapshot row: the input shape plus store-minted fields, and the
    stored `key` must recompute -- corruption inside a file is not swallowed."""
    if not isinstance(rec, dict):
        raise SchemaError('snapshot row: must be an object')
    if rec.get('schema_version') != SCHEMA_VERSION:
        raise SchemaError('snapshot row: unsupported schema_version')
    require_own_id('snapshot', rec.get('snapshot_id'))
    utc('recorded_at', rec.get('recorded_at'))
    if set(rec) != set(SNAPSHOT_INPUT_FIELDS) | _SNAPSHOT_STORE_FIELDS:
        raise SchemaError('snapshot row: wrong fields')
    body = validate_snapshot_input({k: rec[k] for k in SNAPSHOT_INPUT_FIELDS})
    if body != {k: rec[k] for k in SNAPSHOT_INPUT_FIELDS}:
        raise SchemaError('snapshot row: not in normalized form')
    key = rec.get('key')
    if not isinstance(key, str) or not _KEY_RE.fullmatch(key) or key != snapshot_key(rec):
        raise SchemaError('snapshot row: key does not match its identity fields')
    return rec


# -- coverage / read-attempt status -------------------------------------------------------

def coverage_key(source_id: str, connection_id: str, target_: dict) -> str:
    return canonical_hash({'source_id': source_id, 'connection_id': connection_id,
                           'target': target_})


COVERAGE_ENTRY_FIELDS = (
    'source_id', 'connection_id', 'target', 'status', 'last_attempt_at',
    'last_success_at', 'error_code', 'error_detail', 'retry_after', 'gaps',
    'incomplete_pages')


def validate_coverage_entry(e: Any) -> dict:
    if not isinstance(e, dict) or set(e) != set(COVERAGE_ENTRY_FIELDS):
        raise SchemaError('coverage entry: wrong fields')
    require_slug('source_id', e['source_id'])
    require_id('connection_id', e['connection_id'])
    target('target', e['target'])
    choice('status', e['status'], ('ok', 'error'))
    utc('last_attempt_at', e['last_attempt_at'])
    utc_or_none('last_success_at', e['last_success_at'])
    utc_or_none('retry_after', e['retry_after'])
    if e['status'] == 'ok':
        if e['error_code'] is not None or e['error_detail'] is not None:
            raise SchemaError('coverage entry: an ok status carries no error')
        if e['last_success_at'] != e['last_attempt_at']:
            raise SchemaError('coverage entry: ok means the last attempt succeeded')
    else:
        choice('error_code', e['error_code'], ERROR_CODES)
        text('error_detail', e['error_detail'], MAX_DETAIL, required=False)
    gaps = e['gaps']
    if not isinstance(gaps, list) or len(gaps) > MAX_GAPS:
        raise SchemaError(f'gaps: list of at most {MAX_GAPS}')
    for g in gaps:
        if not isinstance(g, dict) or set(g) != {'start', 'end', 'reason'}:
            raise SchemaError('gaps[]: requires start, end, reason')
        utc('gaps[].start', g['start'])
        utc('gaps[].end', g['end'])
        text('gaps[].reason', g['reason'], MAX_DETAIL)
    if not isinstance(e['incomplete_pages'], bool):
        raise SchemaError('incomplete_pages: must be boolean')
    return e


def validate_coverage_document(doc: Any, *, project_id: str) -> dict:
    if not isinstance(doc, dict):
        raise SchemaError('coverage: not an object')
    if doc.get('schema_version') != SCHEMA_VERSION or doc.get('kind') != KIND_COVERAGE:
        raise SchemaError('coverage: wrong schema_version or record kind')
    if doc.get('project_id') != project_id:
        raise SchemaError('coverage: project_id mismatch')
    entries = doc.get('entries')
    if not isinstance(entries, dict):
        raise SchemaError('coverage: entries must be an object')
    for key, e in entries.items():
        validate_coverage_entry(e)
        if key != coverage_key(e['source_id'], e['connection_id'], e['target']):
            raise SchemaError('coverage: key does not match its source/target')
    return doc
