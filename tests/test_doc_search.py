"""mc/doc_search.py — project document search (MC-950).

Unit tests drive the module against a tmp project; the acceptance test runs
the 15-question set from docs/RAG_DOCUMENTS_EXPLORATION.md Appendix A through
the real HTTP route, with the calling project pointing at THIS checkout
(a worktree when an agent runs it), so Q11-Q15 — whose gold docs are
gitignored and live only in the main checkout — prove the worktree blind spot
is closed. The acceptance test skips on a machine without those docs.
"""
import os
import re
from pathlib import Path

import pytest

from mc import doc_search as ds


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / 'clayrune_home'
    monkeypatch.setenv('CLAYRUNE_HOME', str(h))
    return h


def _proj(tmp_path, pid='dp', **extra):
    root = tmp_path / 'proj'
    (root / 'docs').mkdir(parents=True, exist_ok=True)
    return {'id': pid, 'project_path': str(root), **extra}


def _write(project, rel, text):
    p = Path(project['project_path']) / 'docs' / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding='utf-8')
    return p


def _rows(project):
    import sqlite3
    conn = sqlite3.connect(str(ds.db_path(project)))
    try:
        return conn.execute(
            'SELECT file, head, line_start, line_end FROM doc_fts ORDER BY file, line_start'
        ).fetchall()
    finally:
        conn.close()


# ── ingest ───────────────────────────────────────────────────────────────────

def test_ingest_indexes_headings_and_line_ranges(tmp_path, home):
    p = _proj(tmp_path)
    _write(p, 'DESIGN.md',
           '# Design\nintro line\n\n## Cache\nthe cache evicts stale entries\n\n'
           '## Queue\nthe queue drains nightly\n')
    st = ds.build_index(p)
    assert st['files_seen'] == 1 and st['files_indexed'] == 1
    hits = ds.search(p, 'queue drains nightly')
    assert hits[0]['file'] == 'docs/DESIGN.md'
    assert hits[0]['heading'] == 'DESIGN > Design > Queue'
    assert (hits[0]['line_start'], hits[0]['line_end']) == (7, 8)
    assert hits[0]['score'] > 0 and 'queue' in hits[0]['snippet'].lower()


def test_headings_inside_code_fences_do_not_split(tmp_path, home):
    p = _proj(tmp_path)
    _write(p, 'a.md', '# A\n```\n# not a heading\n```\nbody\n')
    ds.build_index(p)
    assert [r[1] for r in _rows(p)] == ['a > A']


def test_long_section_is_packed_into_bounded_chunks(tmp_path, home):
    p = _proj(tmp_path)
    paras = '\n\n'.join(f'paragraph {i} ' + 'word ' * 60 for i in range(20))
    _write(p, 'long.md', '# Long\n' + paras + '\n')
    ds.build_index(p)
    rows = _rows(p)
    assert len(rows) > 1
    assert all(r[2] <= r[3] for r in rows)


def test_index_lives_outside_data_dir_and_repo(tmp_path, home):
    p = _proj(tmp_path)
    _write(p, 'a.md', '# A\nhello world content\n')
    ds.build_index(p)
    path = ds.db_path(p)
    assert path is not None and path.is_file()
    assert path.parent == home / 'doc_search'
    assert not str(path).startswith(p['project_path'])


# ── update / delete ──────────────────────────────────────────────────────────

def test_untouched_sweep_writes_nothing(tmp_path, home):
    p = _proj(tmp_path)
    _write(p, 'a.md', '# A\nalpha content here\n')
    _write(p, 'b.md', '# B\nbeta content here\n')
    ds.build_index(p)
    st = ds.build_index(p)
    assert st['files_seen'] == 2
    assert st['files_indexed'] == 0 and st['rows_written'] == 0 and st['files_removed'] == 0


def test_edit_reindexes_only_the_edited_file(tmp_path, home):
    p = _proj(tmp_path)
    a = _write(p, 'a.md', '# A\nalpha content here\n')
    _write(p, 'b.md', '# B\nbeta content here\n')
    ds.build_index(p)
    a.write_text('# A\nzebra content here now\n', encoding='utf-8')
    os.utime(a, ns=(a.stat().st_atime_ns, a.stat().st_mtime_ns + 5_000_000_000))
    st = ds.build_index(p)
    assert st['files_indexed'] == 1
    assert ds.search(p, 'zebra', refresh=False)[0]['file'] == 'docs/a.md'
    assert ds.search(p, 'alpha', refresh=False) == []
    assert ds.search(p, 'beta', refresh=False)[0]['file'] == 'docs/b.md'


def test_deleted_file_rows_are_removed(tmp_path, home):
    p = _proj(tmp_path)
    a = _write(p, 'a.md', '# A\nalpha content here\n')
    _write(p, 'b.md', '# B\nbeta content here\n')
    ds.build_index(p)
    a.unlink()
    st = ds.build_index(p)
    assert st['files_removed'] == 1
    assert {r[0] for r in _rows(p)} == {'docs/b.md'}
    assert ds.search(p, 'alpha') == []


