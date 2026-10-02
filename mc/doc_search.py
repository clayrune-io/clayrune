"""Project document search — SQLite FTS5 (BM25) over a project's `docs/` tree.

MC-950 (docs/RAG_DOCUMENTS_EXPLORATION.md, decisions D1-D4 in
docs/_journal/13affa86-rag-documents.md). Same pattern as mc/memory_fts.py
(per-file (mtime_ns, size) fingerprint, changed files replaced in one
transaction, vanished files swept) but a separate module on purpose (D3):
the memory files are under active change (MC-964) and the `/memory/search`
response shape must not move.

What it fixes. An agent dispatched into a git worktree can only grep the
tracked docs (25% of this repo's markdown on 2026-10-01); the gitignored
design docs and `_journal/` files exist only in the MAIN checkout. So the
corpus is read from the project's own `project_path` (resolved back to the
main checkout if that path is itself a worktree), never from what git
tracks, and served over HTTP so the caller's filesystem view is irrelevant.

Guards, all reused rather than invented:
  * Names the vault / file-serve allowlist refuses (`.env`, `*.key`, `*.pem`,
    `*credential*`, `id_rsa` ...) are never indexed or returned — the check is
    `project_routes._is_secret_file` itself, so the two cannot drift.
  * `realpath` containment: a symlink that resolves outside `<root>/docs` is
    skipped.
  * The incognito pseudo-project gets nothing (fail closed).

Deliberately NOT here: embeddings, a network call, a model download (D2); a
read-floor / auto-inject hook (D4 — on-demand only); a startup sweep (the
index is brought current lazily on every query, a stat over the docs tree).

The index lives in `~/.clayrune/doc_search/<project_id>.db` — outside
DATA_DIR (CLAUDE.md "DATA_DIR pollution": `load_projects()` treats every
`*.json` there as a project) and outside the repo. It is derived data: delete
it and the next query rebuilds it.
"""
from __future__ import annotations

import os
import re
import sqlite3
import threading
import time as _time
from pathlib import Path
from typing import Any, Optional

from mc.core import _log

DOCS_SUBDIR = 'docs'
DOC_EXTS = {'.md', '.markdown'}
JOURNAL_DIR = '_journal'

# Chunking (Appendix B of the exploration): split on #..#### headings, pack
# paragraphs up to ~1500 chars, the heading path prefixed to the chunk head.
_MAX_CHUNK_CHARS = 1500
_MAX_FILE_BYTES = 2 * 1024 * 1024  # a 2 MB "doc" is a dump, not a document

# D1: _journal is indexed as a lower-weight tier. bm25() is more-negative-is-
# better, so a tier multiplier below 1 pushes a journal hit down the ranking.
_TIER_WEIGHT = {'docs': 1.0, 'journal': 0.6}

# bm25 column weights: (head, body). Heading text counts 3x (Appendix B).
_HEAD_WEIGHT = 3.0

_SCHEMA_VERSION = '1'

_STOPWORDS = frozenset(
    'the and for are was were with that this from have has had not but can '
    'does did why how what when where which who whom its our out all any '
    'you your too than then them they their there these those into over '
    'about would could should will just also been being only more most '
    'some such own same very per via'.split())

_FENCE_RE = re.compile(r'^\s*(```|~~~)')
_HEADING_RE = re.compile(r'^(#{1,4})\s+(.*?)\s*#*\s*$')

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(key: str) -> threading.Lock:
    with _locks_guard:
        lk = _locks.get(key)
        if lk is None:
            lk = _locks[key] = threading.Lock()
        return lk


# ── locations ────────────────────────────────────────────────────────────────

def index_dir() -> Path:
    """`~/.clayrune/doc_search/` — operator state, outside the repo and DATA_DIR."""
    from mc.secrets_store import clayrune_home
    return clayrune_home() / 'doc_search'


def _safe_id(project_id: str) -> str:
    return re.sub(r'[^A-Za-z0-9_.-]', '_', str(project_id or '')) or '_'


def db_path(project) -> Optional[Path]:
    pid = (project or {}).get('id')
    if not pid:
        return None
    return index_dir() / f'{_safe_id(pid)}.db'


