"""Cold session search — SQLite FTS5 over Claude Code transcripts + the agent
log, living UNDERNEATH the curated memory index (mc/memory.py's `_memory_search`).

MC-918. The curated corpus (topic files, archive, managed region) is a small
hand-tended set; everything a project has ever actually said lives in its
session transcripts and nowhere else is it searchable. Hermes Agent's
`session_search` tool (docs/research/HERMES_AGENT_COMPETITIVE_READ.md §6.7)
does exactly this: FTS5 keyword search over every stored session, returning
real messages rather than summaries. The Step-7 semantic-search deferral
(`decision-step7-semantic-search-deferral`) does NOT apply here — that
deferral is about re-ranking the CURATED corpus with embeddings, and its
blocking objection is that Step-6 turns MEMORY.md into a continuous-write
target that a semantic index cannot stay fresh against. Transcripts are the
opposite: each file is append-only and, once a session ends, immutable. A
per-file (mtime, size) fingerprint is therefore an exact incremental-reindex
signal with no staleness window, which is the whole reason keyword search
over transcripts is cheap where semantic search over the live index was not.

Three things this module is deliberately NOT (mirrors mc/memory_delivery.py):

1. **Not part of the read floor.** The dispatch-time auto-injected "RELEVANT
   MEMORY" block calls `_memory_search` directly and this module is never in
   that path — cold hits are real message excerpts, not curated notes, and
   auto-injecting them on every turn would blow the prompt budget for a
   channel that is supposed to be on-demand. Only the explicit
   `/memory/search` HTTP route (the mc-memory-search skill's surface) appends
   a cold tier.
2. **Not load-bearing.** Every entry point swallows and logs. An index build
   or search failure must never break the curated search endpoint it sits
   underneath.
3. **Not under DATA_DIR.** `load_projects()` treats every `*.json` there as a
   project record (CLAUDE.md "DATA_DIR pollution"). The db is a `.db` file
   living beside MEMORY.md — `_mem_corpus` globs `*.md`, so it is inert to
   the curated corpus, exactly like `memory_delivery.py`'s sidecar.
"""
from __future__ import annotations

import json
import sqlite3
import time as _time
from pathlib import Path
from typing import Any, Optional

from mc import state
from mc.core import _log

import mc.agent_runtime as _agent_runtime

DB_FILE = 'session_search.db'

# Below this many characters a message is filler ("ok", "yes", "continue")
# and indexing it only adds noise to every future query.
_MIN_TEXT_CHARS = 12

# A single agent_log entry's task/summary can run long; cap what gets indexed
# per field so one enormous summary can't dominate the index's size.
_MAX_FIELD_CHARS = 20000


def enabled() -> bool:
    try:
        return bool(state.CONFIG.get('session_fts_enabled', True))
    except Exception:
        return True


def _cold_k_default() -> int:
    try:
        return max(0, int(state.CONFIG.get('session_fts_cold_k', 5) or 0))
    except (TypeError, ValueError):
        return 5


