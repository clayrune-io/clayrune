"""The recurring delivery check — phase 4's output, as a report rather than a UI.

DAVE_DESIGN §9 phase 4. The backfill established the baseline and the analysis
it enabled produced exactly two promotions and one real bug. A screen and an
automatic mover would both be scaffolding around that volume. What is worth
keeping is the *watch*: the position that was reaching 57% of tasks had been
wrong since the day positions shipped, and nothing in the code, the tests or the
UI could have shown it. Only a count against real tasks did.

So this runs weekly beside the memory health check, and it reports. It never
promotes, demotes, edits a note or touches the index — same rule as
`positions_review`, and for the same reason: an unattended process that rewrites
what every prompt says is the authority guard wearing a different hat.

FOUR THINGS IT WATCHES

  1. **Promotion gap** — a note the floor keeps fetching with no line in the
     resident index. Every one of those retrievals spends a read-floor slot
     re-discovering something a one-liner would have flagged.
  2. **A position that has become prompt furniture** — firing far above the
     rarity gate's intent. This is the one that already bit us.
  3. **Notes that have gone dark** — referenced by the index, never retrieved.
     Read this one carefully: for a note WITH an index line the one-liner is
     itself the delivery, so darkness may mean the pointer is doing its job. It
     is a question to look at, not a verdict.
  4. **Archive share of delivered slots** — the `expires_when` on the standing
     `read_floor_archive_quota` position. If it climbs back over ~15% the quota
     is load-bearing again and that ruling needs re-opening.

A FLAG IS RAISED ONCE. Keyed to the finding's own content, so a known condition
stays quiet and re-arms when it changes. A weekly "still true" mail is how the
channel stops being read, and then you lose the week it mattered
(`preference-5c17ba9d`).

Reads the live counters, which accumulate real deliveries plus whatever the
backfill seeded. Writes one file: its own review sidecar.

NEVER import `server` here (the tunnel reaper) — see `_harness.py`.
"""
import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _harness  # noqa: E402

_rc = getattr(sys.stdout, 'reconfigure', None)
if _rc:
    try:
        _rc(encoding='utf-8', errors='replace')
    except Exception:
        pass

REVIEW_STATE_FILE = 'delivery_review.json'

# A note fetched this often with no index line is worth a pointer.
PROMOTE_AT = 0.10
# The rarity gate aims a position at roughly its own subject. Well above that
# and it is riding tasks it has nothing to say about.
POSITION_FURNITURE_AT = 0.25
# Below this many tasks every rate is noise. The whole point of the denominator.
MIN_TASKS = 60
# The `expires_when` on the archive-quota position, in one place.
ARCHIVE_SHARE_REOPEN = 0.15


def _now():
    return datetime.now(timezone.utc).isoformat()


def _state_path(m, project):
    return m._get_memory_path(project).parent / REVIEW_STATE_FILE


def read_state(m, project):
    try:
        p = _state_path(m, project)
        if not p.is_file():
            return {}
        d = json.loads(p.read_text(encoding='utf-8'))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def write_state(m, project, state):
    from mc.core import _atomic_write_text
    _atomic_write_text(_state_path(m, project),
                       json.dumps(state, indent=2, sort_keys=True) + '\n')


def indexed_notes(m, project):
    """Note filenames the curated index points at, matched the way the vault
    matches them.

    `_mem_link_key` is not decoration here: the vault's slugs drifted, so
    `[[feedback-grep-memory-dir]]` and `feedback_grep_memory_dir.md` are the
    same note. A naive regex reports that pair as a dangling reference AND the
    note as unindexed — two false findings from one wrong comparison.
    """
    mp = m._get_memory_path(project)
    curated = m._mem_split(mp.read_text(encoding='utf-8'))[0]
    keys = set()
    for target in re.findall(r'\[\[([^\]]+)\]\]', curated):
        keys.add(m._mem_link_key(target))
    for target in re.findall(r'\(([^)\s]+\.md)\)', curated):
        keys.add(m._mem_link_key(target.rsplit('.', 1)[0]))
    return keys


def finding(kind, subject, detail, evidence):
    """A finding plus the hash that keeps it from being raised twice."""
    h = hashlib.sha256(f'{kind}|{subject}|{detail}'.encode('utf-8')).hexdigest()[:16]
    return {'kind': kind, 'subject': subject, 'detail': detail,
            'evidence': evidence, 'hash': h}


