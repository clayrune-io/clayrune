"""MC-998 durable store for the Usage Breakdown dashboard
(docs/USAGE_BREAKDOWN_SPEC.md, "Sampling and durable facts").

`data/usage_breakdown.sqlite`, OUTSIDE `DATA_DIR` (`data/projects/`) so it
never trips `load_projects()`'s "every *.json in DATA_DIR is a project"
contract. WAL mode, so the caller must gitignore the `-wal`/`-shm` sidecars
alongside the db file itself.

Four tables, schema-versioned via `PRAGMA user_version`:
  - allowance_sample: one row per distinct vendor utilization reading.
    Deduplicated by (provider, window_kind, window_scope, source_observed_at)
    — an unchanged source observation is not a new sample (spec: "a cache hit
    is not a new sample" / "identical event identity/timestamp/values are
    stored once").
  - session_fact: one row per MC session_id, upserted at start/checkpoint/
    completion -- always the session's LATEST snapshot, for totals/rankings.
    Carries token categories, provenance, and an `included` flag so
    housekeeping/internal sessions stay visible-but-markable per spec §4.
  - session_checkpoint: exactly one 'baseline' row (written at dispatch, zero
    cumulative tokens -- a partial unique index on session_id makes a
    repeated dispatch-pending call a no-op) and one 'completion' row PER
    TURN (Mode-A completions fire per turn, not once per session; a unique
    index on (session_id, observed_at) only dedupes an exact retry, never a
    later turn's new timestamp). Schema v3 (was: table-level
    `UNIQUE(session_id, checkpoint_type)`, which froze the FIRST completion
    forever and silently discarded every later turn's row while
    `session_fact` kept advancing to the latest cumulative total --
    docs/_journal/4668eafc-mc998-fenn-review.md "2026-09-28 re-review"
    finding 3, P1-3). The tokens-per-point calibration in
    mc/usage_breakdown_aggregate.py walks each session's full checkpoint
    HISTORY as consecutive (turn) pairs to derive a per-turn delta, instead
    of charging a session's lifetime total to every interval it overlaps. A
    session with only a baseline row (still running, or ended without ever
    completing) has no measurable delta yet -- callers treat that as
    incomplete coverage, never as zero.
  - code_delta: one row per session_id, LOC added/deleted or an `unavailable`
    reason. `added`/`deleted` are always the SUM of the session's
    code_delta_lifetime rows.
  - code_delta_lifetime (schema v4): one row per (session_id, base_commit)
    -- one per worktree lifetime, since a session's worktree can be removed
    after merge-back and re-created at a new baseline. Each capture
    replaces only its own lifetime's cumulative count, so repeated captures
    are idempotent and an earlier lifetime is never overwritten (round 3,
    P2-5: accumulating onto a single row lost the prior lifetime on the
    next same-baseline capture, 1 -> 0).

No OAuth token, credential, prompt, transcript, source text, or task summary
is ever written here — only the normalized facts the spec's tables define.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

SCHEMA_VERSION = 4
APPLICATION_ID = 0x4D435542  # 'MCUB'
DB_FILENAME = 'usage_breakdown.sqlite'

_TABLES = {'allowance_sample', 'session_fact', 'session_checkpoint', 'code_delta',
           'code_delta_lifetime'}
_V3_TABLES = {'allowance_sample', 'session_fact', 'session_checkpoint', 'code_delta'}
_V1_TABLES = {'allowance_sample', 'session_fact', 'code_delta'}

_CODE_DELTA_LIFETIME_TABLE_SQL = (
    'CREATE TABLE code_delta_lifetime ('
    ' session_id TEXT NOT NULL,'
    ' base_commit TEXT NOT NULL,'
    ' added INTEGER NOT NULL,'
    ' deleted INTEGER NOT NULL,'
    ' head_commits TEXT,'
    ' updated_at TEXT NOT NULL,'
    ' PRIMARY KEY(session_id, base_commit)'
    ')'
)
# v3 -> v4 seed: every pre-v4 'ok' row becomes its session's single
# lifetime. Exact for v1/v2 rows (they only ever overwrote). A v3 row that
# had already accumulated a recreated worktree stays one lifetime at its
# latest baseline -- v3 existed only on the unmerged MC-998 branch.
_CODE_DELTA_LIFETIME_SEED_SQL = (
    'INSERT INTO code_delta_lifetime (session_id, base_commit, added, deleted, head_commits, updated_at) '
    "SELECT session_id, base_commit, COALESCE(added, 0), COALESCE(deleted, 0), head_commits, created_at "
    "FROM code_delta WHERE status='ok' AND base_commit IS NOT NULL"
)

# Schema v3 shape (docs/_journal/4668eafc-mc998-fenn-review.md finding 3,
# P1-3): no table-level UNIQUE(session_id, checkpoint_type) -- that froze
# every session's completion checkpoint at its first turn. A 'baseline' row
# stays unique per session (a partial index, checked only for that type);
# 'completion' rows append one per turn, deduped only against an exact
# repeated (session_id, observed_at) retry.
_SESSION_CHECKPOINT_TABLE_SQL = (
    'CREATE TABLE session_checkpoint ('
    ' id INTEGER PRIMARY KEY AUTOINCREMENT,'
    ' session_id TEXT NOT NULL,'
    ' provider TEXT NOT NULL,'
    ' checkpoint_type TEXT NOT NULL,'  # 'baseline' | 'completion'
    ' observed_at TEXT NOT NULL,'
    ' input_fresh INTEGER,'
    ' input_cache_write INTEGER,'
    ' input_cache_read INTEGER,'
    ' input_processed_total INTEGER,'
    ' output_tokens INTEGER,'
    ' output_reasoning INTEGER,'
    ' token_coverage TEXT NOT NULL,'
    ' created_at TEXT NOT NULL'
    ')'
)
_SESSION_CHECKPOINT_INDEX_SQL = (
    'CREATE INDEX idx_session_checkpoint_session '
    'ON session_checkpoint(session_id, observed_at)',
    'CREATE INDEX idx_session_checkpoint_observed '
    'ON session_checkpoint(observed_at)',
    'CREATE UNIQUE INDEX idx_session_checkpoint_baseline_once '
    "ON session_checkpoint(session_id) WHERE checkpoint_type='baseline'",
    'CREATE UNIQUE INDEX idx_session_checkpoint_completion_dedup '
    "ON session_checkpoint(session_id, observed_at) WHERE checkpoint_type='completion'",
)


class UsageBreakdownStoreError(RuntimeError):
    pass


class SchemaError(UsageBreakdownStoreError):
    pass


def db_path_for(data_root: Path) -> Path:
    """`<data_root>/data/usage_breakdown.sqlite` — sibling of DATA_DIR
    (`<data_root>/data/projects`), never inside it."""
    return Path(data_root) / 'data' / DB_FILENAME


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class UsageBreakdownStore:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path).absolute()

    @staticmethod
    def _schema(db: sqlite3.Connection) -> None:
        version = db.execute('PRAGMA user_version').fetchone()[0]
        app = db.execute('PRAGMA application_id').fetchone()[0]
        tables = {r[0] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if version == 0 and app == 0 and not tables:
            # No data yet is a normal state (fresh install, "Sampling has not
            # begun" per the spec's empty states) -- always safe to bootstrap
            # the schema on first touch, read or write, since CREATE TABLE
            # only runs when the file is genuinely empty. A real mismatch
            # (existing tables with the wrong shape/version) still raises
            # below regardless of this branch.
            db.execute(
                'CREATE TABLE allowance_sample ('
                ' id INTEGER PRIMARY KEY AUTOINCREMENT,'
                ' provider TEXT NOT NULL,'
                ' window_kind TEXT NOT NULL,'
                ' window_scope TEXT NOT NULL,'
                ' raw_utilization REAL,'
                ' resets_at TEXT,'
                ' source_observed_at TEXT,'
                ' server_received_at TEXT NOT NULL,'
                ' source_version TEXT,'
                ' quality TEXT NOT NULL,'
                ' account_ref TEXT,'
                ' created_at TEXT NOT NULL,'
                ' UNIQUE(provider, window_kind, window_scope, source_observed_at)'
                ')'
            )
            db.execute('CREATE INDEX idx_allowance_sample_provider_window '
                       'ON allowance_sample(provider, window_kind, window_scope, server_received_at)')
            db.execute(
                'CREATE TABLE session_fact ('
                ' session_id TEXT PRIMARY KEY,'
                ' provider TEXT NOT NULL,'
                ' project_id TEXT,'
                ' character TEXT,'
                ' trigger_type TEXT,'
                ' requested_model TEXT,'
                ' observed_model TEXT,'
                ' status TEXT NOT NULL,'
                ' started_at TEXT,'
                ' ended_at TEXT,'
                ' input_fresh INTEGER,'
                ' input_cache_write INTEGER,'
                ' input_cache_read INTEGER,'
                ' input_processed_total INTEGER,'
                ' output_tokens INTEGER,'
                ' output_reasoning INTEGER,'
                ' token_source TEXT,'
                ' token_coverage TEXT NOT NULL,'
                ' included INTEGER NOT NULL DEFAULT 1,'
                ' housekeeping INTEGER NOT NULL DEFAULT 0,'
                ' parent_session_id TEXT,'
                ' updated_at TEXT NOT NULL,'
                ' created_at TEXT NOT NULL'
                ')'
            )
            db.execute('CREATE INDEX idx_session_fact_ended '
                       'ON session_fact(ended_at)')
            db.execute('CREATE INDEX idx_session_fact_project '
                       'ON session_fact(project_id, character, trigger_type, observed_model)')
            db.execute(
                'CREATE TABLE code_delta ('
                ' session_id TEXT PRIMARY KEY,'
                ' added INTEGER,'
                ' deleted INTEGER,'
                ' status TEXT NOT NULL,'
                ' reason TEXT,'
                ' branch TEXT,'
                ' base_commit TEXT,'
                ' head_commits TEXT,'
                ' created_at TEXT NOT NULL,'
                ' FOREIGN KEY(session_id) REFERENCES session_fact(session_id)'
                ')'
            )
            db.execute(_SESSION_CHECKPOINT_TABLE_SQL)
            for stmt in _SESSION_CHECKPOINT_INDEX_SQL:
                db.execute(stmt)
            db.execute(_CODE_DELTA_LIFETIME_TABLE_SQL)
            db.execute(f'PRAGMA application_id={APPLICATION_ID}')
            db.execute(f'PRAGMA user_version={SCHEMA_VERSION}')
            return
        if version == 1 and app == APPLICATION_ID and (tables - {'sqlite_sequence'}) == _V1_TABLES:
            # Pre-existing v1 install (no session_checkpoint table yet): add it
            # in place rather than raising, so a dev/test db created before
            # P1-3 doesn't need to be deleted by hand.
            db.execute(_SESSION_CHECKPOINT_TABLE_SQL)
            for stmt in _SESSION_CHECKPOINT_INDEX_SQL:
                db.execute(stmt)
            version = 3
            db.execute('PRAGMA user_version=3')
        elif version == 2 and app == APPLICATION_ID and (tables - {'sqlite_sequence'}) == _V3_TABLES:
            # v2->v3: the v2 table carried a table-level
            # `UNIQUE(session_id, checkpoint_type)`, which silently discarded
            # every completion checkpoint after the session's FIRST turn
            # (docs/_journal/4668eafc-mc998-fenn-review.md "2026-09-28
            # re-review" finding 3, P1-3) while session_fact kept advancing.
            # SQLite can't drop a table-level constraint in place, so rebuild
            # the table under the new shape (a partial unique index instead)
            # and copy every existing row across unchanged.
            db.execute('ALTER TABLE session_checkpoint RENAME TO session_checkpoint_v2')
            db.execute(_SESSION_CHECKPOINT_TABLE_SQL)
            db.execute(
                'INSERT INTO session_checkpoint (session_id, provider, checkpoint_type, '
                ' observed_at, input_fresh, input_cache_write, input_cache_read, '
                ' input_processed_total, output_tokens, output_reasoning, token_coverage, created_at) '
                'SELECT session_id, provider, checkpoint_type, observed_at, input_fresh, '
                ' input_cache_write, input_cache_read, input_processed_total, output_tokens, '
                ' output_reasoning, token_coverage, created_at FROM session_checkpoint_v2'
            )
            db.execute('DROP TABLE session_checkpoint_v2')
            for stmt in _SESSION_CHECKPOINT_INDEX_SQL:
                db.execute(stmt)
            version = 3
            db.execute('PRAGMA user_version=3')
        if version == 3 and app == APPLICATION_ID and (
                {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                - {'sqlite_sequence'}) == _V3_TABLES:
            # v3->v4 (round 3, P2-5): per-worktree-lifetime LOC rows.
            db.execute(_CODE_DELTA_LIFETIME_TABLE_SQL)
            db.execute(_CODE_DELTA_LIFETIME_SEED_SQL)
            db.execute(f'PRAGMA user_version={SCHEMA_VERSION}')
            return
        if (app != APPLICATION_ID or version != SCHEMA_VERSION
              or (tables - {'sqlite_sequence'}) != _TABLES):
            raise SchemaError(
                f'Unsupported usage_breakdown schema: version={version}, '
                f'application={app}, tables={sorted(tables)}')

    @contextmanager
    def _connection(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(str(self.db_path), timeout=30, isolation_level=None)
        try:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            self._schema(db)
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    # ── allowance_sample ────────────────────────────────────────────────

    def record_allowance_sample(
        self, *, provider: str, window_kind: str, window_scope: str,
        raw_utilization: Optional[float], resets_at: Optional[str],
        source_observed_at: Optional[str], source_version: Optional[str] = None,
        quality: str = 'ok', account_ref: Optional[str] = None,
    ) -> bool:
        """Insert one allowance reading. Returns False (no-op) when a row
        already exists for this exact (provider, window_kind, window_scope,
        source_observed_at) identity -- a cache hit or a repeated poll of an
        unchanged source must not create a second sample.

        `raw_utilization` is validated finite 0-100 when present; an invalid
        value is stored with quality='invalid' rather than silently clamped,
        so the UI can show it as a data-quality problem instead of a real 0.
        """
        if raw_utilization is not None:
            try:
                raw_utilization = float(raw_utilization)
            except (TypeError, ValueError):
                raw_utilization = None
                quality = 'invalid'
            else:
                if not (0.0 <= raw_utilization <= 100.0):
                    quality = 'invalid'
        with self._connection(write=True) as db:
            try:
                db.execute(
                    'INSERT INTO allowance_sample '
                    '(provider, window_kind, window_scope, raw_utilization, resets_at, '
                    ' source_observed_at, server_received_at, source_version, quality, '
                    ' account_ref, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                    (provider, window_kind, window_scope, raw_utilization, resets_at,
                     source_observed_at, _now(), source_version, quality, account_ref, _now()),
                )
            except sqlite3.IntegrityError:
                return False
        return True

    def list_allowance_samples(self, *, provider: str, window_kind: str,
                                window_scope: str, since: Optional[str] = None) -> list[dict]:
        with self._connection(write=False) as db:
            q = ('SELECT * FROM allowance_sample WHERE provider=? AND window_kind=? '
                 'AND window_scope=?')
            params: list[Any] = [provider, window_kind, window_scope]
            if since:
                q += ' AND server_received_at >= ?'
                params.append(since)
            q += ' ORDER BY server_received_at ASC'
            return [dict(r) for r in db.execute(q, params).fetchall()]

    # ── session_fact ────────────────────────────────────────────────────

    def upsert_session_fact(self, session_id: str, fields: dict) -> None:
        """Insert or update the one row for `session_id`. `created_at` is
        preserved across updates (set only on first insert); every other
        column is overwritten with the caller's current view, so a later
        checkpoint/completion call simply replaces the running snapshot."""
        if not session_id:
            raise ValueError('session_id is required')
        cols = ['provider', 'project_id', 'character', 'trigger_type',
                'requested_model', 'observed_model', 'status', 'started_at',
                'ended_at', 'input_fresh', 'input_cache_write', 'input_cache_read',
                'input_processed_total', 'output_tokens', 'output_reasoning',
                'token_source', 'token_coverage', 'included', 'housekeeping',
                'parent_session_id']
        row = {c: fields.get(c) for c in cols}
        row['included'] = 1 if fields.get('included', True) else 0
        row['housekeeping'] = 1 if fields.get('housekeeping', False) else 0
        row['token_coverage'] = row.get('token_coverage') or 'unavailable'
        row['status'] = row.get('status') or 'running'
        row['provider'] = row.get('provider') or 'claude'
        now = _now()
        with self._connection(write=True) as db:
            existing = db.execute(
                'SELECT created_at FROM session_fact WHERE session_id=?',
                (session_id,)).fetchone()
            created_at = existing['created_at'] if existing else now
            placeholders = ', '.join(f':{c}' for c in cols)
            assignments = ', '.join(f'{c}=excluded.{c}' for c in cols)
            params = dict(row)
            params['session_id'] = session_id
            params['updated_at'] = now
            params['created_at'] = created_at
            db.execute(
                f'INSERT INTO session_fact (session_id, {", ".join(cols)}, updated_at, created_at) '
                f'VALUES (:session_id, {placeholders}, :updated_at, :created_at) '
                f'ON CONFLICT(session_id) DO UPDATE SET {assignments}, updated_at=excluded.updated_at',
                params,
            )

    def mark_session_running(self, session_id: str) -> bool:
        """Mark `session_id` as mid-turn: flip its fact to status='running'
        when a RESUMED session starts a new turn (round 3, P1-2), or when an
        automatic wake starts one nobody sent (round 4). The fact otherwise
        stays 'completed' at its previous turn's end until the new turn
        completes, and the aggregate would read the last completion as the
        session's end while it is visibly working. Counters and `ended_at`
        are kept: the aggregate treats everything after the last completion
        as one unmeasured, still-open span. Any status is reopened, including
        'ended_unknown' -- a session reconcile closed that is in fact still
        working must come back.

        A first turn with no fact yet gets a counter-less 'running' fact
        started at its baseline (round 4). The baseline alone already reads
        as open, but it never changes, so a turn start on a baseline-only
        session left nothing newer for `close_session_ended_unknown` to see:
        a reconcile working from an older live-set snapshot closed a session
        that had just started a turn. Returns False only when there is no
        fact and no baseline."""
        if not session_id:
            raise ValueError('session_id is required')
        now = _now()
        with self._connection(write=True) as db:
            db.execute("UPDATE session_fact SET status='running', updated_at=? WHERE session_id=?",
                       (now, session_id))
            if db.execute('SELECT changes()').fetchone()[0] > 0:
                return True
            base = db.execute("SELECT provider, observed_at FROM session_checkpoint "
                              "WHERE session_id=? AND checkpoint_type='baseline'",
                              (session_id,)).fetchone()
            if not base:
                return False
            db.execute(
                'INSERT INTO session_fact (session_id, provider, status, started_at, '
                ' token_coverage, updated_at, created_at) '
                "VALUES (?, ?, 'running', ?, 'unavailable', ?, ?)",
                (session_id, base['provider'] or 'claude', base['observed_at'], now, now))
            return True

    def list_open_sessions(self) -> list[dict]:
        """Every session the aggregate reads as still running: a fact with
        status 'running' (or no ended_at), or a first turn -- a baseline
        checkpoint with no fact and no completion yet. Each row is
        {'session_id', 'provider', 'started_at', 'last_activity_at',
        'generation'}; `last_activity_at` is the newest durable time held for
        it (the fact's updated_at, which every turn start sets, or the
        baseline's observed_at). `generation` is the fact's updated_at, None
        for a fact-less first turn -- pass it back to
        `close_session_ended_unknown` so a turn that starts after this read
        is never closed on its strength. `reconcile_dead_sessions` reads this."""
        with self._connection(write=False) as db:
            rows = [dict(r) for r in db.execute(
                'SELECT session_id, provider, started_at, updated_at AS last_activity_at, '
                ' updated_at AS generation '
                "FROM session_fact WHERE status='running' OR ended_at IS NULL").fetchall()]
            rows += [dict(r) for r in db.execute(
                'SELECT c.session_id, c.provider, c.observed_at AS started_at, '
                ' c.observed_at AS last_activity_at, NULL AS generation FROM session_checkpoint c '
                "WHERE c.checkpoint_type='baseline' "
                ' AND NOT EXISTS (SELECT 1 FROM session_fact f WHERE f.session_id=c.session_id) '
                ' AND NOT EXISTS (SELECT 1 FROM session_checkpoint d '
                "  WHERE d.session_id=c.session_id AND d.checkpoint_type='completion')").fetchall()]
            return rows

    def list_ended_unknown_session_ids(self) -> list[str]:
        """Sessions `close_session_ended_unknown` closed and nothing has
        overwritten since -- the candidates `reconcile_dead_sessions`
        reopens when their MC session turns out to be live after all."""
        with self._connection(write=False) as db:
            return [r[0] for r in db.execute(
                "SELECT session_id FROM session_fact WHERE status='ended_unknown'").fetchall()]

    def close_session_ended_unknown(self, session_id: str, *, provider: str,
                                    started_at: Optional[str], ended_at: str,
                                    generation: Optional[str] = None) -> bool:
        """Close a session that is still open in the store but no longer
        live (it crashed mid-turn, or the server restarted under it):
        status 'ended_unknown', ended_at = when it was observed gone. Left
        open, its span has no end and overlaps every later interval, so one
        crash blocked the calibration gate and marked every window
        incomplete until the 90-day prune. Counters are kept; the aggregate
        reads last checkpoint -> ended_at as unmeasured, so the windows it
        actually overlapped stay incomplete. A first turn (no fact yet) gets
        a counter-less fact carrying the same status.

        Conditional on nothing newer having been written since
        `list_open_sessions` produced `generation` (round 4): a fact is closed
        only while still open AND its updated_at still equals `generation`,
        so a completion or a new turn start (mark_session_running) landing
        between the read and this write wins. `generation=None` is a
        fact-less first turn: closed only while it still has no fact and no
        completion. A later turn re-marks it 'running' and its completion
        overwrites the whole fact. Returns True when a row was closed."""
        if not session_id:
            raise ValueError('session_id is required')
        now = _now()
        with self._connection(write=True) as db:
            if generation is not None:
                db.execute(
                    "UPDATE session_fact SET status='ended_unknown', ended_at=?, updated_at=? "
                    "WHERE session_id=? AND (status='running' OR ended_at IS NULL) AND updated_at=?",
                    (ended_at, now, session_id, generation))
                return db.execute('SELECT changes()').fetchone()[0] > 0
            if db.execute('SELECT 1 FROM session_fact WHERE session_id=?',
                          (session_id,)).fetchone():
                return False
            if db.execute("SELECT 1 FROM session_checkpoint WHERE session_id=? "
                          "AND checkpoint_type='completion'", (session_id,)).fetchone():
                return False
            db.execute(
                'INSERT INTO session_fact (session_id, provider, status, started_at, ended_at, '
                ' token_coverage, updated_at, created_at) '
                "VALUES (?, ?, 'ended_unknown', ?, ?, 'unavailable', ?, ?)",
                (session_id, provider or 'claude', started_at, ended_at, now, now))
            return True

    def get_session_fact(self, session_id: str) -> Optional[dict]:
        with self._connection(write=False) as db:
            row = db.execute('SELECT * FROM session_fact WHERE session_id=?',
                              (session_id,)).fetchone()
            return dict(row) if row else None

    def list_session_facts(self, *, since: Optional[str] = None,
                            include_excluded: bool = True) -> list[dict]:
        with self._connection(write=False) as db:
            q = 'SELECT * FROM session_fact'
            clauses = []
            params: list[Any] = []
            if since:
                clauses.append('(ended_at IS NULL OR ended_at >= ?)')
                params.append(since)
            if not include_excluded:
                clauses.append('included = 1')
            if clauses:
                q += ' WHERE ' + ' AND '.join(clauses)
            q += ' ORDER BY COALESCE(ended_at, started_at) ASC'
            return [dict(r) for r in db.execute(q, params).fetchall()]

    # ── session_checkpoint ──────────────────────────────────────────────

    def record_session_checkpoint(
        self, *, session_id: str, provider: str, checkpoint_type: str, observed_at: str,
        input_fresh: Optional[int] = None, input_cache_write: Optional[int] = None,
        input_cache_read: Optional[int] = None, input_processed_total: Optional[int] = None,
        output_tokens: Optional[int] = None, output_reasoning: Optional[int] = None,
        token_coverage: str = 'unavailable',
    ) -> bool:
        """Record one timestamped cumulative-token snapshot for `session_id`.
        Returns False (no-op) when this would violate one of the two partial
        unique indexes: a 'baseline' row already exists for this session (a
        repeated dispatch-pending call is safe to call more than once), or a
        'completion' row already exists at this EXACT `observed_at` (an exact
        retry). A later turn's completion at a new `observed_at` always
        inserts a new row -- callers must never assume this call overwrites
        a previous completion. `provider` is captured on the baseline row
        (known at dispatch) so calibration can filter to the right provider
        before a still-running session ever gets a session_fact."""
        if not session_id:
            raise ValueError('session_id is required')
        if checkpoint_type not in ('baseline', 'completion'):
            raise ValueError(f'invalid checkpoint_type: {checkpoint_type!r}')
        if not observed_at:
            raise ValueError('observed_at is required')
        with self._connection(write=True) as db:
            try:
                db.execute(
                    'INSERT INTO session_checkpoint '
                    '(session_id, provider, checkpoint_type, observed_at, input_fresh, input_cache_write, '
                    ' input_cache_read, input_processed_total, output_tokens, output_reasoning, '
                    ' token_coverage, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                    (session_id, provider, checkpoint_type, observed_at, input_fresh, input_cache_write,
                     input_cache_read, input_processed_total, output_tokens, output_reasoning,
                     token_coverage, _now()),
                )
            except sqlite3.IntegrityError:
                return False
        return True

    def list_session_checkpoints(self, *, since: Optional[str] = None) -> list[dict]:
        """All checkpoints ordered by (session_id, observed_at) -- the shape
        `usage_breakdown_aggregate.py` needs to walk each session's baseline
        -> completion pairs and derive per-interval deltas."""
        with self._connection(write=False) as db:
            q = 'SELECT * FROM session_checkpoint'
            params: list[Any] = []
            if since:
                q += ' WHERE observed_at >= ?'
                params.append(since)
            q += ' ORDER BY session_id ASC, observed_at ASC'
            return [dict(r) for r in db.execute(q, params).fetchall()]

    def get_session_checkpoints(self, session_id: str) -> dict[str, Any]:
        """{'baseline': row|None, 'completions': [row, ...] oldest first} for
        one session_id. Callers must never assume `completions` has at most
        one entry (P1-3, docs/_journal/4668eafc-mc998-fenn-review.md
        "2026-09-28 re-review" finding 3): Mode-A completions fire per turn,
        so a multi-turn session accumulates one completion row per turn."""
        with self._connection(write=False) as db:
            rows = db.execute(
                'SELECT * FROM session_checkpoint WHERE session_id=? ORDER BY observed_at ASC',
                (session_id,)).fetchall()
            result: dict[str, Any] = {'baseline': None, 'completions': []}
            for r in rows:
                d = dict(r)
                if d['checkpoint_type'] == 'baseline':
                    result['baseline'] = d
                else:
                    result['completions'].append(d)
            return result

    # ── code_delta ──────────────────────────────────────────────────────

    def upsert_code_delta(self, session_id: str, fields: dict) -> None:
        """Reviewer re-review finding #5 (P2-5, docs/_journal/4668eafc-mc998-
        fenn-review.md "2026-09-28 re-review" and "round 3"): the dispatch
        path allows a session_id's worktree to be removed and RE-created
        (e.g. after an earlier merge-back), which captures a fresh baseline
        at the new worktree's own HEAD -- a valid 'ok' count for that new
        lifetime, not a correction of the earlier one. An 'ok' capture is
        the cumulative count for ITS baseline's lifetime only, so it
        replaces just that (session_id, base_commit) row in
        code_delta_lifetime; the code_delta row is then rewritten as the sum
        over every lifetime. Repeating a capture is idempotent, and no
        lifetime's count is ever overwritten by another's. (Accumulating
        onto the single code_delta row, as round 2 did, let the next
        same-baseline capture replace the sum with one lifetime's count.)"""
        if not session_id:
            raise ValueError('session_id is required')
        cols = ['added', 'deleted', 'status', 'reason', 'branch', 'base_commit', 'head_commits']
        row = {c: fields.get(c) for c in cols}
        row['status'] = row.get('status') or 'unavailable'
        with self._connection(write=True) as db:
            existing = db.execute(
                'SELECT * FROM code_delta WHERE session_id=?', (session_id,)).fetchone()
            if row['status'] != 'ok':
                if existing and existing['status'] == 'ok':
                    # A later completion in the same chat (e.g. after
                    # merge-back already removed the worktree) must never
                    # clobber an already-captured LOC count with
                    # 'unavailable' (MC-998 review finding #5).
                    return
            elif row['base_commit']:
                db.execute(
                    'INSERT INTO code_delta_lifetime (session_id, base_commit, added, deleted, '
                    ' head_commits, updated_at) VALUES (?,?,?,?,?,?) '
                    'ON CONFLICT(session_id, base_commit) DO UPDATE SET added=excluded.added, '
                    ' deleted=excluded.deleted, head_commits=excluded.head_commits, '
                    ' updated_at=excluded.updated_at',
                    (session_id, row['base_commit'], row['added'] or 0, row['deleted'] or 0,
                     row['head_commits'], _now()))
                lifetimes = db.execute(
                    'SELECT added, deleted, head_commits FROM code_delta_lifetime '
                    'WHERE session_id=? ORDER BY rowid ASC', (session_id,)).fetchall()
                row['added'] = sum(r['added'] for r in lifetimes)
                row['deleted'] = sum(r['deleted'] for r in lifetimes)
                commits: list[str] = []
                for r in lifetimes:
                    for c in (r['head_commits'] or '').split(','):
                        if c and c not in commits:
                            commits.append(c)
                row['head_commits'] = ','.join(commits)
            placeholders = ', '.join(f':{c}' for c in cols)
            assignments = ', '.join(f'{c}=excluded.{c}' for c in cols)
            params = dict(row)
            params['session_id'] = session_id
            params['created_at'] = _now()
            db.execute(
                f'INSERT INTO code_delta (session_id, {", ".join(cols)}, created_at) '
                f'VALUES (:session_id, {placeholders}, :created_at) '
                f'ON CONFLICT(session_id) DO UPDATE SET {assignments}',
                params,
            )

    def get_code_delta(self, session_id: str) -> Optional[dict]:
        with self._connection(write=False) as db:
            row = db.execute('SELECT * FROM code_delta WHERE session_id=?',
                              (session_id,)).fetchone()
            return dict(row) if row else None

    # ── pruning ─────────────────────────────────────────────────────────

    def prune_older_than(self, *, days: int = 90, batch_size: int = 500) -> dict:
        """Delete allowance_sample rows older than `days` by server_received_at,
        and session_fact (+ their code_delta) rows COMPLETED more than `days`
        ago by ended_at. Running facts (ended_at IS NULL) are never pruned by
        age. Bounded batches so a large backlog can't hold the write lock for
        one huge transaction. Returns counts removed; never raises -- callers
        must log failures, not crash the sampler loop over a prune error.
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        removed = {'allowance_sample': 0, 'session_fact': 0, 'code_delta': 0,
                   'session_checkpoint': 0}
        with self._connection(write=True) as db:
            while True:
                ids = [r[0] for r in db.execute(
                    'SELECT id FROM allowance_sample WHERE server_received_at < ? LIMIT ?',
                    (cutoff, batch_size)).fetchall()]
                if not ids:
                    break
                db.executemany('DELETE FROM allowance_sample WHERE id=?',
                                [(i,) for i in ids])
                removed['allowance_sample'] += len(ids)
                if len(ids) < batch_size:
                    break
            while True:
                sids = [r[0] for r in db.execute(
                    'SELECT session_id FROM session_fact '
                    'WHERE ended_at IS NOT NULL AND ended_at < ? LIMIT ?',
                    (cutoff, batch_size)).fetchall()]
                if not sids:
                    break
                db.executemany('DELETE FROM code_delta WHERE session_id=?',
                                [(s,) for s in sids])
                removed['code_delta'] += db.execute('SELECT changes()').fetchone()[0]
                db.executemany('DELETE FROM code_delta_lifetime WHERE session_id=?',
                                [(s,) for s in sids])
                db.executemany('DELETE FROM session_checkpoint WHERE session_id=?',
                                [(s,) for s in sids])
                removed['session_checkpoint'] += db.execute('SELECT changes()').fetchone()[0]
                db.executemany('DELETE FROM session_fact WHERE session_id=?',
                                [(s,) for s in sids])
                removed['session_fact'] += len(sids)
                if len(sids) < batch_size:
                    break
            # Orphaned checkpoints: a session whose session_fact was already
            # pruned by an earlier run (schema-version-2 upgrade path) or
            # whose checkpoint predates its session_fact's own retention --
            # bound by observed_at directly so this table can't grow forever
            # off a session_fact row that never gets old enough itself
            # (e.g. a long-running/never-completed session).
            while True:
                ids = [r[0] for r in db.execute(
                    'SELECT id FROM session_checkpoint WHERE observed_at < ? LIMIT ?',
                    (cutoff, batch_size)).fetchall()]
                if not ids:
                    break
                db.executemany('DELETE FROM session_checkpoint WHERE id=?',
                                [(i,) for i in ids])
                removed['session_checkpoint'] += len(ids)
                if len(ids) < batch_size:
                    break
        return removed

    def coverage_begins(self) -> Optional[str]:
        """Earliest `server_received_at` across all allowance samples, or None
        if sampling has never produced a row -- the spec's 'Coverage begins
        <date>' UI state."""
        with self._connection(write=False) as db:
            row = db.execute('SELECT MIN(server_received_at) FROM allowance_sample').fetchone()
            return row[0] if row and row[0] else None
