"""MC-998 durable store for the Usage Breakdown dashboard
(docs/USAGE_BREAKDOWN_SPEC.md, "Sampling and durable facts").

`data/usage_breakdown.sqlite`, OUTSIDE `DATA_DIR` (`data/projects/`) so it
never trips `load_projects()`'s "every *.json in DATA_DIR is a project"
contract. WAL mode, so the caller must gitignore the `-wal`/`-shm` sidecars
alongside the db file itself.

Three tables, schema-versioned via `PRAGMA user_version`:
  - allowance_sample: one row per distinct vendor utilization reading.
    Deduplicated by (provider, window_kind, window_scope, source_observed_at)
    — an unchanged source observation is not a new sample (spec: "a cache hit
    is not a new sample" / "identical event identity/timestamp/values are
    stored once").
  - session_fact: one row per MC session_id, upserted at start/checkpoint/
    completion. Carries token categories, provenance, and an `included` flag
    so housekeeping/internal sessions stay visible-but-markable per spec §4.
  - code_delta: one row per session_id, LOC added/deleted or an `unavailable`
    reason.

No OAuth token, credential, prompt, transcript, source text, or task summary
is ever written here — only the normalized facts the spec's tables define.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

SCHEMA_VERSION = 1
APPLICATION_ID = 0x4D435542  # 'MCUB'
DB_FILENAME = 'usage_breakdown.sqlite'

_TABLES = {'allowance_sample', 'session_fact', 'code_delta'}


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
            db.execute(f'PRAGMA application_id={APPLICATION_ID}')
            db.execute(f'PRAGMA user_version={SCHEMA_VERSION}')
        elif (app != APPLICATION_ID or version != SCHEMA_VERSION
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

    # ── code_delta ──────────────────────────────────────────────────────

    def upsert_code_delta(self, session_id: str, fields: dict) -> None:
        if not session_id:
            raise ValueError('session_id is required')
        cols = ['added', 'deleted', 'status', 'reason', 'branch', 'base_commit', 'head_commits']
        row = {c: fields.get(c) for c in cols}
        row['status'] = row.get('status') or 'unavailable'
        with self._connection(write=True) as db:
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
        removed = {'allowance_sample': 0, 'session_fact': 0, 'code_delta': 0}
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
                db.executemany('DELETE FROM session_fact WHERE session_id=?',
                                [(s,) for s in sids])
                removed['session_fact'] += len(sids)
                if len(sids) < batch_size:
                    break
        return removed

    def coverage_begins(self) -> Optional[str]:
        """Earliest `server_received_at` across all allowance samples, or None
        if sampling has never produced a row -- the spec's 'Coverage begins
        <date>' UI state."""
        with self._connection(write=False) as db:
            row = db.execute('SELECT MIN(server_received_at) FROM allowance_sample').fetchone()
            return row[0] if row and row[0] else None
