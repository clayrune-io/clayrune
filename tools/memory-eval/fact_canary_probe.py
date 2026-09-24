"""MC-971 — memory effectiveness + durability monitor (Step 0 of MC-964).

Answers the question the weekly structural health check (eval.py +
delivery_review.py) cannot: "does THIS specific fact reach the agent when
asked the way Ron asks", not "is the corpus shaped correctly". That gap is
exactly how the Codex top-up miss (MC-964) went uncaught — the fact was
CITED nowhere the structural checks would flag, and BM25-reachable is not the
same as "reachable for Ron's actual wording".

FIVE THINGS THIS MEASURES

  1. EFFECTIVENESS - of the pinned fact canaries (data/memory-eval/
     fact_canaries.jsonl), what fraction of (canary, query) pairs deliver
     their unit within the live per-turn delivery depth (max(topk*2,
     topk+4), the same count agent_routes.py actually asks for).
  2. DURABILITY - the same, bucketed by fact age (<7d, 7-30d, 30-90d, >90d),
     plus a week-over-week rank trend per canary. A canary that drops out of
     the top-12 is flagged ONCE (raise-once, mirroring delivery_review.py) -
     re-arms only when the finding's content changes.
  3. CONTRADICTION PAIRS (data/memory-eval/contradiction_pairs.jsonl) - a
     stale fact vs. its correction; passes only when the corrected unit
     outranks the stale one for the shared query.
  4. REAL-WORLD MISS RATE - scans the last 7 days of real user messages for
     "I told you" / "we already" / "you forgot" / "as I said" and reports
     hits per 100 tasks. Each hit is a CANDIDATE canary, printed for human
     approval, never auto-added to the pinned file.
  5. A 5-line trend summary suitable for the weekly health-check schedule.

Read-only against the vault; writes only its own state sidecar
(data/memory-eval/fact_canary_state.json, gitignored) to track week-over-week
rank and raised-once flags. Same `_harness` rules as every other probe here:
NEVER import `server` (see _harness.py / this dir's README) - the tunnel
reaper lives behind that import and has already killed the operator's tunnel
once.

    python tools/memory-eval/fact_canary_probe.py
    python tools/memory-eval/fact_canary_probe.py --project-path <canonical checkout>
    python tools/memory-eval/fact_canary_probe.py --dry-run   # don't persist state
    python tools/memory-eval/fact_canary_probe.py --all       # reprint already-flagged drops too
"""
import argparse
import glob
import hashlib
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _harness  # noqa: E402

_rc = getattr(sys.stdout, 'reconfigure', None)
if _rc:
    try:
        _rc(encoding='utf-8', errors='replace')
    except Exception:
        pass

DATA_DIR = _harness.repo_root() / 'data' / 'memory-eval'
CANARIES_FILE = DATA_DIR / 'fact_canaries.jsonl'
PAIRS_FILE = DATA_DIR / 'contradiction_pairs.jsonl'
STATE_FILE = DATA_DIR / 'fact_canary_state.json'

# Real user messages, scanned for a miss signal.
MISS_PHRASES = ['i told you', 'we already', 'you forgot', 'as i said']
MISS_WINDOW_DAYS = 7

SEARCH_DEPTH = 40  # how far we look, so we can still see a near-miss rank


def _now():
    return datetime.now(timezone.utc)


def load_jsonl(path):
    if not path.exists():
        raise SystemExit(f'missing {path} — see this file\'s docstring')
    out = []
    for ln in path.read_text(encoding='utf-8').splitlines():
        ln = ln.strip()
        if ln:
            out.append(json.loads(ln))
    return out


def deliver_depth(topk):
    return max(topk * 2, topk + 4)


def age_days(date_recorded):
    d = datetime.strptime(date_recorded, '%Y-%m-%d').replace(tzinfo=timezone.utc)
    return max(0, (_now() - d).days)


def age_bucket(days):
    if days < 7:
        return '<7d'
    if days < 30:
        return '7-30d'
    if days < 90:
        return '30-90d'
    return '>90d'


def find_rank(m, hits, match_type, match_value):
    """1-based rank of the first hit matching (match_type, match_value), or None."""
    if match_type == 'file':
        target = m._mem_link_key(match_value.rsplit('.', 1)[0])
        for i, h in enumerate(hits, 1):
            f = str(h.get('file') or '')
            if m._mem_link_key(f.rsplit('.', 1)[0]) == target:
                return i
        return None
    if match_type == 'needle':
        needle = match_value.lower()
        for i, h in enumerate(hits, 1):
            blob = (str(h.get('snippet', '')) + ' ' + str(h.get('text', ''))).lower()
            if needle in blob:
                return i
        return None
    raise ValueError(f'unknown match_type {match_type!r}')


