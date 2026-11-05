"""Persistence for Desk channel impact (MC-1152 P1, DARK).

Spec: `docs/desk_v1/CHANNEL_IMPACT_SPEC.md` section 5 and section 12 row P1.
Shapes and limits live in `mc/desk_impact_schema.py`; this module owns ownership
resolution, locking, revision conflicts, crash-safe writes and append-only
history. Nothing imports it yet: no route, no UI, no scheduler, no network.

WHERE THE STATE LIVES: `data/desk_impact/<project_id>/` -- a SIBLING of DATA_DIR
(`data/projects/`), never under it. `load_projects()` parses every `*.json` in
DATA_DIR as a project record, so a state file written there would become a
malformed "project" and 500 both restart endpoints (CLAUDE.md LOAD-BEARING
DATA_DIR rule). The constructor refuses a root at or inside DATA_DIR rather
than trusting every future caller to remember that. `data/desk_impact/` is
gitignored and absent from `installer/build-macos.spec`.

    definitions.json  conversion definition + every revision, keyed by campaign
    links.json        tracked-link manifest (P2 writes it)
    coverage.json     last attempt / last success / safe error per source+target
    snapshots.jsonl   append-only observations; a correction adds a row

INVARIANTS, each with a test in tests/test_desk_impact_store.py:

  1. IDs are validated BEFORE a path is built, and the built path is checked
     LEXICALLY (see `_path`), so `..`, separators, drive letters and NULs never
     reach the filesystem.
  2. Ownership is resolved server-side on every call that names a campaign,
     reads included: the campaign must exist and its `project_id` must equal the
     caller's. A wrong-project campaign reads exactly like an unknown one. A
     connection must also belong to the caller's project.
  3. A definition edit carries the head revision the caller last saw; a stale
     one is a `RevisionConflict`, never a lost write.
  4. A corrupt or schema-invalid record raises `StoreCorrupt` and is logged; it
     is NEVER replaced by a fresh empty one.
  5. History is append-only with no retention cap: definition revisions, link
     rows and snapshot rows are only ever added. Over-limit input is refused
     whole (`SchemaError`), never truncated. There is no delete or purge method,
     and campaign archive/delete gains no implicit impact-data purge.
  6. A failed read attempt is recorded in coverage and never touches a snapshot.
  7. A truncated final `snapshots.jsonl` line (crash mid-append) is reported and
     excluded on read, and set aside before the next append; corruption anywhere
     else raises.

SNAPSHOT RETRY vs CORRECTION (spec section 5 leaves the discriminator open;
this is the rule built, `schema.snapshot_key` / `snapshot_content_hash`): the
KEY is source/connection/target + metric + exact window + attribution
dimensions + definition id/revision. A cumulative counter's window ends at its
`as_of` instant, so each sample is its own key. Same key and same content
(value, null reason, coverage, observed_at) is a retry and appends nothing.
Same key with different content appends a row; the effective value for the key
is the row with the latest `observed_at` (ties: later append wins) and every
earlier row stays readable as history.
"""
from __future__ import annotations

import contextlib
import copy
import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Generator, Mapping, Optional

from mc.atomic_json import read_text_with_retry, write_json_atomic
from mc.core import _log, now_iso, path_is_within
from mc import desk_impact_schema as S
from mc.desk_impact_sources import base as src_base
from mc.desk_impact_sources.registry import REGISTRY, AdapterRegistry, UnknownSource

CampaignResolver = Callable[[str], Optional[dict]]
ConnectionResolver = Callable[[str], Optional[Mapping[str, Any]]]

DEFINITIONS_FILE = 'definitions.json'
LINKS_FILE = 'links.json'
COVERAGE_FILE = 'coverage.json'
SNAPSHOTS_FILE = 'snapshots.jsonl'
_LOCK_TIMEOUT_S = 10.0
_LOCK_STALE_S = 30.0
_LOCK_POLL_S = 0.005
_DUMPS_KW: dict[str, Any] = {'indent': 1, 'ensure_ascii': False, 'sort_keys': True}


# -- errors ---------------------------------------------------------------------------

class ImpactError(Exception):
    """Base class. Messages carry ids and rule names, never submitted values."""