def collect(m, project):
    from mc import memory_delivery as deliv
    st = deliv.read_stats(project)
    tasks = int(st.get('tasks', 0) or 0)
    units = st.get('units') or {}
    corpus = m.corpus_uids(project)
    out = []

    if tasks < MIN_TASKS:
        return tasks, [finding(
            'not-enough-data', 'delivery counters',
            f'only {tasks} tasks recorded (need {MIN_TASKS})',
            'run tools/memory-eval/delivery_backfill.py --reset to seed a '
            'baseline from transcript history')]

    indexed = indexed_notes(m, project)

    def is_note(uid):
        return uid.endswith('.md') and '#' not in uid

    # 1. promotion gap
    for uid, rec in sorted(units.items(), key=lambda kv: -kv[1].get('n', 0)):
        if not is_note(uid) or uid.startswith('position_'):
            continue
        n = int(rec.get('n', 0) or 0)
        if n / tasks < PROMOTE_AT:
            break
        if m._mem_link_key(uid.rsplit('.', 1)[0]) in indexed:
            continue
        out.append(finding(
            'promote', uid, f'{n}/{tasks} deliveries ({100.0 * n / tasks:.0f}%)',
            'fetched constantly with no line in the resident index — every one '
            'of those retrievals spent a slot re-discovering it'))

    # 2. a position that has become furniture
    for uid, rec in units.items():
        if not uid.startswith('position_'):
            continue
        n = int(rec.get('n', 0) or 0)
        if n / tasks < POSITION_FURNITURE_AT:
            continue
        out.append(finding(
            'position-furniture', uid,
            f'{n}/{tasks} tasks ({100.0 * n / tasks:.0f}%)',
            'a standing ruling in this many prompts is riding tasks it has '
            'nothing to say about. Narrow it with an explicit `triggers:` list '
            '— see the rarity gate in _position_trigger_max_df'))

    # 3. gone dark
    for uid, meta in sorted(corpus.items()):
        if meta[1] != 'topic' or uid in units:
            continue
        if m._mem_link_key(uid.rsplit('.', 1)[0]) not in indexed:
            continue
        out.append(finding(
            'dark', uid, f'0 deliveries over {tasks} tasks',
            'the index points at it and the floor never retrieves it. Its '
            'one-liner may be doing the whole job — a question, not a verdict'))

    # 4. is the archive quota load-bearing again
    slots = sum(int(v.get('n', 0) or 0) for v in units.values())
    arch = sum(int(v.get('n', 0) or 0) for k, v in units.items()
               if k.startswith('MEMORY_ARCHIVE.md'))
    share = arch / max(1, slots)
    if share > ARCHIVE_SHARE_REOPEN:
        out.append(finding(
            'archive-share', 'read_floor_archive_quota',
            f'{100.0 * share:.1f}% of delivered slots (reopen above '
            f'{100.0 * ARCHIVE_SHARE_REOPEN:.0f}%)',
            'the standing position says the quota is inert because dedupe '
            'removed the flood. This is its expires_when — re-open it'))
    return tasks, out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--all', action='store_true',
                    help='print every finding, including ones already flagged')
    ap.add_argument('--dry-run', action='store_true',
                    help='do not record what was flagged (a re-run then repeats)')
    args = ap.parse_args()

    m, project = _harness.wire()
    tasks, found = collect(m, project)
    state = read_state(m, project)
    seen = set(state.get('flagged') or [])

    fresh = [f for f in found if args.all or f['hash'] not in seen]
    print('delivery review — %d tasks measured, %d finding(s), %d new'
          % (tasks, len(found), len([f for f in found if f['hash'] not in seen])))

    if not fresh:
        print('\nnothing new. A known condition stays quiet until it changes.')
    for f in fresh:
        print('\n[%s] %s' % (f['kind'].upper(), f['subject']))
        print('  %s' % f['detail'])
        print('  %s' % f['evidence'])

    if not args.dry_run:
        state['flagged'] = sorted({f['hash'] for f in found})
        state['last_run'] = _now()
        state['tasks'] = tasks
        write_state(m, project, state)

    print('\nReports only. Nothing was promoted, demoted, or edited.')
    _harness.cleanup()


if __name__ == '__main__':
    main()
