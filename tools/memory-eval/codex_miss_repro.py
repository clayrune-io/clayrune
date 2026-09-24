"""Replay the 2026-09-19 Codex-top-up miss (MC-964) against the corpus as it
stood when Ron sent "We have plenty of codex token allowance, I added some
more" (2026-09-20T00:20:16Z, session 4ae1b44f).

Read-only. Builds a point-in-time snapshot of the memory dir in a temp dir:
  * topic files CREATED after the cutoff are dropped (st_birthtime on Windows,
    so a later edit to an older file does not drop it; its later CONTENT is
    kept, which biases toward reachability, never against it);
  * MEMORY_ARCHIVE.md is cut at the first line dated after the cutoff day.
Then it runs the SAME `_memory_search` the per-turn refresh calls, at the live
signature, and prints where the recorded top-up fact (archive line containing
"after adding Codex credits") lands for each query.

    python tools/memory-eval/codex_miss_repro.py --project-path <main checkout>
"""
import argparse
import datetime as dt
import re
import shutil
import sys
import tempfile
from pathlib import Path

# Archive content routinely carries non-cp1252 characters (—, ≤, …) and
# Windows' default console codepage is cp1252, not UTF-8 — printing a
# snippet that happens to contain one crashes with UnicodeEncodeError deep
# into a run (measured 2026-09-24, MC-964 Step A). Reconfigure instead of
# requiring callers to remember `PYTHONIOENCODING=utf-8`.
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _harness  # noqa: E402

CUTOFF = dt.datetime(2026, 9, 20, 0, 20, 16, tzinfo=dt.timezone.utc)
NEEDLE = 'after adding Codex credits'

QUERIES = {
    # Ron's own words, verbatim (the per-turn query is the raw message text).
    'ron_0020Z': "We have plenty of codex token allowance, I added some more",
    'ron_0021Z': ("I'm bit worried since  I told you earlier already about the codex "
                  "allowance. The fact that you did not remember it is worrying"),
    # The turn where Dave first acted on the stale record (a test failure, not a
    # human message; shown for completeness — a tool result never reaches the ranker).
    'dave_plan_query': "codex is out of allowance (usage_limit), resets Sep 24th",
    # An oracle query: the words a retriever WOULD need to match the stored fact.
    'oracle': "Codex credits added access problem cleared",
}


def birth(p: Path) -> dt.datetime:
    st = p.stat()
    t = getattr(st, 'st_birthtime', None) or st.st_ctime
    return dt.datetime.fromtimestamp(t, tz=dt.timezone.utc)


def snapshot(mem_dir: Path) -> Path:
    tmp = Path(tempfile.mkdtemp(prefix='mc964-snap-'))
    dropped = []
    for f in mem_dir.glob('*.md'):
        if f.name == 'MEMORY_ARCHIVE.md':
            keep = []
            for line in f.read_text(encoding='utf-8', errors='replace').splitlines():
                m = re.match(r'- \[(\d{4}-\d{2}-\d{2})\]', line)
                if m and m.group(1) > '2026-09-19':
                    break
                keep.append(line)
            (tmp / f.name).write_text('\n'.join(keep) + '\n', encoding='utf-8')
            continue
        # Machine-rewritten files are recreated in place, so their birth time
        # says nothing about when their content appeared; without MEMORY.md the
        # resolver sees no corpus at all and every query returns [].
        if f.name not in ('MEMORY.md', 'SESSION_LOG.md', 'continuity.md') and birth(f) > CUTOFF:
            dropped.append(f.name)
            continue
        shutil.copy2(f, tmp / f.name)
    print(f'snapshot: {len(list(tmp.glob("*.md")))} files, dropped (born after cutoff): {sorted(dropped)}')
    return tmp


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--project-path', required=True,
                    help='canonical project checkout (not a worktree)')
    ap.add_argument('--current', action='store_true',
                    help='also run against the live corpus as it is today')
    ap.add_argument('--no-dedupe', action='store_true',
                    help='counterfactual arm: bypass _dedupe_archive_lines')
    ap.add_argument('--depth', type=int, default=40)
    a = ap.parse_args()

    m, project = _harness.wire(project_path=a.project_path)
    mem_dir = m._get_memory_path(project).parent
    topk, expand = _harness.live_signature()
    print(f'live signature: topk={topk} expand={expand}; per-turn asks for {max(topk*2, topk+4)}')

    snap0 = snapshot(mem_dir)
    # Mount the snapshot under the CANONICAL project's encoded slot. The
    # harness's corpus_snapshot mounts it under repo_root()'s encoding, which
    # from a worktree is not the project_path it then searches — every query
    # silently returns [] (measured: 0 of 0 on all four queries).
    tmp_home = Path(tempfile.mkdtemp(prefix='mc964-home-'))
    dest = tmp_home / m._encode_project_path(a.project_path) / 'memory'
    shutil.copytree(snap0, dest)

    def mount(claude_home):
        noop = lambda *x, **k: None  # noqa: E731
        root = _harness.repo_root()
        m.wire(data_dir=root / 'data' / 'projects', memory_dir=root / 'data' / 'memory',
               claude_home=claude_home, session_size_limit=10 * 1024 * 1024,
               popen_flags=0, startupinfo=None, load_project_fn=noop,
               get_manager_fn=noop, resolve_claude_fn=noop, register_process_fn=noop,
               read_agent_stream_fn=noop, hide_windows_delayed_fn=noop)
        m._memsearch_cache.clear()   # per-file corpus cache would reuse the other arm

    live_home = Path.home() / '.claude' / 'projects'
    arms = [('as-of-cutoff', tmp_home, False)]
    if a.no_dedupe:
        arms.append(('as-of-cutoff, archive dedupe BYPASSED', tmp_home, True))
    if a.current:
        arms.append(('current', live_home, False))
    real_dedupe = m._dedupe_archive_lines
    for label, home, bypass in arms:
        m._dedupe_archive_lines = (lambda lines: list(lines)) if bypass else real_dedupe
        mount(home)
        print(f'\n===== {label} =====')
        for qn, q in QUERIES.items():
            hits = m._memory_search(project, q, a.depth, expand=expand, record=None)
            pos = next((i for i, h in enumerate(hits, 1)
                        if NEEDLE.lower() in str(h.get('snippet', '')).lower()
                        or NEEDLE.lower() in str(h.get('text', '')).lower()), None)
            print(f'\n[{qn}] {q!r}\n  top-up fact rank: {pos} of {len(hits)} returned')
            for i, h in enumerate(hits[:max(topk * 2, topk + 4)], 1):
                snip = re.sub(r'\s+', ' ', str(h.get('snippet', '')))[:110]
                print(f'  {i:>2}. score={h.get("score", 0):.3f} {h.get("file")} | {snip}')
    m._dedupe_archive_lines = real_dedupe
    _harness.assert_no_server()
    shutil.rmtree(snap0, ignore_errors=True)
    shutil.rmtree(tmp_home, ignore_errors=True)


if __name__ == '__main__':
    main()