def test_search_refreshes_lazily(tmp_path, home):
    p = _proj(tmp_path)
    _write(p, 'a.md', '# A\nalpha content here\n')
    assert ds.search(p, 'alpha')[0]['file'] == 'docs/a.md'
    _write(p, 'new.md', '# New\nquokka appears later\n')
    assert ds.search(p, 'quokka')[0]['file'] == 'docs/new.md'


def test_moved_project_root_drops_the_old_tree(tmp_path, home):
    p = _proj(tmp_path)
    _write(p, 'a.md', '# A\nalpha content here\n')
    ds.build_index(p)
    other = tmp_path / 'other'
    (other / 'docs').mkdir(parents=True)
    (other / 'docs' / 'z.md').write_text('# Z\nzulu content here\n', encoding='utf-8')
    moved = {'id': p['id'], 'project_path': str(other)}
    ds.build_index(moved)
    assert {r[0] for r in _rows(moved)} == {'docs/z.md'}


# ── refused names / guards ───────────────────────────────────────────────────

@pytest.mark.parametrize('name', ['.env', '.env.local', 'server.key', 'cert.pem',
                                  'id_rsa', 'aws-credentials.md', 'my_secret_plan.md'])
def test_is_refused_name_matches_the_file_serve_denylist(name):
    assert ds.is_refused_name(str(Path('x') / name))


def test_refused_names_are_never_indexed_or_returned(tmp_path, home):
    p = _proj(tmp_path)
    _write(p, 'ok.md', '# Ok\nplatypus ordinary notes\n')
    _write(p, 'aws-credentials.md', '# Creds\nplatypus password hunter2\n')
    _write(p, 'my_secret_plan.md', '# Plan\nplatypus launch codes\n')
    _write(p, '.env', 'platypus=1\n')
    _write(p, 'server.key', 'platypus\n')
    _write(p, 'cert.pem', 'platypus\n')
    st = ds.build_index(p)
    assert st['files_seen'] == 1
    assert [h['file'] for h in ds.search(p, 'platypus')] == ['docs/ok.md']
    assert {r[0] for r in _rows(p)} == {'docs/ok.md'}


def test_symlink_escaping_docs_is_skipped(tmp_path, home):
    p = _proj(tmp_path)
    outside = tmp_path / 'outside.md'
    outside.write_text('# Out\nplatypus outside the tree\n', encoding='utf-8')
    link = Path(p['project_path']) / 'docs' / 'link.md'
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip('symlinks not available')
    _write(p, 'ok.md', '# Ok\nordinary notes\n')
    ds.build_index(p)
    assert {r[0] for r in _rows(p)} == {'docs/ok.md'}


def test_incognito_project_gets_nothing(tmp_path, home):
    p = _proj(tmp_path, _is_incognito_project=True)
    _write(p, 'a.md', '# A\nalpha content here\n')
    assert ds.build_index(p)['files_seen'] == 0
    assert ds.search(p, 'alpha') == []
    assert not ds.db_path(p).exists()


# ── tiers + worktree ─────────────────────────────────────────────────────────

def test_journal_hits_rank_below_docs_hits(tmp_path, home):
    p = _proj(tmp_path)
    text = '# Note\nthe frobnicator calibrates the flux capacitor\n'
    _write(p, '_journal/entry.md', text)
    _write(p, 'SPEC.md', text)
    hits = ds.search(p, 'frobnicator flux capacitor')
    assert [h['file'] for h in hits] == ['docs/SPEC.md', 'docs/_journal/entry.md']
    assert [h['tier'] for h in hits] == ['docs', 'journal']


def test_linked_worktree_indexes_the_main_checkouts_docs(tmp_path, home):
    """The point of MC-950: a worktree's own tree lacks gitignored docs; the
    index must come from the main checkout the worktree belongs to."""
    main = tmp_path / 'main'
    (main / '.git' / 'worktrees' / 'wt').mkdir(parents=True)
    (main / '.git' / 'worktrees' / 'wt' / 'commondir').write_text('../..\n')
    (main / 'docs').mkdir()
    (main / 'docs' / 'IGNORED_DESIGN.md').write_text(
        '# Ignored\nthe narwhal protocol lives only here\n', encoding='utf-8')
    wt = tmp_path / 'wt'
    (wt / 'docs').mkdir(parents=True)
    (wt / '.git').write_text(f'gitdir: {main / ".git" / "worktrees" / "wt"}\n')
    p = {'id': 'wtp', 'project_path': str(wt)}
    assert ds.project_root(p) == main.resolve() or ds.project_root(p) == main
    hits = ds.search(p, 'narwhal protocol')
    assert hits and hits[0]['file'] == 'docs/IGNORED_DESIGN.md'
    assert hits[0]['path'] == str(ds.project_root(p) / 'docs/IGNORED_DESIGN.md')


# ── route + acceptance ───────────────────────────────────────────────────────