class NotFound(ImpactError):
    pass


class OwnershipError(NotFound):
    """Campaign or connection belongs to another project. A subclass of NotFound
    and worded the same, so a caller cannot probe which project owns an id."""


class RevisionConflict(ImpactError):
    def __init__(self, expected: Any, current: int):
        self.expected = expected
        self.current = current
        super().__init__(f'definition changed since revision {expected!r}; '
                         f'current head revision is {current}')


class LinkConflict(ImpactError):
    pass


class StoreCorrupt(ImpactError):
    """The record on disk cannot be read as valid. Left in place."""


class StoreIOError(ImpactError):
    pass


# -- default resolvers ------------------------------------------------------------------

def desk_campaign_resolver(campaign_id: str) -> dict | None:
    """Look a campaign up in the Desk store (`data/desk.json`). Lazy import so
    this module stays importable without the Desk wired."""
    from mc import desk as _desk
    with _desk._store_lock:
        camp = _desk._read_store()['campaigns'].get(campaign_id)
    return copy.deepcopy(camp) if isinstance(camp, dict) else None


def no_connection_resolver(connection_id: str) -> None:
    """P1 default: there is no connection source wired, so every connection is
    unknown and no definition can be saved against it. P3 passes the real
    resolver (the existing Desk connection system); never invent one here."""
    return None


def default_root(base_dir: Path) -> Path:
    """`<base>/data/desk_impact` -- `base` is the data root server.py hands
    `desk.STORE_PATH` (`_DATA_ROOT`)."""
    return Path(base_dir) / 'data' / 'desk_impact'


@dataclass(frozen=True)
class SnapshotRead:
    records: tuple[dict, ...]
    truncated_tail: bool = False       # a partial final line was excluded


# -- the store --------------------------------------------------------------------------

