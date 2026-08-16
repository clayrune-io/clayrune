#!/usr/bin/env python3
"""Sweep the S4 ranker constants — ACROSS PROJECTS, not just this one.

    python tools/memory-eval/sweep_constants.py

WHY ACROSS PROJECTS. `bm25_b`, `bm25_title_boost` and `read_floor_archive_quota`
live in the global config, so flipping one changes retrieval for every project on
the box. Every measurement behind them was taken on mission_control alone, which
has the largest and most link-dense corpus here. A constant that helps the biggest
corpus can easily hurt a small one — a title boost matters less when there are
eight notes, and an archive quota does nothing when there is no archive.

So a win here is a hypothesis about the other projects until this says otherwise.
Read-only: sets `state.CONFIG` in THIS process only, never PUTs anything.
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _harness  # noqa: E402

SUITE = _harness.repo_root() / 'data' / 'memory-eval' / 'suite-v1.jsonl'

VARIANTS = [
    ('baseline (today)', {}),
    ('bm25_b=1.0', {'bm25_b': 1.0}),
    ('title_boost=1', {'bm25_title_boost': 1}),
    ('archive_quota=2', {'read_floor_archive_quota': 2}),
]


def projects(m):
    """Every project with a memory dir, biggest corpus first."""
    out = []
    data_dir = _harness.repo_root() / 'data' / 'projects'
    for f in sorted(data_dir.glob('*.json')):
        if any(f.name.endswith(sfx) for sfx in (
                '_agent_log.json', '_scribe_stats.json', '_topics.json',
                '_topic_state.json', '_skill_stats.json', '_router_stats.json',
                '_skill_stats_summary.json')):
            continue
        try:
            p = json.loads(f.read_text(encoding='utf-8'))
        except Exception:
            continue
        # Some records in DATA_DIR are lists, not project dicts — the very
        # pollution CLAUDE.md warns about. Skip rather than crash.
        if not isinstance(p, dict) or not p.get('project_path'):
            continue
        try:
            mem = m._get_memory_path(p).parent
        except Exception:
            continue
        if not mem.is_dir():
            continue
        n = len(list(mem.glob('*.md')))
        if n >= 3:
            out.append((p, n))
    out.sort(key=lambda r: -r[1])
    return [p for p, _ in out]


def reach(m, project, tasks, topk, expand):
    topics = {t.lower() for t in _harness.topic_files(m, project)}
    seen = set()
    for t in tasks:
        for h in m._memory_search(project, t, topk, expand=expand):
            seen.add(h['file'].split('#')[0].lower())
    hit = seen & topics
    return len(hit), len(topics) - len(hit), len(topics)


def main():
    m, _ = _harness.wire()
    from mc import state
    topk, expand = _harness.live_signature()
    tasks = [json.loads(ln)['task'] for ln in
             SUITE.read_text(encoding='utf-8').splitlines() if ln.strip()]

    targets = projects(m)
    print(f'signature topk={topk} expand={expand} | suite {len(tasks)} tasks '
          f'| {len(targets)} project(s) with a memory dir\n')
    print('NOTE: the suite is mission_control\'s tasks. For other projects it is a '
          'proxy for "does this constant change retrieval shape", not a relevance '
          'measure — their own tasks are not pinned.\n')

    saved = {k: state.CONFIG.get(k) for k in
             ('bm25_b', 'bm25_title_boost', 'read_floor_archive_quota')}
    try:
        for project in targets:
            base = None
            print(f"{project['id']}:")
            for label, overrides in VARIANTS:
                for k in saved:
                    state.CONFIG.pop(k, None)
                state.CONFIG.update(overrides)
                r, dark, total = reach(m, project, tasks, topk, expand)
                if base is None:
                    base = r
                    print(f'    {label:<18} reachable {r:>3}/{total}  dark {dark:>3}')
                else:
                    d = r - base
                    flag = '' if d >= 0 else '   <-- REGRESSION'
                    print(f'    {label:<18} reachable {r:>3}/{total}  dark {dark:>3}  '
                          f'delta {d:+d}{flag}')
            print()
    finally:
        for k, v in saved.items():
            if v is None:
                state.CONFIG.pop(k, None)
            else:
                state.CONFIG[k] = v
    return 0


if __name__ == '__main__':
    sys.exit(main())