def project_root(project) -> Optional[Path]:
    """The checkout whose docs/ should be indexed.

    `project_path`, unless it is itself a linked git worktree — then the main
    checkout it belongs to, because that is where the gitignored docs live.
    """
    pp = (project or {}).get('project_path') or ''
    if not pp:
        return None
    root = Path(pp)
    try:
        dotgit = root / '.git'
        if dotgit.is_file():
            first = dotgit.read_text(encoding='utf-8', errors='replace').strip()
            if first.startswith('gitdir:'):
                gitdir = Path(first[len('gitdir:'):].strip())
                if not gitdir.is_absolute():
                    gitdir = (root / gitdir)
                common = gitdir / 'commondir'
                if common.is_file():
                    cdir = (gitdir / common.read_text(encoding='utf-8').strip()).resolve()
                    if cdir.name == '.git' and cdir.parent.is_dir():
                        return cdir.parent
    except (OSError, ValueError) as e:
        _log(f'[doc_search] worktree resolve failed for {pp}: {e}')
    return root


# ── guards ───────────────────────────────────────────────────────────────────

def is_refused_name(path: str) -> bool:
    """True for a file the vault / file-serve allowlist refuses to serve."""
    from mc.blueprints.project_routes import _is_secret_file
    return bool(_is_secret_file(path))


def _is_incognito(project) -> bool:
    p = project or {}
    return bool(p.get('_is_incognito_project')) or p.get('id') == '_incognito'


# ── chunking ─────────────────────────────────────────────────────────────────

def chunk_markdown(text: str, title: str = '') -> list[tuple[str, str, int, int]]:
    """Split markdown into [(head, body, line_start, line_end)] (1-based lines).

    Splits on `#`..`####` headings outside code fences; a section longer than
    ~1500 chars is packed paragraph by paragraph. `head` is
    `title > H1 > H2 ...` so the heading path is searchable and citable.
    """
    lines = text.splitlines()
    sections: list[tuple[list[str], int, int, list[str]]] = []
    path: list[tuple[int, str]] = []
    cur_head: list[str] = []
    cur_start = 1
    cur_lines: list[str] = []
    in_fence = False

    def flush(end_line: int) -> None:
        body = '\n'.join(cur_lines).strip()
        if body:
            sections.append((list(cur_head), cur_start, end_line, list(cur_lines)))

    for i, line in enumerate(lines, start=1):
        if _FENCE_RE.match(line):
            in_fence = not in_fence
        m = None if in_fence else _HEADING_RE.match(line)
        if m:
            flush(i - 1)
            level, name = len(m.group(1)), m.group(2).strip()
            path = [(lv, nm) for lv, nm in path if lv < level] + [(level, name)]
            cur_head = [nm for _, nm in path]
            cur_start = i
            cur_lines = [line]
        else:
            cur_lines.append(line)
    flush(len(lines))

    out: list[tuple[str, str, int, int]] = []
    for heads, start, end, sec_lines in sections:
        head = ' > '.join(([title] if title else []) + heads)
        body = '\n'.join(sec_lines).strip()
        if len(body) <= _MAX_CHUNK_CHARS:
            out.append((head, body, start, end))
            continue
        # Pack paragraphs; track line numbers so a hit still cites a range.
        buf: list[str] = []
        buf_len = 0
        buf_start = start
        line_no = start
        for para_lines in _paragraphs(sec_lines):
            plen = sum(len(x) + 1 for x in para_lines)
            if buf and buf_len + plen > _MAX_CHUNK_CHARS:
                out.append((head, '\n'.join(buf).strip(), buf_start, line_no - 1))
                buf, buf_len, buf_start = [], 0, line_no
            buf.extend(para_lines)
            buf_len += plen
            line_no += len(para_lines)
        if buf and '\n'.join(buf).strip():
            out.append((head, '\n'.join(buf).strip(), buf_start, end))
    return out


def _paragraphs(sec_lines: list[str]):
    """Yield runs of lines, each ending after a blank line (blank kept with the
    paragraph so line counts stay exact). A single paragraph longer than
    _MAX_CHUNK_CHARS (a big table, a pasted log) stays one chunk."""
    run: list[str] = []
    for ln in sec_lines:
        run.append(ln)
        if not ln.strip():
            yield run
            run = []
    if run:
        yield run