def run_canaries(m, project, canaries, topk, expand):
    depth = deliver_depth(topk)
    results = []
    for c in canaries:
        q_results = []
        for q in c['queries']:
            hits = m._memory_search(project, q, SEARCH_DEPTH, expand=expand, record=None)
            rank = find_rank(m, hits, c['match_type'], c['match_value'])
            q_results.append({'query': q, 'rank': rank,
                              'delivered': rank is not None and rank <= depth})
        results.append({
            'id': c['id'], 'fact': c['fact'], 'date_recorded': c['date_recorded'],
            'age_days': age_days(c['date_recorded']),
            'bucket': age_bucket(age_days(c['date_recorded'])),
            'queries': q_results,
            'best_rank': min([r['rank'] for r in q_results if r['rank'] is not None], default=None),
            'any_delivered': any(r['delivered'] for r in q_results),
            'all_delivered': all(r['delivered'] for r in q_results),
        })
    return results, depth


def run_contradictions(m, project, pairs, topk, expand):
    depth = deliver_depth(topk)
    out = []
    for p in pairs:
        hits = m._memory_search(project, p['query'], SEARCH_DEPTH, expand=expand, record=None)
        stale_rank = find_rank(m, hits, p['stale']['match_type'], p['stale']['match_value'])
        corr_rank = find_rank(m, hits, p['corrected']['match_type'], p['corrected']['match_value'])
        # The correction must actually be DELIVERED (rank <= depth), not just
        # outrank the stale record somewhere in the search list. First
        # baseline (2026-09-23) "passed" with the correction at rank 14 of a
        # 12-slot delivery, i.e. the agent never saw it.
        passed = (corr_rank is not None and corr_rank <= depth and
                  (stale_rank is None or corr_rank < stale_rank))
        out.append({'id': p['id'], 'subject': p['subject'], 'query': p['query'],
                    'stale_rank': stale_rank, 'corrected_rank': corr_rank,
                    'passed': passed, 'depth': depth})
    return out


_HEADER_RE = re.compile(r'^---.*---\s*$')


def _strip_injected_context(text):
    """Best-effort removal of harness-injected boilerplate before phrase-matching.

    Every real turn in this codebase carries prepended context blocks
    (`--- STANDING POSITIONS ---`, `--- RELEVANT MEMORY ---`, `<system-
    reminder>...</system-reminder>`, the REPLY SHAPE tail, etc. -- see this
    project's own system prompt for a live example). Those blocks routinely
    contain the literal words "already", "told", "forgot" as part of their
    OWN prose (a standing position literally saying "because we already ARE
    that vault shape" false-positived as a miss on first cut of this probe),
    so an unfiltered scan measures the boilerplate, not Ron. Strip
    `<system-reminder>` bodies and any `--- HEADER ---` block's indented/
    bulleted lines; what's left is heavily biased toward what a human typed.
    Not perfect (residual tails like the REPLY SHAPE block have no header
    line to strip on), but it eliminates the false-positive CLASS that
    produced 25 hits, all boilerplate, on the first run.
    """
    text = re.sub(r'<system-reminder>.*?</system-reminder>', ' ', text, flags=re.S)
    out = []
    skipping = False
    for ln in text.split('\n'):
        s = ln.strip()
        if _HEADER_RE.match(s):
            skipping = True
            continue
        if skipping:
            if s == '' or s.startswith(('•', '-', '*')) or ln.startswith('  '):
                continue
            skipping = False
        out.append(ln)
    return '\n'.join(out)


# Non-chat tool prompts (Scribe checkpoint calls, Distiller extraction calls)
# are logged with role="user" too -- they are a model call, not something Ron
# typed. Excluded wholesale rather than phrase-filtered.
_TOOL_PROMPT_MARKERS = (
    'you are a project-memory scribe',
    'you are the phase 4 distiller extraction model',
    'this is one chunk of a longer agent session transcript',
)