def _db_path(project) -> Optional[Path]:
    """Beside MEMORY.md, never under DATA_DIR — see module docstring."""
    from mc import memory as _mem
    try:
        base = _mem._get_memory_path(project)
    except Exception:
        return None
    if not base:
        return None
    return Path(base).parent / DB_FILE


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute('PRAGMA journal_mode=WAL')
    _ensure_schema(conn)
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS session_fts USING fts5(
            text, session_id UNINDEXED, role UNINDEXED, ts UNINDEXED,
            source_file UNINDEXED
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS indexed_files (
            path TEXT PRIMARY KEY,
            mtime_ns INTEGER,
            size INTEGER,
            rows INTEGER,
            indexed_at TEXT
        )
    """)
    conn.commit()


# ── transcript discovery ─────────────────────────────────────────────────────

def _iter_transcript_files(project_path: str):
    """Every Claude Code transcript .jsonl for a project, across every encoded
    dir spelling AND per-agent worktree dirs. Mirrors ClaudeRuntime.list_sessions
    (mc/memory.py:_find_transcript_file's worktree fallback), but unbounded —
    the eval surface needs every session, not the most-recent N.

    ALSO includes nested subagent transcripts (a Task-tool dispatch or CC
    Workflow fan-out agent), via iter_transcript_files_in_dir() — see that
    helper's docstring. This module's whole premise is "everything a project
    has ever actually said lives in its session transcripts" (module
    docstring); a subagent's messages are as real as its parent's, so
    excluding its nested file would silently under-index this cold-search
    tier the same way it under-reported the Documents tab pre-MC-939.
    """
    rt = _agent_runtime.get_runtime('claude')
    home = _agent_runtime._CLAUDE_HOME
    candidates = [home / e for e in rt._encoded_dir_candidates(project_path)]  # pyright: ignore[reportAttributeAccessIssue]
    for e in list(rt._encoded_dir_candidates(project_path)):  # pyright: ignore[reportAttributeAccessIssue]
        try:
            candidates.extend(sorted(home.glob(f'{e}--clayrune-agents-*')))
        except OSError:
            continue
    seen: set = set()
    for d in candidates:
        for f in sorted(_agent_runtime.iter_transcript_files_in_dir(d, seen)):
            yield f


# ── incremental indexing ─────────────────────────────────────────────────────

def _file_fingerprint(path: Path):
    try:
        st = path.stat()
        return st.st_mtime_ns, st.st_size
    except OSError:
        return None


def _already_indexed(conn: sqlite3.Connection, key: str, fp) -> bool:
    if fp is None:
        return True
    row = conn.execute(
        'SELECT mtime_ns, size FROM indexed_files WHERE path = ?', (key,)
    ).fetchone()
    return bool(row) and tuple(row) == fp


def _record_indexed(conn: sqlite3.Connection, key: str, fp, rows: int) -> None:
    from mc.core import now_iso
    conn.execute(
        'INSERT INTO indexed_files (path, mtime_ns, size, rows, indexed_at) '
        'VALUES (?, ?, ?, ?, ?) '
        'ON CONFLICT(path) DO UPDATE SET mtime_ns=excluded.mtime_ns, '
        'size=excluded.size, rows=excluded.rows, indexed_at=excluded.indexed_at',
        (key, fp[0], fp[1], rows, now_iso()))


def _index_transcript_file(conn: sqlite3.Connection, path: Path) -> Optional[int]:
    """(Re)index one transcript file if it is new or changed. Returns the
    number of rows written, or None if it was already up to date."""
    key = str(path)
    fp = _file_fingerprint(path)
    if fp is None:
        return None
    if _already_indexed(conn, key, fp):
        return None
    session_id = path.stem
    rt = _agent_runtime.get_runtime('claude')
    messages = rt.parse_transcript_file(path, max_messages=10**9)  # pyright: ignore[reportAttributeAccessIssue]
    conn.execute('DELETE FROM session_fts WHERE source_file = ?', (key,))
    rows = 0
    for msg in messages:
        if msg.get('role') not in ('user', 'assistant'):
            continue
        text = (msg.get('text') or '').strip()
        if len(text) < _MIN_TEXT_CHARS:
            continue
        conn.execute(
            'INSERT INTO session_fts (text, session_id, role, ts, source_file) '
            'VALUES (?, ?, ?, ?, ?)',
            (text[:_MAX_FIELD_CHARS], session_id, msg.get('role', ''),
             msg.get('timestamp', ''), key))
        rows += 1
    _record_indexed(conn, key, fp, rows)
    return rows


def _index_agent_log(conn: sqlite3.Connection, data_dir: Path, project_id: str) -> Optional[int]:
    """(Re)index one project's agent_log.json (task + summary per entry) if
    changed. Small and rewritten often, so a full reparse on any change is
    cheap — unlike transcripts, there is no append-only guarantee to exploit.
    """
    path = data_dir / f'{project_id}_agent_log.json'
    if not path.is_file():
        return None
    key = str(path)
    fp = _file_fingerprint(path)
    if fp is None or _already_indexed(conn, key, fp):
        return None
    try:
        entries = json.loads(path.read_text(encoding='utf-8'))
    except Exception as e:
        _log(f'[memory_fts] agent_log parse failed for {project_id}: {e}')
        return None
    conn.execute('DELETE FROM session_fts WHERE source_file = ?', (key,))
    rows = 0
    for e in entries if isinstance(entries, list) else []:
        session_id = e.get('claude_session_id') or e.get('session_id') or ''
        ts = e.get('started_at') or e.get('ts') or ''
        for role, field in (('task', 'task'), ('summary', 'summary')):
            text = str(e.get(field) or '').strip()
            if len(text) < _MIN_TEXT_CHARS:
                continue
            conn.execute(
                'INSERT INTO session_fts (text, session_id, role, ts, source_file) '
                'VALUES (?, ?, ?, ?, ?)',
                (text[:_MAX_FIELD_CHARS], session_id, role, ts, key))
            rows += 1
    _record_indexed(conn, key, fp, rows)
    return rows


def build_index(project, *, data_dir: Optional[Path] = None,
                max_files: Optional[int] = None) -> dict:
    """Incrementally (re)build one project's cold session-search index.

    Unchanged files (same mtime+size as last run) are skipped entirely — the
    only cost on a warm cache is one stat() per file. Never raises; a failure
    on one file is logged and the rest of the batch still runs.
    """
    stats = {'files_seen': 0, 'files_indexed': 0, 'rows_written': 0,
              'agent_log_indexed': False, 'elapsed_s': 0.0}
    if not enabled():
        return stats
    t0 = _time.time()
    db_path = _db_path(project)
    if db_path is None:
        return stats
    project_path = (project or {}).get('project_path') or ''
    conn = _connect(db_path)
    try:
        if project_path:
            n = 0
            for f in _iter_transcript_files(project_path):
                if max_files and n >= max_files:
                    break
                n += 1
                stats['files_seen'] += 1
                try:
                    rows = _index_transcript_file(conn, f)
                except Exception as e:
                    _log(f'[memory_fts] index failed for {f}: {e}')
                    continue
                if rows is not None:
                    stats['files_indexed'] += 1
                    stats['rows_written'] += rows
        if data_dir is not None:
            pid = (project or {}).get('id') or ''
            if pid:
                try:
                    rows = _index_agent_log(conn, Path(data_dir), pid)
                except Exception as e:
                    _log(f'[memory_fts] agent_log index failed for {pid}: {e}')
                    rows = None
                if rows is not None:
                    stats['agent_log_indexed'] = True
                    stats['rows_written'] += rows
        conn.commit()
    finally:
        conn.close()
    stats['elapsed_s'] = round(_time.time() - t0, 3)
    return stats


# ── search ────────────────────────────────────────────────────────────────

def cold_search(project, query: str, limit: int = 5,
                 exclude_session_ids=None) -> list[dict[str, Any]]:
    """Ranked FTS5 search over the cold session-search index.

    Returns [{tier:'cold', session_id, role, timestamp, source, snippet,
    score}], best match first (SQLite's bm25() is more-negative-is-better; we
    sort ascending and leave the raw score in place rather than inverting it,
    so it stays comparable to sqlite's own docs). Returns [] on any failure —
    including "no index built yet" — never raises. `snippet` is a real
    excerpt from the actual message, not a summary, so a hit is traceable
    back to session_id + timestamp.
    """
    if not enabled() or not query or limit <= 0:
        return []
    from mc import memory as _mem
    terms = _mem._mem_tokens(query)
    if not terms:
        return []
    db_path = _db_path(project)
    if db_path is None or not db_path.is_file():
        return []
    match = ' OR '.join(terms)
    exclude = set(exclude_session_ids or [])
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            rows = conn.execute(
                "SELECT session_id, role, ts, source_file, "
                "snippet(session_fts, 0, '», ', ' «', ' … ', 16), "
                "bm25(session_fts) AS score "
                "FROM session_fts WHERE session_fts MATCH ? "
                "ORDER BY score LIMIT ?",
                (match, limit + len(exclude))
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error as e:
        _log(f'[memory_fts] cold search failed: {e}')
        return []
    out = []
    for session_id, role, ts, source_file, snippet, score in rows:
        if session_id in exclude:
            continue
        out.append({
            'tier': 'cold',
            'file': f'session:{session_id}',
            'session_id': session_id,
            'role': role,
            'timestamp': ts,
            'source': source_file,
            'snippet': snippet,
            'score': round(float(score), 4),
        })
        if len(out) >= limit:
            break
    return out
