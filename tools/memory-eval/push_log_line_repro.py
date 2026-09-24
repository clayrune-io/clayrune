"""Step B / RC4 acceptance: does the FIXED push log answer "did line X appear
in a turn" from the log alone, for the exact 09-18/19 window RC4 measured?

RC4 (`docs/MEMORY_OVERHAUL_PLAN.md` section 2): the OLD push log recorded
archive hits as `note: "MEMORY_ARCHIVE.md"` with no line id — 495 real
observations in the 09-18/19 window had codex/allowance in the query, 83 of
those were archive rows, and none of the 83 said WHICH archive line, so "was
the top-up fact ever near" was unanswerable without a corpus replay.

The 83 archive rows and their `query` text are real, already on disk in
`data/memory_push_log/mission_control.jsonl` — the OLD log has the right
QUERIES, just the wrong (anonymous) telemetry shape. This script:

  1. Pulls those exact historical queries back out of the old log (read-only).
  2. Rebuilds the memory corpus as it stood at the end of that window (reuses
     codex_miss_repro.py's as-of-cutoff snapshot: MEMORY_ARCHIVE.md cut at the
     first line dated after 2026-09-19).
  3. Replays each query through the FIXED `mc.memory_push.observe()` (this
     is the one-time "replay" needed to backfill what the OLD telemetry
     should have recorded — after this, no further replay is needed: the log
     alone answers the question, which is the point of Step B).
  4. Reads ONLY the resulting new-format log rows to answer "did the top-up
     line appear (as a would_send) in this window" — no second
     `_memory_search` call.

Writes its replay output to a TEMP log dir (monkeypatches
`memory_push._log_dir`), never touching the real
`data/memory_push_log/<project>.jsonl`. Read-only otherwise.

    python tools/memory-eval/push_log_line_repro.py --project-path <main checkout>
"""
import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _harness  # noqa: E402
import codex_miss_repro as _cmr  # noqa: E402 — reuse its as-of-cutoff snapshot

NEEDLE = 'after adding Codex credits'
WINDOW_PREFIXES = ('2026-09-18', '2026-09-19')


def real_window_queries(real_log: Path) -> list:
    """The exact historical (query, note_class) pairs RC4 measured, pulled
    from the OLD log on disk. Read-only."""
    out = []
    if not real_log.is_file():
        return out
    for line in real_log.read_text(encoding='utf-8', errors='replace').splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts = r.get('ts', '')
        q = (r.get('query') or '')
        if ts.startswith(WINDOW_PREFIXES) and ('codex' in q.lower() or 'allowance' in q.lower()):
            out.append(q)
    # De-duplicate, preserving first-seen order — the same query recurs across
    # many tool calls in a long real session.
    seen, uniq = set(), []
    for q in out:
        if q not in seen:
            seen.add(q)
            uniq.append(q)
    return uniq


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--project-path', required=True,
                     help='canonical project checkout (not a worktree)')
    a = ap.parse_args()

    m, project = _harness.wire(project_path=a.project_path)
    mem_dir = m._get_memory_path(project).parent
    # The real historical push log is per-installation data (gitignored) that
    # lives in the CANONICAL checkout `--project-path` names, not wherever
    # this script file happens to be running from (a worktree, per-agent) —
    # unlike `_harness.repo_root()`, which is derived from `__file__` and is
    # correct for the CODE but not for this one piece of operator data.
    real_log = Path(a.project_path) / 'data' / 'memory_push_log' / 'mission_control.jsonl'

    queries = real_window_queries(real_log)
    print(f'pulled {len(queries)} distinct real query(ies) from the 09-18/19 '
          f'window in the OLD log at {real_log}')
    if not queries:
        print('no historical queries found — nothing to replay')
        return

    snap0 = _cmr.snapshot(mem_dir)
    tmp_home = Path(tempfile.mkdtemp(prefix='mc964-stepb-home-'))
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
        m._memsearch_cache.clear()

    mount(tmp_home)

    # Replay output goes to a TEMP push-log dir — never the real one.
    from mc import memory_push as mp
    tmp_pushlog = Path(tempfile.mkdtemp(prefix='mc964-stepb-pushlog-'))
    mp._log_dir = lambda: tmp_pushlog

    session = {'session_id': 'stepb-replay', 'project_id': project['id']}
    for q in queries:
        mp.observe(project, session, 'replay', 'tool_result', q)

    replay_log = tmp_pushlog / f"{project['id']}.jsonl"
    rows = [json.loads(l) for l in
            replay_log.read_text(encoding='utf-8', errors='replace').splitlines()
            if l.strip()] if replay_log.is_file() else []
    print(f'replay wrote {len(rows)} row(s) to {replay_log} (temp, discarded after this run)')

    # ── the acceptance question, answered from the replayed log ROWS ONLY ──
    would_send_archive = [r for r in rows
                           if r.get('note_class') == 'archive' and r.get('result') == 'would_send']
    hit = next((r for r in would_send_archive if NEEDLE.lower() in r.get('line', '').lower()), None)

    all_archive = [r for r in rows if r.get('note_class') == 'archive']
    print(f"\n{len(all_archive)} total archive row(s) (would_send + near_miss) in the "
          f"replayed window; {len(would_send_archive)} were would_send")
    for r in sorted(all_archive, key=lambda r: -r.get('score', 0))[:10]:
        print(f"  {r['result']:>9} score={r['score']:.3f} line={r.get('line', '')!r}")
    if hit:
        print(f"ANSWER (from the log alone, no _memory_search call): YES — the top-up "
              f"line appeared as would_send.\n  line: {hit['line']!r}\n  query: {hit['query']!r}\n"
              f"  score={hit['score']} rank={hit['rank']} ts={hit['ts']}")
    else:
        near = [r for r in rows if r.get('note_class') == 'archive'
                and NEEDLE.lower() in r.get('line', '').lower()]
        if near:
            print("ANSWER (from the log alone): NO would_send, but the line WAS a ranked "
                  f"near_miss candidate {len(near)} time(s) — e.g. {near[0]['line']!r} "
                  f"(score={near[0]['score']}, threshold={near[0]['threshold']})")
        else:
            print("ANSWER (from the log alone): the top-up line never appeared in this "
                  "window's archive rows at all, would_send or near_miss.")

    _harness.assert_no_server()
    shutil.rmtree(snap0, ignore_errors=True)
    shutil.rmtree(tmp_home, ignore_errors=True)
    shutil.rmtree(tmp_pushlog, ignore_errors=True)


if __name__ == '__main__':
    main()
