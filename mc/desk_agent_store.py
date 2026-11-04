"""Persistence for the Desk agent channel (MC-1151 P1, DARK).

Spec: `docs/desk_v1/AGENT_CHANNEL_SPEC.md` sections 4 and 9 (row P1). Shapes
and limits live in `mc/desk_agent_schema.py`; this module owns ownership
resolution, locking, revision conflicts, crash-safe writes and append-only
history. Nothing imports it yet: no route, no UI, no scheduler. P4 wires it.

WHERE THE STATE LIVES: `data/desk_agent/<project_id>/<campaign_id>/checklist.json`
-- a SIBLING of DATA_DIR (`data/projects/`), never under it. `load_projects()`
parses every `*.json` in DATA_DIR as a project record, so a checklist written
there would become a malformed "project" and 500 both restart endpoints
(CLAUDE.md LOAD-BEARING DATA_DIR rule). The constructor refuses a root at or
inside DATA_DIR rather than trusting every future caller to remember that.
`data/desk_agent/` is gitignored and not in `installer/build-macos.spec`.

FIVE INVARIANTS, each with a test in tests/test_desk_agent_store.py:

  1. IDs are validated BEFORE a path is built, and the built path is checked to
     be inside the root, so `..`, separators, drive letters and NULs never
     reach the filesystem.
  2. Ownership is resolved server-side on every call, reads included: the
     campaign must exist and its `project_id` must equal the caller's. A
     wrong-project campaign is reported exactly like an unknown one.
  3. Every edit carries the checklist revision the caller last saw. A stale
     revision is a `RevisionConflict`, never a lost write. Opens are the one
     exception: they are idempotent and never need a revision.
  4. A corrupt or schema-invalid record raises `StoreCorrupt` and is logged;
     it is NEVER replaced by a fresh empty checklist (spec section 4).
  5. History is append-only with no last-N cap: revisions, evidence, lint
     results and submission events are only ever added. Over-limit input is
     refused whole (`SchemaError`), never truncated.

Human-only outcome reporting is NOT enforced here. `append_submission_event`
records `reported_by: human` because its only intended caller is the P4 route,
which must apply the existing human-action enforcement pattern (a request body
saying `by: human` is insufficient, spec section 6.5). No route calls it in P1.
"""
from __future__ import annotations

import contextlib
import copy
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Generator, Optional

from mc.atomic_json import read_text_with_retry, write_json_atomic
from mc.core import _log, now_iso, path_is_within
from mc import desk_agent_schema as S

CampaignResolver = Callable[[str], Optional[dict]]

FILE_NAME = 'checklist.json'
_LOCK_TIMEOUT_S = 10.0
_LOCK_STALE_S = 30.0
_LOCK_POLL_S = 0.005
_DUMPS_KW: dict[str, Any] = {'indent': 1, 'ensure_ascii': False, 'sort_keys': True}


# -- errors ---------------------------------------------------------------------

class AgentChannelError(Exception):
    """Base class. Messages carry ids and rule names, never submitted values."""


class NotFound(AgentChannelError):
    pass


class OwnershipError(NotFound):
    """Campaign belongs to another project. Subclass of NotFound and worded the
    same, so a caller cannot probe which project owns a campaign id."""


class RevisionConflict(AgentChannelError):
    def __init__(self, expected: Any, current: int):
        self.expected = expected
        self.current = current
        super().__init__(f'checklist changed since revision {expected!r}; '
                         f'current revision is {current}')


class ArchivedError(AgentChannelError):
    pass


class ChecklistExists(AgentChannelError):
    pass


class StoreCorrupt(AgentChannelError):
    """The record on disk cannot be read as a valid checklist. Left in place."""


class StoreIOError(AgentChannelError):
    pass


# -- default ownership resolver ----------------------------------------------