# ── index ────────────────────────────────────────────────────────────────────

def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute('PRAGMA journal_mode=WAL')
    _ensure_schema(conn)
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS doc_fts USING fts5(
            head, body, file UNINDEXED, tier UNINDEXED,
            line_start UNINDEXED, line_end UNINDEXED,
            tokenize='porter unicode61'
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
    conn.execute('CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)')
    conn.commit()


def _meta_get(conn: sqlite3.Connection, k: str) -> Optional[str]:
    row = conn.execute('SELECT v FROM meta WHERE k = ?', (k,)).fetchone()
    return row[0] if row else None


def _meta_set(conn: sqlite3.Connection, k: str, v: str) -> None:
    conn.execute('INSERT INTO meta (k, v) VALUES (?, ?) '
                 'ON CONFLICT(k) DO UPDATE SET v = excluded.v', (k, v))


def _tier_for(rel: str) -> str:
    return 'journal' if JOURNAL_DIR in rel.split('/') else 'docs'


def _iter_doc_files(docs_root: Path):
    """Yield (relative_posix_path, absolute Path) for every indexable doc.

    Skips refused names, non-markdown, oversize files, and anything whose
    realpath leaves `docs_root` (symlink escape).
    """
    real_root = os.path.realpath(docs_root)

    def _inside(real: str) -> bool:
        return real == real_root or real.startswith(real_root + os.sep)

    for dirpath, dirnames, filenames in os.walk(docs_root):
        dirnames[:] = sorted(d for d in dirnames if d not in ('node_modules', '.git'))
        # realpath is the expensive call on Windows (~0.08 ms each, 566 files
        # = 45 ms per sweep). Resolve once per DIRECTORY (catches a junction
        # os.walk descended into) and per file only when it is a symlink.
        if not _inside(os.path.realpath(dirpath)):
            dirnames[:] = []
            continue
        for name in sorted(filenames):
            if os.path.splitext(name)[1].lower() not in DOC_EXTS:
                continue
            full = os.path.join(dirpath, name)
            if is_refused_name(full):
                continue
            if os.path.islink(full) and not _inside(os.path.realpath(full)):
                continue
            rel = os.path.relpath(full, docs_root).replace(os.sep, '/')
            yield rel, Path(full)


def _fingerprint(path: Path):
    try:
        st = path.stat()
        return st.st_mtime_ns, st.st_size
    except OSError:
        return None


def _index_file(conn: sqlite3.Connection, rel: str, path: Path, fp) -> int:
    key = f'{DOCS_SUBDIR}/{rel}'
    conn.execute('DELETE FROM doc_fts WHERE file = ?', (key,))
    rows = 0
    if fp[1] <= _MAX_FILE_BYTES:
        text = path.read_text(encoding='utf-8', errors='replace')
        title = path.stem
        tier = _tier_for(rel)
        for head, body, ls, le in chunk_markdown(text, title):
            conn.execute(
                'INSERT INTO doc_fts (head, body, file, tier, line_start, line_end) '
                'VALUES (?, ?, ?, ?, ?, ?)', (head, body, key, tier, ls, le))
            rows += 1
    from mc.core import now_iso
    conn.execute(
        'INSERT INTO indexed_files (path, mtime_ns, size, rows, indexed_at) '
        'VALUES (?, ?, ?, ?, ?) '
        'ON CONFLICT(path) DO UPDATE SET mtime_ns=excluded.mtime_ns, '
        'size=excluded.size, rows=excluded.rows, indexed_at=excluded.indexed_at',
        (key, fp[0], fp[1], rows, now_iso()))
    return rows


def build_index(project) -> dict:
    """Bring one project's doc index up to date. Never raises.

    Unchanged files (same mtime_ns + size) cost one stat. Changed files have
    their rows replaced; files that no longer exist have their rows removed.
    A changed project root wipes the index first (a moved project must not
    keep serving the old tree).
    """
    stats = {'files_seen': 0, 'files_indexed': 0, 'files_removed': 0,
             'rows_written': 0, 'elapsed_s': 0.0}
    t0 = _time.time()
    try:
        root = project_root(project)
        path = db_path(project)
        if root is None or path is None or _is_incognito(project):
            return stats
        docs_root = root / DOCS_SUBDIR
        if not docs_root.is_dir():
            return stats
        with _lock_for(str(path)):
            conn = _connect(path)
            try:
                if (_meta_get(conn, 'root') not in (None, str(root))
                        or _meta_get(conn, 'version') not in (None, _SCHEMA_VERSION)):
                    conn.execute('DELETE FROM doc_fts')
                    conn.execute('DELETE FROM indexed_files')
                _meta_set(conn, 'root', str(root))
                _meta_set(conn, 'version', _SCHEMA_VERSION)
                known = {r[0]: (r[1], r[2]) for r in conn.execute(
                    'SELECT path, mtime_ns, size FROM indexed_files')}
                seen: set[str] = set()
                for rel, fpath in _iter_doc_files(docs_root):
                    key = f'{DOCS_SUBDIR}/{rel}'
                    seen.add(key)
                    stats['files_seen'] += 1
                    fp = _fingerprint(fpath)
                    if fp is None or known.get(key) == fp:
                        continue
                    try:
                        stats['rows_written'] += _index_file(conn, rel, fpath, fp)
                        stats['files_indexed'] += 1
                    except Exception as e:
                        _log(f'[doc_search] index failed for {fpath}: {e}')
                for key in set(known) - seen:
                    conn.execute('DELETE FROM doc_fts WHERE file = ?', (key,))
                    conn.execute('DELETE FROM indexed_files WHERE path = ?', (key,))
                    stats['files_removed'] += 1
                conn.commit()
            finally:
                conn.close()
    except Exception as e:
        _log(f'[doc_search] build_index failed: {e}')
    stats['elapsed_s'] = round(_time.time() - t0, 3)
    return stats


# ── search ───────────────────────────────────────────────────────────────────

def _terms(query: str) -> list[str]:
    seen: list[str] = []
    for t in re.findall(r'[a-z0-9]+', (query or '').lower()):
        if len(t) >= 3 and t not in _STOPWORDS and t not in seen:
            seen.append(t)
    return seen


def search(project, query: str, limit: int = 5, *, refresh: bool = True) -> list[dict[str, Any]]:
    """Ranked search; best file first, one hit (its best chunk) per file.

    Each hit: {file, heading, line_start, line_end, tier, score, snippet,
    path}. `file` is project-relative (`docs/X.md`); `path` is the absolute
    path under the project root, for `[file:...]` links. `score` is positive
    and higher-is-better (negated bm25, x tier weight): it is NOT a relevance
    probability — a question with no answer in the corpus still returns
    scored hits, so the caller must read the snippet (the exploration
    measured that no score threshold separates "no answer" from "weak
    answer"). Returns [] on any failure or an incognito project; never raises.
    """
    try:
        limit = max(1, min(int(limit), 50))
    except (TypeError, ValueError):
        limit = 5
    terms = _terms(query)
    if not terms or _is_incognito(project):
        return []
    try:
        if refresh:
            build_index(project)
        path = db_path(project)
        root = project_root(project)
        if path is None or root is None or not path.is_file():
            return []
        match = ' OR '.join(f'"{t}"' for t in terms)
        conn = sqlite3.connect(str(path))
        try:
            rows = conn.execute(
                "SELECT file, head, tier, line_start, line_end, "
                "snippet(doc_fts, 1, '»', '«', ' … ', 24), "
                "bm25(doc_fts, ?, 1.0) AS score "
                "FROM doc_fts WHERE doc_fts MATCH ? "
                "ORDER BY score LIMIT ?",
                (_HEAD_WEIGHT, match, limit * 12 + 20)
            ).fetchall()
        finally:
            conn.close()
    except Exception as e:
        _log(f'[doc_search] search failed: {e}')
        return []
    best: dict[str, dict[str, Any]] = {}
    for file, head, tier, ls, le, snippet, bm in rows:
        score = -float(bm) * _TIER_WEIGHT.get(tier, 1.0)
        cur = best.get(file)
        if cur is None or score > cur['score']:
            best[file] = {
                'file': file,
                'path': str(root / file),
                'heading': head,
                'tier': tier,
                'line_start': int(ls),
                'line_end': int(le),
                'score': round(score, 4),
                'snippet': snippet,
            }
    return sorted(best.values(), key=lambda h: -h['score'])[:limit]