class ImpactStore:
    def __init__(self, root: Path, *, campaign_resolver: CampaignResolver | None = None,
                 connection_resolver: ConnectionResolver | None = None,
                 registry: AdapterRegistry | None = None,
                 data_dir: Path | None = None, clock: Callable[[], str] = now_iso):
        root = Path(root)
        if data_dir is not None and (path_is_within(root, data_dir)
                                     or path_is_within(data_dir, root)):
            raise ValueError('channel-impact store root must not overlap DATA_DIR '
                             '(data/projects); load_projects() would read it')
        self.root = root
        self._resolve_campaign = campaign_resolver or desk_campaign_resolver
        self._resolve_connection = connection_resolver or no_connection_resolver
        self._registry = registry if registry is not None else REGISTRY
        self._clock = clock
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    # -- identity, ownership, paths --------------------------------------------------

    def _owned(self, project_id: Any, campaign_id: Any) -> tuple[str, str, dict]:
        pid = S.require_id('project_id', project_id)
        cid = S.require_id('campaign_id', campaign_id)
        camp = self._resolve_campaign(cid)
        if not isinstance(camp, dict):
            raise NotFound('campaign not found in this project')
        if camp.get('project_id') != pid:
            raise OwnershipError('campaign not found in this project')
        return pid, cid, camp

    def _connection(self, pid: str, connection_id: str) -> Mapping[str, Any]:
        conn = self._resolve_connection(connection_id)
        if not isinstance(conn, Mapping):
            raise NotFound('connection not found in this project')
        if conn.get('project_id') != pid:
            raise OwnershipError('connection not found in this project')
        return conn

    def _path(self, pid: str, name: str) -> Path:
        path = self.root / pid / name
        # Belt and braces after require_id, LEXICAL on purpose. Measured
        # 2026-11-04 (Python 3.14, Windows): Path.resolve() on a path whose
        # directory a sibling thread is creating returns it with a `\\?\` long-path
        # prefix, so a resolve()-based containment check said "outside" and
        # refused 3 of 12 concurrent valid opens.
        if path.relative_to(self.root).parts != (pid, name):
            raise S.SchemaError('resolved path escapes the channel-impact root')
        return path

    # -- locking ---------------------------------------------------------------------

    @contextlib.contextmanager
    def _locked(self, path: Path) -> Generator[None, None, None]:
        """In-process lock per file plus an O_EXCL lockfile, so the two MC
        processes alive during a restart overlap (mc/atomic_json.py) cannot
        interleave a read-modify-write either. Same shape as
        the MC-1151 agent-channel store lock."""
        key = str(path)
        with self._locks_guard:
            lock = self._locks.setdefault(key, threading.Lock())
        with lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            lock_path = path.with_name(path.name + '.lock')
            deadline = time.monotonic() + _LOCK_TIMEOUT_S
            fd = None
            while fd is None:
                try:
                    fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                except FileExistsError:
                    try:
                        stale = (time.time() - lock_path.stat().st_mtime) > _LOCK_STALE_S
                    except OSError:
                        stale = False   # vanished between open and stat: retry
                    if stale:
                        _log(f'[desk-impact] breaking stale lock {lock_path}', level='warn')
                        with contextlib.suppress(OSError):
                            lock_path.unlink()
                        continue
                    if time.monotonic() > deadline:
                        raise StoreIOError('channel-impact record is locked by another writer')
                    time.sleep(_LOCK_POLL_S)
            try:
                os.close(fd)
                yield
            finally:
                try:
                    lock_path.unlink()
                except OSError as e:
                    _log(f'[desk-impact] lock release failed for {lock_path}: {e}',
                         level='warn')

    # -- raw JSON state files ---------------------------------------------------------

    def _read_json(self, path: Path, validate: Callable[[Any], Any]) -> dict | None:
        if not path.exists():
            return None
        try:
            if path.stat().st_size > S.MAX_RECORD_BYTES:
                raise StoreCorrupt('channel-impact record exceeds the size limit')
            text = read_text_with_retry(path)
        except StoreCorrupt:
            _log(f'[desk-impact] record over size limit, left in place: {path}',
                 level='error')
            raise
        except OSError as e:
            _log(f'[desk-impact] read failed for {path}: {type(e).__name__}: {e}',
                 level='error')
            raise StoreIOError('channel-impact record could not be read')
        try:
            doc = json.loads(text)
        except ValueError as e:
            _log(f'[desk-impact] corrupt record (JSON), left in place: {path}: '
                 f'{type(e).__name__} at char {getattr(e, "pos", "?")}', level='error')
            raise StoreCorrupt('channel-impact record is not valid JSON')
        try:
            return validate(doc)
        except S.SchemaError as e:
            _log(f'[desk-impact] corrupt record (schema), left in place: {path}: {e}',
                 level='error')
            raise StoreCorrupt(f'channel-impact record failed validation: {e}')

    def _write_json(self, path: Path, doc: dict, validate: Callable[[Any], Any]) -> None:
        validate(doc)
        size = len(json.dumps(doc, **_DUMPS_KW).encode('utf-8'))
        if size > S.MAX_RECORD_BYTES:
            raise S.SchemaError(f'record would be {size} bytes; limit is '
                                f'{S.MAX_RECORD_BYTES}. Nothing was saved.')
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            write_json_atomic(path, doc, **_DUMPS_KW)
        except OSError as e:
            _log(f'[desk-impact] write failed for {path}: {type(e).__name__}: {e}',
                 level='error')
            raise StoreIOError('channel-impact record could not be saved')

    # -- campaigns --------------------------------------------------------------------

    def get_campaign(self, project_id: Any, campaign_id: Any) -> dict:
        """The owned campaign, resolved server-side (copy). The Goal binding
        reads the manual goal through this; nothing here writes it."""
        return copy.deepcopy(self._owned(project_id, campaign_id)[2])

    # -- definitions --------------------------------------------------------------------

    def _definitions_io(self, pid: str):
        path = self._path(pid, DEFINITIONS_FILE)
        return (path,
                lambda d: S.validate_definitions_document(d, project_id=pid))

    def _new_definitions_doc(self, pid: str) -> dict:
        return {'schema_version': S.SCHEMA_VERSION, 'kind': S.KIND_DEFINITIONS,
                'project_id': pid, 'campaigns': {}}

    def get_definitions(self, project_id: Any, campaign_id: Any) -> dict:
        """Read only. `active` is None when the campaign has no definition (a
        valid state). `revisions` lists every revision, oldest first."""
        pid, cid, _camp = self._owned(project_id, campaign_id)
        path, validate = self._definitions_io(pid)
        doc = self._read_json(path, validate)
        entry = (doc or {}).get('campaigns', {}).get(cid)
        if not entry:
            return {'definition_id': None, 'head_revision': 0, 'active': None,
                    'revisions': []}
        revs = copy.deepcopy(entry['revisions'])
        act = entry['active_revision']
        return {'definition_id': entry['definition_id'], 'head_revision': len(revs),
                'active': revs[act - 1] if act else None, 'revisions': revs}

    def set_definition(self, project_id: Any, campaign_id: Any, spec: Any, *,
                       expected_revision: Any) -> dict:
        """Create the campaign's first definition, or supersede the active one.
        `expected_revision` is the head revision the caller last saw (0 when it
        saw none). Identical content to the active revision is an idempotent
        no-op, not a new series; any change starts a new revision and so a new
        series (spec section 5). Returns the active revision record."""
        pid, cid, _camp = self._owned(project_id, campaign_id)
        if not isinstance(expected_revision, int) or isinstance(expected_revision, bool):
            raise S.SchemaError('expected_revision: required integer')
        clean = S.validate_definition_spec(spec)
        conn = self._connection(pid, clean['connection_id'])
        try:
            adapter = self._registry.get(clean['source_id'])
        except UnknownSource:
            raise src_base.DefinitionRefused('source_id: no adapter is registered '
                                             'for this source')
        adapter.validate_definition(clean, conn)
        src_base.check_against_capabilities(clean, adapter.capabilities(conn))
        path, validate = self._definitions_io(pid)
        with self._locked(path):
            doc = self._read_json(path, validate) or self._new_definitions_doc(pid)
            entry = doc['campaigns'].get(cid)
            head = len(entry['revisions']) if entry else 0
            if expected_revision != head:
                raise RevisionConflict(expected_revision, head)
            now = self._clock()
            if entry and entry['active_revision']:
                active = entry['revisions'][entry['active_revision'] - 1]
                if S.definition_content(active) == S.definition_content(clean):
                    return copy.deepcopy(active)
                active['retired_at'] = now
            if not entry:
                entry = doc['campaigns'][cid] = {
                    'definition_id': S.new_id('definition'),
                    'active_revision': None, 'revisions': []}
            rec = {'id': entry['definition_id'], 'revision': head + 1,
                   'campaign_id': cid, **clean,
                   'effective_at': clean['effective_at'] or now,
                   'created_at': now, 'retired_at': None}
            entry['revisions'].append(rec)
            entry['active_revision'] = rec['revision']
            self._write_json(path, doc, validate)
            return copy.deepcopy(rec)

    def retire_definition(self, project_id: Any, campaign_id: Any, *,
                          expected_revision: Any) -> dict:
        """Leave the campaign with no active definition (a valid state). The
        revision and its snapshots stay readable; nothing is deleted."""
        pid, cid, _camp = self._owned(project_id, campaign_id)
        if not isinstance(expected_revision, int) or isinstance(expected_revision, bool):
            raise S.SchemaError('expected_revision: required integer')
        path, validate = self._definitions_io(pid)
        with self._locked(path):
            doc = self._read_json(path, validate)
            entry = (doc or {}).get('campaigns', {}).get(cid)
            if not doc or not entry or not entry['active_revision']:
                raise NotFound('no active definition for this campaign')
            if expected_revision != len(entry['revisions']):
                raise RevisionConflict(expected_revision, len(entry['revisions']))
            rec = entry['revisions'][entry['active_revision'] - 1]
            rec['retired_at'] = self._clock()
            entry['active_revision'] = None
            self._write_json(path, doc, validate)
            return copy.deepcopy(rec)

    def find_definition(self, project_id: Any, definition_id: Any) -> dict:
        """The revision list of a definition by id, within ONE project. Another
        project's id is simply not found here."""
        pid = S.require_id('project_id', project_id)
        S.require_own_id('definition', definition_id)
        path, validate = self._definitions_io(pid)
        doc = self._read_json(path, validate)
        for entry in (doc or {}).get('campaigns', {}).values():
            if entry['definition_id'] == definition_id:
                return copy.deepcopy(entry)
        raise NotFound('definition not found in this project')

    # -- link manifest ----------------------------------------------------------------------

    def _links_io(self, pid: str):
        return (self._path(pid, LINKS_FILE),
                lambda d: S.validate_links_document(d, project_id=pid))

    def add_link(self, project_id: Any, record: Any) -> tuple[dict, bool]:
        """Append one manifest row. The caller mints the id (the UTM carries it),
        so a retry sends the same row: identical content is a no-op, the same id
        with different content is a `LinkConflict` -- an issued identity is
        frozen with its version (spec section 6). Returns (row, created)."""
        S.validate_link_record(record)
        pid, cid, _camp = self._owned(project_id, record['campaign_id'])
        path, validate = self._links_io(pid)
        with self._locked(path):
            doc = self._read_json(path, validate) or {
                'schema_version': S.SCHEMA_VERSION, 'kind': S.KIND_LINKS,
                'project_id': pid, 'links': {}}
            existing = doc['links'].get(record['link_id'])
            if existing is not None:
                if existing == record:
                    return copy.deepcopy(existing), False
                raise LinkConflict('link id already issued with different content')
            doc['links'][record['link_id']] = copy.deepcopy(record)
            self._write_json(path, doc, validate)
            return copy.deepcopy(record), True

    def get_link(self, project_id: Any, link_id: Any) -> dict:
        pid = S.require_id('project_id', project_id)
        S.require_own_id('link', link_id)
        path, validate = self._links_io(pid)
        doc = self._read_json(path, validate)
        rec = (doc or {}).get('links', {}).get(link_id)
        if rec is None:
            raise NotFound('link not found in this project')
        return copy.deepcopy(rec)

    def list_links(self, project_id: Any, campaign_id: Any) -> list[dict]:
        pid, cid, _camp = self._owned(project_id, campaign_id)
        path, validate = self._links_io(pid)
        doc = self._read_json(path, validate)
        rows = [r for r in (doc or {}).get('links', {}).values() if r['campaign_id'] == cid]
        return copy.deepcopy(sorted(rows, key=lambda r: (r['created_at'], r['link_id'])))

    # -- snapshots (append-only JSONL) --------------------------------------------------------

    def _snapshots_path(self, pid: str) -> Path:
        return self._path(pid, SNAPSHOTS_FILE)

    def _parse_snapshot_line(self, raw: bytes) -> dict:
        rec = json.loads(raw.decode('utf-8'))
        return S.validate_snapshot_record(rec)

    def _read_snapshots_unlocked(self, path: Path) -> SnapshotRead:
        if not path.exists():
            return SnapshotRead(())
        try:
            data = path.read_bytes()
        except OSError as e:
            _log(f'[desk-impact] read failed for {path}: {type(e).__name__}: {e}',
                 level='error')
            raise StoreIOError('snapshots could not be read')
        lines = data.split(b'\n')
        tail = lines.pop()              # b'' when the file ends with a newline
        records: list[dict] = []
        for n, raw in enumerate(lines, 1):
            try:
                records.append(self._parse_snapshot_line(raw))
            except (ValueError, UnicodeDecodeError) as e:
                _log(f'[desk-impact] corrupt snapshot row {n}, left in place: {path}: '
                     f'{type(e).__name__}', level='error')
                raise StoreCorrupt(f'snapshots.jsonl row {n} is not a valid snapshot')
        truncated = False
        if tail:
            try:
                records.append(self._parse_snapshot_line(tail))
            except (ValueError, UnicodeDecodeError):
                truncated = True
                _log(f'[desk-impact] truncated final snapshot line ({len(tail)} bytes) '
                     f'excluded: {path}', level='warn')
        return SnapshotRead(tuple(records), truncated)

    def _set_aside_tail(self, path: Path) -> None:
        """Called under the lock before an append. If the file does not end with
        a newline the last line is unterminated: a complete record just gets its
        newline; a partial one is moved to a `.part` sidecar (preserved) and cut
        off, so the new row never concatenates onto it."""
        if not path.exists() or path.stat().st_size == 0:
            return
        data = path.read_bytes()
        if data.endswith(b'\n'):
            return
        cut = data.rfind(b'\n') + 1
        tail = data[cut:]
        try:
            self._parse_snapshot_line(tail)
        except (ValueError, UnicodeDecodeError):
            side = path.with_name(f'{path.name}.truncated-'
                                  f'{self._clock().replace(":", "")}.part')
            try:
                with open(side, 'wb') as f:
                    f.write(tail)
                    f.flush()
                    os.fsync(f.fileno())
                with open(path, 'r+b') as f:
                    f.truncate(cut)
                    f.flush()
                    os.fsync(f.fileno())
            except OSError as e:
                _log(f'[desk-impact] could not set aside truncated tail of {path}: {e}',
                     level='error')
                raise StoreIOError('snapshots tail could not be repaired')
            _log(f'[desk-impact] truncated final snapshot line set aside to {side}',
                 level='warn')
            return
        try:
            with open(path, 'ab') as f:
                f.write(b'\n')
                f.flush()
                os.fsync(f.fileno())
        except OSError as e:
            raise StoreIOError(f'snapshots tail could not be terminated: {e}')

    def _check_snapshot_refs(self, pid: str, snap: dict) -> None:
        self._connection(pid, snap['connection_id'])
        did = snap['definition_id']
        if did is None:
            return
        entry = self.find_definition(pid, did)
        rev = snap['definition_revision']
        if rev > len(entry['revisions']):
            raise NotFound('definition revision not found in this project')
        d = entry['revisions'][rev - 1]
        for field in ('source_id', 'connection_id', 'metric', 'unit', 'target'):
            if d[field] != snap[field]:
                raise S.SchemaError(f'snapshot {field} does not match definition '
                                    f'revision {rev}')

    def append_snapshot(self, project_id: Any, observation: Any) -> tuple[dict, bool]:
        """Append one observation. Returns (row, created); a retry returns the
        existing row with created=False. Refuses an observation for a connection
        or definition that is not this project's, and one whose target/metric
        disagree with the definition revision it claims."""
        pid = S.require_id('project_id', project_id)
        snap = S.validate_snapshot_input(observation)
        self._check_snapshot_refs(pid, snap)
        key = S.snapshot_key(snap)
        content = S.snapshot_content_hash(snap)
        path = self._snapshots_path(pid)
        with self._locked(path):
            self._set_aside_tail(path)
            for row in self._read_snapshots_unlocked(path).records:
                if row['key'] == key and S.snapshot_content_hash(row) == content:
                    return copy.deepcopy(row), False
            row = {'schema_version': S.SCHEMA_VERSION,
                   'snapshot_id': S.new_id('snapshot'), 'key': key,
                   'recorded_at': self._clock(), **snap}
            line = json.dumps(row, sort_keys=True, ensure_ascii=False,
                              separators=(',', ':')).encode('utf-8')
            if len(line) > S.MAX_SNAPSHOT_LINE_BYTES:
                raise S.SchemaError(f'snapshot would be {len(line)} bytes; limit is '
                                    f'{S.MAX_SNAPSHOT_LINE_BYTES}. Nothing was saved.')
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                with open(path, 'ab') as f:
                    f.write(line + b'\n')
                    f.flush()
                    os.fsync(f.fileno())
            except OSError as e:
                _log(f'[desk-impact] snapshot append failed for {path}: '
                     f'{type(e).__name__}: {e}', level='error')
                raise StoreIOError('snapshot could not be saved')
            return copy.deepcopy(row), True

    def read_snapshots(self, project_id: Any) -> SnapshotRead:
        """Every stored row in append order, corrections included. A truncated
        final line is excluded and flagged; corruption elsewhere raises."""
        pid = S.require_id('project_id', project_id)
        path = self._snapshots_path(pid)
        if not path.exists():
            return SnapshotRead(())     # a read never creates the project directory
        with self._locked(path):
            return self._read_snapshots_unlocked(path)

    def effective_snapshots(self, project_id: Any, *, definition_id: str | None = None,
                            definition_revision: int | None = None,
                            source_id: str | None = None,
                            connection_id: str | None = None,
                            metric: str | None = None) -> list[dict]:
        """One row per key: the latest `observed_at`, ties to the later append.
        Filters are exact; a definition revision is its own series and is never
        merged with another."""
        read = self.read_snapshots(project_id)
        best: dict[str, tuple[Any, int, dict]] = {}
        for i, row in enumerate(read.records):
            if definition_id is not None and row['definition_id'] != definition_id:
                continue
            if definition_revision is not None and row['definition_revision'] != definition_revision:
                continue
            if source_id is not None and row['source_id'] != source_id:
                continue
            if connection_id is not None and row['connection_id'] != connection_id:
                continue
            if metric is not None and row['metric'] != metric:
                continue
            stamp = (S.parse_utc('observed_at', row['observed_at']), i)
            cur = best.get(row['key'])
            if cur is None or stamp > (cur[0], cur[1]):
                best[row['key']] = (stamp[0], i, row)
        rows = [copy.deepcopy(v[2]) for v in best.values()]
        return sorted(rows, key=lambda r: (r['effective_at'], r['key']))

    def snapshot_history(self, project_id: Any, key: str) -> list[dict]:
        """Every row ever stored for one key, oldest append first."""
        if not isinstance(key, str) or not S._KEY_RE.fullmatch(key):
            raise S.SchemaError('key: invalid snapshot key')
        return [copy.deepcopy(r) for r in self.read_snapshots(project_id).records
                if r['key'] == key]

    # -- read-attempt status (coverage.json) ---------------------------------------------------

    def _coverage_io(self, pid: str):
        return (self._path(pid, COVERAGE_FILE),
                lambda d: S.validate_coverage_document(d, project_id=pid))

    def record_attempt(self, project_id: Any, source_id: Any, connection_id: Any,
                       target: Any, *, ok: bool, error_code: str | None = None,
                       error_detail: str | None = None, retry_after: str | None = None,
                       gaps: list[dict] | None = None,
                       incomplete_pages: bool = False) -> dict:
        """Record one collection attempt for a source+target. Success sets
        `last_success_at`; failure keeps the earlier one, so stale data is never
        relabelled fresh. Gaps accumulate (deduplicated); they are never
        dropped. A snapshot is not touched either way."""
        pid = S.require_id('project_id', project_id)
        sid = S.require_slug('source_id', source_id)
        cid_ = S.require_id('connection_id', connection_id)
        tgt = S.target('target', target)
        if not isinstance(ok, bool):
            raise S.SchemaError('ok: must be boolean')
        self._connection(pid, cid_)
        key = S.coverage_key(sid, cid_, tgt)
        path, validate = self._coverage_io(pid)
        with self._locked(path):
            doc = self._read_json(path, validate) or {
                'schema_version': S.SCHEMA_VERSION, 'kind': S.KIND_COVERAGE,
                'project_id': pid, 'entries': {}}
            prev = doc['entries'].get(key)
            now = self._clock()
            merged = list(prev['gaps']) if prev else []
            for g in gaps or []:
                if g not in merged:
                    merged.append(g)
            entry = {
                'source_id': sid, 'connection_id': cid_, 'target': tgt,
                'status': 'ok' if ok else 'error',
                'last_attempt_at': now,
                'last_success_at': now if ok else (prev['last_success_at'] if prev else None),
                'error_code': None if ok else error_code,
                'error_detail': None if ok else error_detail,
                'retry_after': None if ok else retry_after,
                'gaps': merged, 'incomplete_pages': bool(incomplete_pages),
            }
            S.validate_coverage_entry(entry)
            doc['entries'][key] = entry
            self._write_json(path, doc, validate)
            return copy.deepcopy(entry)

    def get_coverage(self, project_id: Any, source_id: Any, connection_id: Any,
                     target: Any) -> dict | None:
        pid = S.require_id('project_id', project_id)
        key = S.coverage_key(S.require_slug('source_id', source_id),
                             S.require_id('connection_id', connection_id),
                             S.target('target', target))
        path, validate = self._coverage_io(pid)
        doc = self._read_json(path, validate)
        entry = (doc or {}).get('entries', {}).get(key)
        return copy.deepcopy(entry)
