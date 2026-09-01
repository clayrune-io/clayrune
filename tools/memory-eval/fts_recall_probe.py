#!/usr/bin/env python3
"""MC-918 — does the cold FTS5 session-search tier actually close a recall gap?

MEASURE, DO NOT ASSERT. For every task in the pinned suite (real dispatched
prompts, tools/memory-eval/pin_suite.py), replays:

  BEFORE: mc.memory._memory_search(project, task, topk=<live>)        — the
          curated corpus (topic files + archive + managed region) alone.
  AFTER:  BEFORE + mc.memory_fts.cold_search(project, task, ...)      — with
          the cold tier appended, excluding the task's own source session (a
          task's own transcript trivially contains its own text — scoring
          that as "recall" would be measuring nothing).

Reports the zero-result rate before/after, how many previously-dark queries
the cold tier lights up, and how many distinct real sessions become reachable
that the curated corpus never surfaces (the actual size of the gap Hermes's
session_search closes — see docs/research/HERMES_AGENT_COMPETITIVE_READ.md §6.7).

Read-only over the curated corpus. WRITES the cold-search SQLite index (a new
sidecar db beside MEMORY.md, additive, never touches MEMORY.md itself) unless
--no-build is passed.

    python tools/memory-eval/fts_recall_probe.py --project-path "<canonical mission_control checkout>"
"""
import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _harness  # noqa: E402

SUITE_DIR = _harness.repo_root() / 'data' / 'memory-eval'
SUITE = SUITE_DIR / 'suite-v1.jsonl'


def _load_suite():
    if not SUITE.exists():
        raise SystemExit(
            f'no pinned suite at {SUITE} — run pin_suite.py first '
            '(python tools/memory-eval/pin_suite.py)')
    rows = []
    with open(SUITE, encoding='utf-8') as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--project-path', default=None,
                    help='canonical project checkout to read memory + '
                         'transcripts from (default: this repo root — WRONG '
                         'when run from an isolated worktree, see _harness.wire)')
    ap.add_argument('--k', type=int, default=None,
                    help='curated-corpus topk (default: live read_floor_topk)')
    ap.add_argument('--cold-k', type=int, default=5)
    ap.add_argument('--limit', type=int, default=None,
                    help='cap on tasks evaluated (default: whole suite)')
    ap.add_argument('--no-build', action='store_true',
                    help='skip (re)building the cold index; measure against '
                         'whatever is already indexed')
    ap.add_argument('--sample', type=int, default=6,
                    help='how many before/after transitions to print')
    args = ap.parse_args()

    m, project = _harness.wire(project_path=args.project_path)
    import mc.memory_fts as fts

    if not args.no_build:
        t0 = time.time()
        stats = fts.build_index(project, data_dir=_harness.repo_root() / 'data' / 'projects')
        print(f'[index] {stats["files_indexed"]}/{stats["files_seen"]} file(s) '
              f'(re)indexed, {stats["rows_written"]} row(s) written, '
              f'agent_log={stats["agent_log_indexed"]}, {stats["elapsed_s"]}s '
              f'(build_index elapsed; wall {round(time.time() - t0, 3)}s)')

    live_k, live_expand = _harness.live_signature()
    k = args.k or live_k
    cold_k = args.cold_k

    rows = _load_suite()
    if args.limit:
        rows = rows[:args.limit]
    print(f'[suite] {len(rows)} task(s) from {SUITE}')

    n = len(rows)
    zero_before = zero_after = lit_up = tasks_with_cold_coverage = 0
    cold_hit_counts = []
    distinct_cold_sessions = set()
    total_cold_hits = 0
    transitions = []  # (task, before_n, after_n, sample cold hit)

    for row in rows:
        task = row['task']
        source_session = Path(row['source']).stem
        index_hits = m._memory_search(project, task, topk=k, expand=live_expand)
        cold_hits = fts.cold_search(project, task, limit=cold_k,
                                    exclude_session_ids={source_session})
        before_n = len(index_hits)
        after_n = before_n + len(cold_hits)
        if before_n == 0:
            zero_before += 1
            if after_n > 0:
                lit_up += 1
        if after_n == 0:
            zero_after += 1
        cold_hit_counts.append(len(cold_hits))
        total_cold_hits += len(cold_hits)
        if cold_hits:
            tasks_with_cold_coverage += 1
        for h in cold_hits:
            distinct_cold_sessions.add(h['session_id'])
        if before_n == 0 and after_n > 0 and len(transitions) < args.sample:
            transitions.append((task, before_n, after_n, cold_hits[0]))

    print()
    print('=== recall: curated corpus alone vs curated + cold FTS5 tier ===')
    print(f'tasks evaluated:                    {n}')
    print(f'zero-result rate, index only:       {zero_before}/{n} '
          f'({100.0 * zero_before / max(1, n):.1f}%)')
    print(f'zero-result rate, index + cold:      {zero_after}/{n} '
          f'({100.0 * zero_after / max(1, n):.1f}%)')
    print(f'previously-zero queries lit up by cold tier: {lit_up}/{max(1, zero_before)} '
          f'of the zero-result set '
          f'({100.0 * lit_up / max(1, zero_before):.1f}%)')
    print(f'total cold hits returned:            {total_cold_hits} '
          f'(avg {statistics.mean(cold_hit_counts):.2f}/task, '
          f'median {statistics.median(cold_hit_counts):.0f}/task)')
    print(f'distinct real sessions reached ONLY via cold tier: '
          f'{len(distinct_cold_sessions)}')
    print(f'tasks with >=1 cold hit (content dark before this feature): '
          f'{tasks_with_cold_coverage}/{n} '
          f'({100.0 * tasks_with_cold_coverage / max(1, n):.1f}%)')
    print()
    print(f'=== sample of {len(transitions)} zero -> nonzero transitions ===')
    for task, b, a, sample_hit in transitions:
        print(f'- task: {task[:100]!r}')
        print(f'  before={b} after={a} '
              f'sample cold hit: session={sample_hit["session_id"]} '
              f'ts={sample_hit["timestamp"]} role={sample_hit["role"]}')
        print(f'  snippet: {sample_hit["snippet"][:160]!r}')

    _harness.cleanup()


if __name__ == '__main__':
    main()