def _client(tmp_data_dir, monkeypatch, project):
    # Import server FIRST: its first import runs the blueprints' wire(), which
    # would overwrite a load_project patched in before it.
    import server
    from mc.blueprints import guide_routes as gr
    monkeypatch.setattr(gr, 'load_project',
                        lambda pid: project if pid == project['id'] else None)
    return server.app.test_client()


def test_route_shape_errors_and_limit(tmp_data_dir, tmp_path, home, monkeypatch):
    p = _proj(tmp_path, pid='r')
    for i in range(4):
        _write(p, f'd{i}.md', f'# D{i}\nkiwi bird number {i}\n')
    c = _client(tmp_data_dir, monkeypatch, p)
    r = c.get('/api/project/r/docs/search?q=kiwi+bird&limit=2')
    assert r.status_code == 200
    body = r.get_json()
    assert len(body) == 2
    assert {'file', 'path', 'heading', 'line_start', 'line_end', 'score',
            'snippet', 'tier'} <= set(body[0])
    assert c.get('/api/project/r/docs/search').status_code == 400
    assert c.get('/api/project/nope/docs/search?q=x').status_code == 404
    assert c.get('/api/project/r/docs/search?q=kiwi&limit=abc').status_code == 200


_QUESTIONS = [
    ("Why was our Mac no-Python installer CI check giving a false picture?", r"unconditionally prepends"),
    ("How can an agent's git commits end up on master while its own branch looks untouched?", r"resets the working directory to the worktree"),
    ("What does the Cloudflare Worker cost per month, and is tunnel bandwidth metered?", r"\$5/mo flat"),
    ("Why can't the Origin header tell us whether a request came from a human or an agent?", r"not a proof of anything"),
    ("How long does the first page load take and how many requests does it make?", r"420 HTTP requests"),
    ("Which route did the Higgsfield spike recommend for Desk Studio?", r"Recommend route A"),
    ("Why did we pick a SQLite state migration instead of only adding a full-text index?", r"over an FTS-only"),
    ("Why did our test suite stay green when a renamed tab threw an error on every click?", r"nobody clicked the button"),
    ("How many memory topic files were really never retrieved once link expansion was counted?", r"15 topic files, not 30"),
    ("What incident motivated the no-downgrade guidance on browser read errors?", r"confusing error \(HTTP 415\)"),
    ("Is semantic search over memory built, and what holds it back?", r"Step 7 . bge-m3 retrieval.{0,100}deferred"),
    ("Are we scheduling sprints to split up server.py?", r"No standalone .extraction sprints"),
    ("What problem was the auto-model router designed to fix?", r"burned by trivial requests being routed to Opus"),
    ("Why does the live chat show only a tiny tail of the conversation while the transcript on disk is intact?", r"Key fact:\*\* no data is lost"),
    ("Where is the clayrune.io installer site hosted?", r"host on \*\*Cloudflare Pages\*\*"),
]


def _main_checkout(repo_root: Path):
    # Not `git rev-parse`: other tests leave subprocess patched, which made
    # this return None (and the acceptance test skip) depending on test order.
    return ds.project_root({'project_path': str(repo_root)})


def test_appendix_a_question_set_hit_at_3(tmp_data_dir, home, monkeypatch, repo_root):
    """Acceptance (MC-950): hit@3 >= 13/15 through the HTTP route, and Q11-Q15
    (gold docs gitignored, absent from a worktree) all answerable when the
    calling project is this checkout. Gold = every main-checkout docs/**/*.md
    matching the Appendix A answer regex (strict file-level hit)."""
    main = _main_checkout(repo_root)
    if main is None or not (main / 'docs' / 'MEMORY_SYSTEM.md').is_file():
        pytest.skip(f'main checkout with its gitignored docs not present on this machine '
                    f'(main={main})')
    corpus = {f'docs/{rel}': path.read_text(encoding='utf-8', errors='replace')
              for rel, path in ds._iter_doc_files(main / 'docs')}
    project = {'id': 'acc', 'project_path': str(repo_root)}
    c = _client(tmp_data_dir, monkeypatch, project)

    results = []
    for i, (question, rx) in enumerate(_QUESTIONS, start=1):
        gold = {f for f, t in corpus.items() if re.search(rx, t, re.S)}
        assert gold, f'Q{i:02d}: answer regex matches no doc in the corpus'
        r = c.get('/api/project/acc/docs/search', query_string={'q': question, 'limit': 5})
        assert r.status_code == 200
        top3 = [h['file'] for h in r.get_json()[:3]]
        results.append((i, any(f in gold for f in top3), top3))
    hits = sum(ok for _, ok, _ in results)
    misses = [(i, top3) for i, ok, top3 in results if not ok]
    assert hits >= 13, f'hit@3 {hits}/15, misses: {misses}'
    worktree_only = [i for i, ok, _ in results if i >= 11 and not ok]
    assert not worktree_only, f'gitignored-doc questions not answered: {worktree_only}'
