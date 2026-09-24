"""Summarise the mid-task memory push log (MC-944, mc/memory_push.py).

Report mode's whole deliverable is the JSONL log
(`data/memory_push_log/<project_id>.jsonl`) — nothing else reads it or acts on
it yet. This script is how a human grades it: would-send volume, which notes
fire most, how much of that volume duplicates what per-turn delivery
(mc.memory_turn) already sent for real, and a sample of rows to eyeball.

Read-only. Never touches the log, the corpus, or config. Deliberately does not
import `server` or `mc.memory` — the log is plain JSONL on disk, and importing
`server` risks the tunnel-reaper side effect `_harness.py` documents (S0).
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _harness  # noqa: E402 — only used for repo_root(), never wire()

_rc = getattr(sys.stdout, 'reconfigure', None)
if _rc:
    try:
        _rc(encoding='utf-8', errors='replace')
    except Exception:
        pass


def log_path(project_id: str) -> Path:
    return _harness.repo_root() / 'data' / 'memory_push_log' / f'{project_id}.jsonl'


def read_rows(path: Path) -> list:
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding='utf-8', errors='replace').splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def summarize(rows: list) -> dict:
    fires = [r for r in rows if r.get('result') == 'would_send']
    misses = [r for r in rows if r.get('result') == 'near_miss']

    per_session = Counter(r.get('session_id', '') for r in fires)
    # MC-964 Step B / RC4: an archive fire's `note` is the shared filename —
    # group by (note, line) so distinct archive lines don't collapse into one
    # bucket ("MEMORY_ARCHIVE.md" was previously indistinguishable across
    # ~2.5k lines). `line` is '' for topic/position fires, so those group by
    # filename exactly as before.
    top_notes = Counter((r.get('note', ''), r.get('line', '')) for r in fires)
    duplicate_of_per_turn = sum(1 for r in fires if r.get('already_delivered_by_per_turn'))
    by_source = Counter(r.get('source', '') for r in fires)
    by_class = Counter(r.get('note_class', '') for r in fires)

    return {
        'total_rows': len(rows),
        'fires': len(fires),
        'near_misses': len(misses),
        'sessions_with_a_fire': len(per_session),
        'fires_per_session': per_session.most_common(),
        'top_notes': top_notes.most_common(10),
        'duplicate_of_per_turn_delivery': duplicate_of_per_turn,
        'fires_by_source': dict(by_source),
        'fires_by_class': dict(by_class),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--project', default='mission_control')
    ap.add_argument('--sample', type=int, default=10,
                     help='how many would_send rows to print verbatim for grading')
    ap.add_argument('--json', action='store_true', help='print the summary as JSON')
    args = ap.parse_args()

    path = log_path(args.project)
    rows = read_rows(path)
    if not rows:
        print(f'no log at {path} — nothing has fired yet '
              f'(memory_push_mode must be "report" and a session with a '
              f'matching tool call must have run)')
        return

    summary = summarize(rows)
    if args.json:
        print(json.dumps(summary, indent=2))
        return

    print(f'memory push review — {path}')
    print(f'  {summary["total_rows"]} row(s): {summary["fires"]} would_send, '
          f'{summary["near_misses"]} near_miss')
    print(f'  {summary["sessions_with_a_fire"]} session(s) had at least one fire')
    print(f'  {summary["duplicate_of_per_turn_delivery"]}/{summary["fires"]} fires '
          f'duplicate a note per-turn delivery already sent for real this session')
    print(f'  by source: {summary["fires_by_source"]}')
    print(f'  by class:  {summary["fires_by_class"]}')

    if summary['top_notes']:
        print('\ntop notes by fire count:')
        for (note, line), n in summary['top_notes']:
            label = f'{note}  ::  {line}' if line else note
            print(f'  {n:3d}  {label}')

    fires = [r for r in rows if r.get('result') == 'would_send']
    if fires and args.sample:
        print(f'\nsample of up to {args.sample} would_send row(s) to grade:')
        for r in fires[-args.sample:]:
            target = r.get('line') or r.get('note', '')
            print(f"  [{r.get('ts', '')}] {r.get('tool', '')} ({r.get('source', '')}) "
                  f"score={r.get('score')} -> {target}")
            print(f"      query: {r.get('query', '')[:160]!r}")

    print('\nRead-only. Nothing here was promoted, delivered, or edited.')


if __name__ == '__main__':
    main()