def scan_misses(days=MISS_WINDOW_DAYS):
    """Real user messages in the last N days containing a miss phrase.

    Mirrors delivery_backfill.collect_tasks's session discovery (glob every
    session dir with 'mission-control' in the name), but scans EVERY user
    message in-window, not just the first, since a miss can land mid-chat.
    """
    root = os.path.join(os.path.expanduser('~'), '.claude', 'projects')
    if not os.path.isdir(root):
        return 0, []
    cutoff = _now() - timedelta(days=days)
    dirs = [x for x in os.listdir(root)
            if 'mission-control' in x and 'scratch' not in x and 'demo' not in x]
    files = []
    for x in dirs:
        files += glob.glob(os.path.join(root, x, '*.jsonl'))

    tasks_in_window = 0
    hits = []
    seen_tasks = set()
    for path in files:
        try:
            fh = open(path, 'r', encoding='utf-8', errors='replace')
        except Exception:
            continue
        with fh:
            for line in fh:
                if '"user"' not in line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                msg = rec.get('message') or {}
                if msg.get('role') != 'user':
                    continue
                ts = rec.get('timestamp')
                if not ts:
                    continue
                try:
                    when = datetime.fromisoformat(ts.replace('Z', '+00:00'))
                except Exception:
                    continue
                if when < cutoff:
                    continue
                c = msg.get('content')
                if isinstance(c, str):
                    text = c
                elif isinstance(c, list):
                    text = ' '.join(b.get('text', '') for b in c
                                    if isinstance(b, dict) and b.get('type') == 'text')
                else:
                    continue
                text = text.strip()
                if not text or text.startswith('<'):
                    continue
                low_full = text.lower()
                if any(marker in low_full for marker in _TOOL_PROMPT_MARKERS):
                    continue  # a Scribe/Distiller model call, not a Ron message
                key = (path, rec.get('uuid'))
                if key not in seen_tasks:
                    seen_tasks.add(key)
                    if len(text) >= 25:
                        tasks_in_window += 1
                real_text = _strip_injected_context(text)
                low = real_text.lower()
                for phrase in MISS_PHRASES:
                    idx = low.find(phrase)
                    if idx != -1:
                        ctx = real_text[max(0, idx - 80):idx + 160].strip()
                        hits.append({'session': os.path.basename(path), 'ts': ts,
                                    'phrase': phrase, 'text': ctx})
                        break
    return tasks_in_window, hits


def read_state():
    try:
        if STATE_FILE.is_file():
            return json.loads(STATE_FILE.read_text(encoding='utf-8'))
    except Exception:
        pass
    return {}


def write_state(state):
    from mc.core import _atomic_write_text
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(STATE_FILE, json.dumps(state, indent=2, sort_keys=True) + '\n')