def desk_campaign_resolver(campaign_id: str) -> dict | None:
    """Look a campaign up in the Desk store (`data/desk.json`). Lazy import so
    this module stays importable without the Desk wired."""
    from mc import desk as _desk
    with _desk._store_lock:
        camp = _desk._read_store()['campaigns'].get(campaign_id)
    return copy.deepcopy(camp) if isinstance(camp, dict) else None


def default_root(base_dir: Path) -> Path:
    """`<base>/data/desk_agent` -- `base` is the same data root server.py
    hands `desk.STORE_PATH` (`_DATA_ROOT`)."""
    return Path(base_dir) / 'data' / 'desk_agent'


# -- the store -------------------------------------------------------------------

class AgentChannelStore:
    def __init__(self, root: Path, *, campaign_resolver: CampaignResolver | None = None,
                 data_dir: Path | None = None, clock: Callable[[], str] = now_iso):
        root = Path(root)
        if data_dir is not None and (path_is_within(root, data_dir)
                                     or path_is_within(data_dir, root)):
            raise ValueError('agent-channel store root must not overlap DATA_DIR '
                             '(data/projects); load_projects() would read it')
        self.root = root
        self._resolve_campaign = campaign_resolver or desk_campaign_resolver
        self._clock = clock
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    # -- identity, ownership, paths ------------------------------------------

    def _owned(self, project_id: Any, campaign_id: Any) -> tuple[str, str]:
        pid = S.require_id('project_id', project_id)
        cid = S.require_id('campaign_id', campaign_id)
        camp = self._resolve_campaign(cid)
        if not isinstance(camp, dict):
            raise NotFound('campaign not found in this project')
        if camp.get('project_id') != pid:
            raise OwnershipError('campaign not found in this project')
        return pid, cid

    def _path(self, pid: str, cid: str) -> Path:
        path = self.root / pid / cid / FILE_NAME
        # Belt and braces after require_id, LEXICAL on purpose. Measured
        # 2026-11-04 (Python 3.14, Windows): Path.resolve() on a path whose
        # directory a sibling thread is creating returns it with a `\\?\` long-path
        # prefix, so the resolve()-based path_is_within said "outside" and
        # refused 3 of 12 concurrent valid opens.
        if path.relative_to(self.root).parts != (pid, cid, FILE_NAME):
            raise S.SchemaError('resolved path escapes the agent-channel root')
        return path

    # -- locking ---------------------------------------------------------------

    @contextlib.contextmanager
    def _locked(self, path: Path) -> Generator[None, None, None]:
        """In-process lock per record plus an O_EXCL lockfile, so the two MC
        processes alive during a restart overlap (mc/atomic_json.py) cannot
        interleave a read-modify-write either. Same lockfile shape as
        mc/positions_review._locked_state."""
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
                        _log(f'[desk-agent] breaking stale lock {lock_path}', level='warn')
                        with contextlib.suppress(OSError):
                            lock_path.unlink()
                        continue
                    if time.monotonic() > deadline:
                        raise StoreIOError('agent-channel record is locked by another writer')
                    time.sleep(_LOCK_POLL_S)
            try:
                os.close(fd)
                yield
            finally:
                try:
                    lock_path.unlink()
                except OSError as e:
                    _log(f'[desk-agent] lock release failed for {lock_path}: {e}',
                         level='warn')

    # -- raw read / write ------------------------------------------------------

    def _read(self, path: Path, pid: str, cid: str) -> dict | None:
        if not path.exists():
            return None
        try:
            if path.stat().st_size > S.MAX_RECORD_BYTES:
                raise StoreCorrupt('agent-channel record exceeds the size limit')
            text = read_text_with_retry(path)
        except StoreCorrupt:
            _log(f'[desk-agent] record over size limit, left in place: {path}', level='error')
            raise
        except OSError as e:
            _log(f'[desk-agent] read failed for {path}: {type(e).__name__}: {e}', level='error')
            raise StoreIOError('agent-channel record could not be read')
        try:
            doc = json.loads(text)
        except ValueError as e:
            _log(f'[desk-agent] corrupt record (JSON), left in place: {path}: '
                 f'{type(e).__name__} at char {getattr(e, "pos", "?")}', level='error')
            raise StoreCorrupt('agent-channel record is not valid JSON')
        try:
            return S.validate_document(doc, project_id=pid, campaign_id=cid)
        except S.SchemaError as e:
            _log(f'[desk-agent] corrupt record (schema), left in place: {path}: {e}',
                 level='error')
            raise StoreCorrupt(f'agent-channel record failed validation: {e}')

    def _write(self, path: Path, doc: dict, pid: str, cid: str) -> None:
        S.validate_document(doc, project_id=pid, campaign_id=cid)
        size = len(json.dumps(doc, **_DUMPS_KW).encode('utf-8'))
        if size > S.MAX_RECORD_BYTES:
            raise S.SchemaError(f'record would be {size} bytes; limit is '
                                f'{S.MAX_RECORD_BYTES}. Nothing was saved.')
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            write_json_atomic(path, doc, **_DUMPS_KW)
        except OSError as e:
            _log(f'[desk-agent] write failed for {path}: {type(e).__name__}: {e}',
                 level='error')
            raise StoreIOError('agent-channel record could not be saved')

    def _new_doc(self, pid: str, cid: str) -> dict:
        now = self._clock()
        return {
            'schema_version': S.SCHEMA_VERSION,
            'kind': S.RECORD_KIND,
            'checklist': {
                'schema_version': S.SCHEMA_VERSION,
                'id': S.new_id('checklist'),
                'project_id': pid,
                'campaign_id': cid,
                # Stable measurement identity (spec section 7): survives title
                # and project display-name changes; never a plan.accounts row.
                'channel_id': S.new_id('channel'),
                'revision': 1,
                'artifacts': [],
                'entry_ids': [],
                'created_at': now,
                'updated_at': now,
                'archived_at': None,
                'copied_from': None,
            },
            'entries': {},
            'revisions': {},
            'evidence': {},
            'lint_results': {},
            'submission_events': [],
        }

    @contextlib.contextmanager
    def _mutate(self, project_id: Any, campaign_id: Any,
                expected_revision: Any) -> Generator[dict, None, None]:
        """Locked read-modify-write. The body mutates the yielded doc; on clean
        exit the revision is bumped and the record replaced atomically. Any
        exception inside the body saves nothing."""
        pid, cid = self._owned(project_id, campaign_id)
        if not isinstance(expected_revision, int) or isinstance(expected_revision, bool):
            raise S.SchemaError('expected_revision: required integer')
        path = self._path(pid, cid)
        with self._locked(path):
            doc = self._read(path, pid, cid)
            if doc is None:
                raise NotFound('no agent-listings checklist for this campaign')
            ck = doc['checklist']
            if ck.get('archived_at'):
                raise ArchivedError('checklist is archived; its history is read-only')
            if ck['revision'] != expected_revision:
                raise RevisionConflict(expected_revision, ck['revision'])
            work = copy.deepcopy(doc)
            yield work
            work['checklist']['revision'] = ck['revision'] + 1
            work['checklist']['updated_at'] = self._clock()
            self._write(path, work, pid, cid)

    # -- reads -------------------------------------------------------------------

    def get_checklist(self, project_id: Any, campaign_id: Any) -> dict | None:
        """Read only. Never creates. None when the campaign has no checklist."""
        pid, cid = self._owned(project_id, campaign_id)
        path = self._path(pid, cid)
        return self._read(path, pid, cid)

    def _require_doc(self, project_id: Any, campaign_id: Any) -> dict:
        doc = self.get_checklist(project_id, campaign_id)
        if doc is None:
            raise NotFound('no agent-listings checklist for this campaign')
        return doc

    def find_entry(self, project_id: Any, campaign_id: Any, entry_id: Any) -> dict:
        S.require_own_id('entry', entry_id)
        doc = self._require_doc(project_id, campaign_id)
        entry = doc['entries'].get(entry_id)
        if entry is None or entry['checklist_id'] != doc['checklist']['id']:
            raise NotFound('entry not found in this campaign')
        return entry

    def find_revision(self, project_id: Any, campaign_id: Any, revision_id: Any) -> dict:
        S.require_own_id('revision', revision_id)
        doc = self._require_doc(project_id, campaign_id)
        rev = doc['revisions'].get(revision_id)
        if rev is None:
            raise NotFound('revision not found in this campaign')
        return rev

    def entry_history(self, project_id: Any, campaign_id: Any, entry_id: Any) -> dict:
        """Every revision (oldest first), every lint result bound to them and
        every submission event (in append order, corrections included)."""
        S.require_own_id('entry', entry_id)
        doc = self._require_doc(project_id, campaign_id)
        if entry_id not in doc['entries']:
            raise NotFound('entry not found in this campaign')
        revs = sorted((r for r in doc['revisions'].values() if r['entry_id'] == entry_id),
                      key=lambda r: r['number'])
        rev_ids = {r['id'] for r in revs}
        lints = sorted((x for x in doc['lint_results'].values()
                        if x['revision_id'] in rev_ids), key=lambda x: x['created_at'])
        events = [e for e in doc['submission_events'] if e['entry_id'] == entry_id]
        return {'entry': doc['entries'][entry_id], 'revisions': revs,
                'lint_results': lints, 'submission_events': events}

    # -- opens (idempotent) --------------------------------------------------------

    def open_checklist(self, project_id: Any, campaign_id: Any) -> dict:
        """Return the campaign's checklist, creating it on first open. Repeat
        and concurrent opens return the same record; an archived checklist is
        returned as-is, never replaced by a new one."""
        pid, cid = self._owned(project_id, campaign_id)
        path = self._path(pid, cid)
        with self._locked(path):
            doc = self._read(path, pid, cid)
            if doc is not None:
                return doc
            doc = self._new_doc(pid, cid)
            self._write(path, doc, pid, cid)
            return doc

    def open_entry(self, project_id: Any, campaign_id: Any, surface_id: Any,
                   artifact_id: Any = None) -> tuple[dict, dict]:
        """Return the entry for (surface, artifact), creating it once.
        Unique by checklist/surface/artifact (spec section 4); a removed entry
        is returned, not duplicated. A new entry starts `needs_information`."""
        S.require_choice('surface_id', surface_id, S.SURFACE_IDS)
        if artifact_id is not None:
            S.require_own_id('artifact', artifact_id)
        pid, cid = self._owned(project_id, campaign_id)
        path = self._path(pid, cid)
        with self._locked(path):
            doc = self._read(path, pid, cid)
            if doc is None:
                raise NotFound('no agent-listings checklist for this campaign')
            for eid in doc['checklist']['entry_ids']:
                e = doc['entries'][eid]
                if e['surface_id'] == surface_id and e['artifact_id'] == artifact_id:
                    return doc, e
            if doc['checklist'].get('archived_at'):
                raise ArchivedError('checklist is archived; its history is read-only')
            if artifact_id is not None and artifact_id not in {
                    a['id'] for a in doc['checklist']['artifacts']}:
                raise NotFound('artifact not found in this checklist')
            entry = {
                'id': S.new_id('entry'),
                'checklist_id': doc['checklist']['id'],
                'surface_id': surface_id,
                'artifact_id': artifact_id,
                'applicability': 'needs_information',
                'applicability_reason': '',
                'draft_revision': 0,
                'lifecycle': 'active',
                # Owned by MC-1152's manifest (spec section 8); referenced only.
                'link_ids': [],
                'created_at': self._clock(),
            }
            doc['entries'][entry['id']] = entry
            doc['checklist']['entry_ids'].append(entry['id'])
            doc['checklist']['revision'] += 1
            doc['checklist']['updated_at'] = self._clock()
            self._write(path, doc, pid, cid)
            return doc, entry

    # -- edits (all require expected_revision) ---------------------------------------

    def set_artifacts(self, project_id: Any, campaign_id: Any, expected_revision: Any,
                      artifacts: Any) -> dict:
        """Replace the owner-provided artifact facts. An artifact id that an
        entry references may be corrected but not dropped."""
        if not isinstance(artifacts, list) or len(artifacts) > S.MAX_ARTIFACTS:
            raise S.SchemaError(f'artifacts: must be a list of at most {S.MAX_ARTIFACTS}')
        clean = [S.normalize_artifact(a) for a in artifacts]
        ids = [a['id'] for a in clean]
        if len(ids) != len(set(ids)):
            raise S.SchemaError('artifacts: duplicate artifact id')
        with self._mutate(project_id, campaign_id, expected_revision) as doc:
            referenced = {e['artifact_id'] for e in doc['entries'].values()
                          if e['artifact_id']}
            if referenced - set(ids):
                raise S.SchemaError('artifacts: an entry still references a removed artifact')
            doc['checklist']['artifacts'] = clean
        return doc

    def set_applicability(self, project_id: Any, campaign_id: Any, expected_revision: Any,
                          entry_id: Any, applicability: Any, reason: Any = '') -> dict:
        S.require_own_id('entry', entry_id)
        value = S.require_choice('applicability', applicability, S.APPLICABILITY)
        why = S.normalize_reason('applicability_reason', reason)
        if value != 'applicable' and not why.strip():
            raise S.SchemaError('applicability_reason: required unless applicable')
        with self._mutate(project_id, campaign_id, expected_revision) as doc:
            entry = self._entry_in(doc, entry_id)
            entry['applicability'] = value
            entry['applicability_reason'] = why
        return doc

    def remove_entry(self, project_id: Any, campaign_id: Any, expected_revision: Any,
                     entry_id: Any) -> dict:
        """Retire an entry. Its id, revisions and reports stay (spec section 8:
        removed entries retain historical identities)."""
        S.require_own_id('entry', entry_id)
        with self._mutate(project_id, campaign_id, expected_revision) as doc:
            self._entry_in(doc, entry_id)['lifecycle'] = 'removed'
        return doc

    def add_evidence(self, project_id: Any, campaign_id: Any, expected_revision: Any,
                     evidence: Any) -> tuple[dict, dict]:
        ev = S.normalize_evidence(evidence, now=self._clock())
        with self._mutate(project_id, campaign_id, expected_revision) as doc:
            doc['evidence'][ev['id']] = ev
        return doc, ev

    def add_revision(self, project_id: Any, campaign_id: Any, expected_revision: Any,
                     entry_id: Any, *, fields: Any, field_evidence: Any,
                     rendered_text: Any, template_version: Any) -> tuple[dict, dict]:
        """Append a prepared revision. Earlier revisions are never edited.
        The content hash is computed here, never accepted from the caller."""
        S.require_own_id('entry', entry_id)
        clean_fields = S.normalize_revision_fields(fields)
        text = S.normalize_rendered_text(rendered_text)
        tver = S.normalize_template_version(template_version)
        if not isinstance(field_evidence, dict):
            raise S.SchemaError('field_evidence: must map field name to evidence ids')
        with self._mutate(project_id, campaign_id, expected_revision) as doc:
            entry = self._entry_in(doc, entry_id)
            if entry['lifecycle'] != 'active':
                raise S.SchemaError('entry is removed; reopen it before preparing')
            fe: dict[str, list[str]] = {}
            for fname, ev_ids in field_evidence.items():
                if fname not in clean_fields:
                    raise S.SchemaError('field_evidence: cites a field the revision lacks')
                if not isinstance(ev_ids, list):
                    raise S.SchemaError('field_evidence: values must be lists of ids')
                for ev_id in ev_ids:
                    S.require_own_id('evidence', ev_id)
                    if ev_id not in doc['evidence']:
                        raise S.SchemaError('field_evidence: cites unknown evidence')
                fe[fname] = sorted(set(ev_ids))
            number = entry['draft_revision'] + 1
            rev = {
                'id': S.new_id('revision'),
                'entry_id': entry_id,
                'number': number,
                'fields': clean_fields,
                'field_evidence': fe,
                'rendered_text': text,
                'content_hash': S.content_hash(text),
                'template_version': tver,
                'created_at': self._clock(),
            }
            doc['revisions'][rev['id']] = rev
            entry['draft_revision'] = number
        return doc, rev

    def record_lint_result(self, project_id: Any, campaign_id: Any, expected_revision: Any,
                           revision_id: Any, *, result: Any, findings: Any,
                           linter_version: Any, rules_version: Any) -> tuple[dict, dict]:
        """P2's linter writes through this. The bound hashes are taken from the
        stored revision, so a result can never be attached to other bytes."""
        S.require_own_id('revision', revision_id)
        outcome = S.require_choice('result', result, S.LINT_RESULTS)
        clean = S.normalize_findings(findings)
        lver = S.normalize_template_version(linter_version)
        rver = S.normalize_template_version(rules_version)
        with self._mutate(project_id, campaign_id, expected_revision) as doc:
            rev = doc['revisions'].get(revision_id)
            if rev is None:
                raise NotFound('revision not found in this campaign')
            rec = {
                'id': S.new_id('lint'),
                'revision_id': revision_id,
                'content_hash': rev['content_hash'],
                'evidence_hash': revision_evidence_hash(doc, rev),
                'linter_version': lver,
                'rules_version': rver,
                'template_version': rev['template_version'],
                'created_at': self._clock(),
                'result': outcome,
                'findings': clean,
            }
            doc['lint_results'][rec['id']] = rec
        return doc, rec

    def append_submission_event(self, project_id: Any, campaign_id: Any,
                                expected_revision: Any, entry_id: Any, revision_id: Any,
                                *, outcome: Any, listing_url: Any = None,
                                reason: Any = '', supersedes: Any = None) -> tuple[dict, dict]:
        """Append a USER-REPORTED outcome. Corrections append a superseding
        event; nothing is edited. Caller must enforce human origin (module
        docstring). Not independently verified, and not a conversion."""
        S.require_own_id('entry', entry_id)
        S.require_own_id('revision', revision_id)
        value = S.require_choice('outcome', outcome, S.SUBMISSION_OUTCOMES)
        url = S.optional_url('listing_url', listing_url, required=False)
        why = S.normalize_reason('reason', reason)
        if supersedes is not None:
            S.require_own_id('submission', supersedes)
        with self._mutate(project_id, campaign_id, expected_revision) as doc:
            self._entry_in(doc, entry_id)
            rev = doc['revisions'].get(revision_id)
            if rev is None or rev['entry_id'] != entry_id:
                raise NotFound('revision not found for this entry')
            if supersedes is not None and not any(
                    e['id'] == supersedes and e['entry_id'] == entry_id
                    for e in doc['submission_events']):
                raise NotFound('superseded event not found for this entry')
            ev = {
                'id': S.new_id('submission'),
                'entry_id': entry_id,
                'revision_id': revision_id,
                'content_hash': rev['content_hash'],
                'outcome': value,
                'created_at': self._clock(),
                'reported_by': 'human',
                'listing_url': url,
                'reason': why,
                'supersedes': supersedes,
            }
            doc['submission_events'].append(ev)
        return doc, ev

    def archive_checklist(self, project_id: Any, campaign_id: Any,
                          expected_revision: Any) -> dict:
        """Freeze the checklist. History stays readable; edits are refused.
        There is no delete or purge (spec section 4)."""
        with self._mutate(project_id, campaign_id, expected_revision) as doc:
            doc['checklist']['archived_at'] = self._clock()
        return doc

    def copy_checklist(self, project_id: Any, source_campaign_id: Any,
                       target_campaign_id: Any) -> dict:
        """Copy into another campaign of the SAME project. New checklist,
        channel and entry ids; link ids reset (MC-1152 mints its own); the
        current draft of each active entry is carried as revision 1 with
        fresh evidence ids. Lint results and submission reports are NOT
        carried -- the copy starts as an unlinted draft with no outcome."""
        pid, src = self._owned(project_id, source_campaign_id)
        _, dst = self._owned(project_id, target_campaign_id)
        if src == dst:
            raise S.SchemaError('copy target must be a different campaign')
        source = self.get_checklist(pid, src)
        if source is None:
            raise NotFound('no agent-listings checklist for the source campaign')
        path = self._path(pid, dst)
        with self._locked(path):
            if self._read(path, pid, dst) is not None:
                raise ChecklistExists('target campaign already has a checklist')
            doc = self._new_doc(pid, dst)
            ck = doc['checklist']
            ck['artifacts'] = copy.deepcopy(source['checklist']['artifacts'])
            ck['copied_from'] = {'project_id': pid, 'campaign_id': src,
                                 'checklist_id': source['checklist']['id']}
            now = self._clock()
            for old_id in source['checklist']['entry_ids']:
                old = source['entries'][old_id]
                if old['lifecycle'] != 'active':
                    continue
                entry = {
                    'id': S.new_id('entry'),
                    'checklist_id': ck['id'],
                    'surface_id': old['surface_id'],
                    'artifact_id': old['artifact_id'],
                    'applicability': old['applicability'],
                    'applicability_reason': old['applicability_reason'],
                    'draft_revision': 0,
                    'lifecycle': 'active',
                    'link_ids': [],
                    'created_at': now,
                    'copied_from_entry_id': old_id,
                }
                current = _current_revision(source, old)
                if current is not None:
                    ev_map: dict[str, str] = {}
                    for ids in current['field_evidence'].values():
                        for ev_id in ids:
                            if ev_id not in ev_map:
                                ev = copy.deepcopy(source['evidence'][ev_id])
                                ev['id'] = S.new_id('evidence')
                                doc['evidence'][ev['id']] = ev
                                ev_map[ev_id] = ev['id']
                    rev = copy.deepcopy(current)
                    rev['id'] = S.new_id('revision')
                    rev['entry_id'] = entry['id']
                    rev['number'] = 1
                    rev['created_at'] = now
                    rev['field_evidence'] = {
                        f: sorted(ev_map[i] for i in ids)
                        for f, ids in current['field_evidence'].items()}
                    doc['revisions'][rev['id']] = rev
                    entry['draft_revision'] = 1
                doc['entries'][entry['id']] = entry
                ck['entry_ids'].append(entry['id'])
            self._write(path, doc, pid, dst)
            return doc

    # -- helpers ---------------------------------------------------------------------

    @staticmethod
    def _entry_in(doc: dict, entry_id: str) -> dict:
        entry = doc['entries'].get(entry_id)
        if entry is None or entry['checklist_id'] != doc['checklist']['id']:
            raise NotFound('entry not found in this campaign')
        return entry


def _current_revision(doc: dict, entry: dict) -> dict | None:
    if not entry['draft_revision']:
        return None
    for rev in doc['revisions'].values():
        if rev['entry_id'] == entry['id'] and rev['number'] == entry['draft_revision']:
            return rev
    return None


def current_revision(doc: dict, entry: dict) -> dict | None:
    """The entry's newest prepared revision, or None before the first prepare."""
    return _current_revision(doc, entry)


def revision_evidence_hash(doc: dict, rev: dict) -> str:
    ids = sorted({i for ids in rev['field_evidence'].values() for i in ids})
    return S.evidence_hash([doc['evidence'][i] for i in ids if i in doc['evidence']])