def drop_finding_hash(canary_id):
    return hashlib.sha256(f'drop|{canary_id}'.encode('utf-8')).hexdigest()[:16]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--project-path', default=None,
                    help='canonical project checkout (default: this repo\'s own root; '
                         'pass this when running from a worktree)')
    ap.add_argument('--dry-run', action='store_true', help='do not persist state')
    ap.add_argument('--all', action='store_true',
                    help='reprint drop-flags already raised')
    ap.add_argument('--miss-days', type=int, default=MISS_WINDOW_DAYS)
    args = ap.parse_args()

    m, project = _harness.wire(project_path=args.project_path)
    topk, expand = _harness.live_signature()
    depth = deliver_depth(topk)
    print(f'live signature: topk={topk} expand={expand}; per-turn delivery depth={depth}')

    canaries = load_jsonl(CANARIES_FILE)
    pairs = load_jsonl(PAIRS_FILE) if PAIRS_FILE.exists() else []
    results, _ = run_canaries(m, project, canaries, topk, expand)
    contradictions = run_contradictions(m, project, pairs, topk, expand) if pairs else []

    # ---- 1. EFFECTIVENESS ----
    all_pairs = [(r, q) for r in results for q in r['queries']]
    n_delivered = sum(1 for _, q in all_pairs if q['delivered'])
    effectiveness = 100.0 * n_delivered / max(1, len(all_pairs))
    canaries_any = sum(1 for r in results if r['any_delivered'])

    print(f'\n== EFFECTIVENESS ==')
    print(f'{n_delivered}/{len(all_pairs)} (canary, query) pairs delivered in top {depth}'
          f' ({effectiveness:.1f}%)')
    print(f'{canaries_any}/{len(results)} canaries reached by at least one query')

    # ---- 2. DURABILITY per age bucket ----
    print(f'\n== DURABILITY (by fact age) ==')
    buckets = defaultdict(list)
    for r in results:
        buckets[r['bucket']].append(r)
    for b in ['<7d', '7-30d', '30-90d', '>90d']:
        rs = buckets.get(b, [])
        if not rs:
            print(f'  {b:>6}: no canaries in this bucket')
            continue
        pairs_b = [(r, q) for r in rs for q in r['queries']]
        deliv_b = sum(1 for _, q in pairs_b if q['delivered'])
        print(f'  {b:>6}: {deliv_b}/{len(pairs_b)} query-deliveries '
              f'({100.0 * deliv_b / len(pairs_b):.0f}%), {len(rs)} canaries')

    # ---- week-over-week rank trend + raise-once drop flags ----
    state = read_state()
    prev_ranks = state.get('ranks') or {}
    flagged = set(state.get('flagged_drops') or [])
    new_ranks = {}
    fresh_drops = []
    for r in results:
        new_ranks[r['id']] = r['best_rank']
        prev = prev_ranks.get(r['id'])
        was_in = prev is not None and prev <= depth
        now_in = r['best_rank'] is not None and r['best_rank'] <= depth
        h = drop_finding_hash(r['id'])
        if was_in and not now_in:
            if args.all or h not in flagged:
                fresh_drops.append((r, prev))
            flagged.add(h)
        elif now_in and h in flagged:
            flagged.discard(h)  # re-armed: it recovered

    if fresh_drops:
        print(f'\n== RANK DROPS (canary left the top-{depth} since last run) ==')
        for r, prev in fresh_drops:
            print(f'  [{r["id"]}] was rank {prev}, now {r["best_rank"]} '
                  f'({r["fact"][:80]})')
    else:
        print(f'\nno new rank drops since the last run')

    # ---- 3. CONTRADICTION PAIRS ----
    print(f'\n== CONTRADICTION PAIRS ==')
    if not contradictions:
        print('  none pinned yet')
    else:
        n_pass = sum(1 for c in contradictions if c['passed'])
        print(f'{n_pass}/{len(contradictions)} pass '
              f'({100.0 * n_pass / len(contradictions):.0f}%)')
        for c in contradictions:
            status = 'PASS' if c['passed'] else 'FAIL'
            print(f'  [{status}] {c["subject"]}: stale rank={c["stale_rank"]}, '
                  f'corrected rank={c["corrected_rank"]} (query: {c["query"]!r})')

    # ---- 4. REAL-WORLD MISS RATE ----
    tasks_in_window, misses = scan_misses(args.miss_days)
    miss_rate = 100.0 * len(misses) / max(1, tasks_in_window)
    print(f'\n== REAL-WORLD MISS RATE (last {args.miss_days}d) ==')
    print(f'{len(misses)} miss-phrase hit(s) over {tasks_in_window} real tasks '
          f'({miss_rate:.1f} per 100 tasks)')
    if misses:
        print('  candidate canaries (human approval required, none auto-added):')
        for hit in misses[:15]:
            print(f'    [{hit["phrase"]}] {hit["ts"]} {hit["session"]}: '
                  f'{hit["text"][:140]!r}')

    # ---- 5. Trend summary (for the weekly schedule) ----
    print(f'\n== TREND SUMMARY ==')
    print(f'effectiveness: {effectiveness:.1f}%  |  '
          f'durability: ' + ', '.join(
              f'{b}={100.0 * sum(1 for _, q in [(r, q) for r in buckets.get(b, []) for q in r["queries"]] if q["delivered"]) / max(1, len([(r, q) for r in buckets.get(b, []) for q in r["queries"]])):.0f}%'
              for b in ['<7d', '7-30d', '30-90d', '>90d'] if buckets.get(b)))
    print(f'contradiction pass rate: '
          f'{100.0 * sum(1 for c in contradictions if c["passed"]) / max(1, len(contradictions)):.0f}%'
          if contradictions else 'contradiction pass rate: n/a (no pairs pinned)')
    print(f'miss rate: {miss_rate:.1f}/100 tasks  |  new rank-drop flags: {len(fresh_drops)}')

    if not args.dry_run:
        state['ranks'] = new_ranks
        state['flagged_drops'] = sorted(flagged)
        state['last_run'] = _now().isoformat()
        write_state(state)

    print('\nReports only. Nothing was promoted, demoted, or added to the pinned canary file.')
    _harness.assert_no_server()
    _harness.cleanup()


if __name__ == '__main__':
    main()
