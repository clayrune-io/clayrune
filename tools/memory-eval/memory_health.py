#!/usr/bin/env python3
"""Escalation rules for the weekly memory health check: turn the numbers the
check already computes into a finding a person is told about.

    python tools/memory-eval/eval.py             # prints what it would send
    python tools/memory-eval/eval.py --notify    # sends it, once per finding
    python tools/memory-eval/memory_health.py --set-recall-baseline 80.0

WHY THIS FILE EXISTS (2026-11-19). The weekly run measured memory getting worse
for ten weeks and reported each week into a journal file that nobody is paged
by. Its one alarm was "any note uncited AND unreachable", and that count had
filled with auto-written mint notes, so it was read as known noise. Inside the
noise, hand-written notes went unreachable and no one was told. A count that
mixes what a person wrote with what a trigger wrote cannot be an alarm, and an
alarm that only writes to a file is a log.

So this does three things, and `eval.py` calls it rather than containing it:

  1. SPLITS the unreachable count by who wrote the note (`split_unreachable`):
     hand-written, standing positions, mints, and the two files the memory
     system keeps for itself, which are in that list and are nobody's note.
  2. Applies FOUR FIXED RULES (the constants below, each with its reason).
  3. On a hit, sends ONE short mail per rule through the existing mailer
     (`tools/night-review/send_mail.py`), and only when the run was started
     with `--notify`. Without it the run prints what it would send.

EACH FINDING IS RAISED ONCE. The hash and the flagged-set are
`delivery_review.py`'s, reused, not copied: a finding's hash is stored when it
is sent and dropped when the condition clears, so it re-arms if it recurs. A
weekly "still true" mail is how the channel stops being read.

IT WRITES NOTHING INTO THE MEMORY DIRECTORY. The proposed link for an
unreachable note is a draft in the mail; a person applies it. A wrong link is a
plausible-but-wrong retrieval edge, and an unattended process that rewrites
what every prompt says is the authority guard wearing a different hat. Its one
file is its own state sidecar, beside the canary probe's, outside the vault.

NEVER import `server` here (the tunnel reaper) - see `_harness.py`.
"""
import argparse
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _harness  # noqa: E402
import delivery_review as _review  # noqa: E402  (the raise-once mechanism)

# ── who wrote the note ──────────────────────────────────────────────────────

KIND_HAND = 'hand'
KIND_POSITION = 'position'
KIND_MINT = 'mint'
KIND_SYSTEM = 'system'
KINDS = (KIND_HAND, KIND_POSITION, KIND_MINT, KIND_SYSTEM)

# A note is auto-written when its filename carries the prefix its writer gives
# it: `mc.memory._mint_slug` names every mint `mint_<kind>_<hash>`, and standing
# positions are `position_<slug>`. Everything else is treated as hand-written,
# on purpose: an unknown prefix that is wrongly counted as hand-written raises
# a finding someone can dismiss, while one wrongly counted as auto-written
# disappears into the noise this split exists to remove.
_AUTO_PREFIXES = (('mint_', KIND_MINT), ('position_', KIND_POSITION))

# ── the four rules ──────────────────────────────────────────────────────────

# (a) Hand-written notes unreachable at or above this count are a finding.
# One is enough: a note a person wrote is a rule or a lesson they expect every
# agent to have, and an unreachable one is deleted in effect. There is no
# tolerable number of those.
HAND_UNREACHABLE_ALARM = 1

# (b) Planted-fact recall this many points under the stored baseline...
# The canary set is small (tens of query pairs), so one pair moving is a couple
# of points and a single bad week is noise. Ten points is several facts lost.
RECALL_DROP_POINTS = 10.0
# ...in this many runs in a row. Two, so one odd run does not page anyone, and
# a real slide is reported within a fortnight instead of never.
RECALL_CONSECUTIVE_RUNS = 2

# (c) This many passing contradiction pairs (out of at least one pinned) is a
# finding. Zero means no correction outranks the stale fact it corrects: every
# agent asking gets the outdated answer, with nothing to tell it so.
CONTRADICTION_ALARM_PASSES = 0

# (d) The newest memory snapshot may be at most this old. A nightly snapshot is
# 24 hours apart; 26 leaves two hours for a late run before one missed night is
# reported. No snapshot at all is the same finding: the vault is outside git,
# so without a snapshot one disk failure loses all of it.
SNAPSHOT_MAX_AGE_HOURS = 26

# ── where things live ───────────────────────────────────────────────────────

# The state sidecar sits beside the canary probe's (data/memory-eval/ is
# gitignored), NOT in the memory directory like delivery_review's own.
STATE_FILE = _harness.repo_root() / 'data' / 'memory-eval' / 'health_state.json'
# The root tools/memory-snapshot.py writes to (a test pins the two together).
SNAP_ROOT = Path.home() / '.clayrune' / 'memory-snapshots'
SEND_MAIL = _harness.repo_root() / 'tools' / 'night-review' / 'send_mail.py'
SUBJECT_PREFIX = '[Clayrune memory] '

# Mail is a summary: the verdict, a few bullets, the drafted fix.
MAIL_WORD_LIMIT = 200
MAIL_NOTES_SHOWN = 5
# send_mail.py retries a failed name lookup for a little over two minutes.
MAILER_TIMEOUT_S = 300
# How far down the ranking a link target is looked for.
LINK_SEARCH_DEPTH = 20


def _now():
    return datetime.now(timezone.utc)


# ── 1. the split ────────────────────────────────────────────────────────────

def _system_files():
    """The files the memory system rewrites itself (the session log and the
    continuity record), by the names `mc.memory` gives them. The invariant
    lists them as topic files, and on a real vault both showed up as
    "hand-written, unreachable": a finding that names a file nobody wrote and
    that no link would ever clear. Read from the module, so a rename there
    fails here loudly instead of quietly re-filing them as hand-written."""
    import mc.memory as m
    return {m.SESSION_LOG_FILE.lower(), m.CONTINUITY_FILE.lower()}


def note_kind(name):
    low = str(name).lower()
    if low in _system_files():
        return KIND_SYSTEM
    for prefix, kind in _AUTO_PREFIXES:
        if low.startswith(prefix):
            return kind
    return KIND_HAND


def split_unreachable(violations):
    """The unreachable notes, grouped by who wrote them. Every kind is always
    present, so a count of zero prints as a zero and not as a missing line."""
    out = {k: [] for k in KINDS}
    for v in violations:
        out[note_kind(v)].append(v)
    return out


def split_line(violations):
    s = split_unreachable(violations)
    return ('  M1 by kind: hand-written %d | positions %d | mints %d | system files %d'
            % (len(s[KIND_HAND]), len(s[KIND_POSITION]), len(s[KIND_MINT]),
               len(s[KIND_SYSTEM])))


# ── measurements (read-only) ────────────────────────────────────────────────

def propose_link(m, project, note_path, linkable):
    """Draft the fix for one unreachable note: the reachable hand-written note
    that should carry a `[[wikilink]]` to it. Returns that note's filename, or
    None when nothing related ranks.

    The candidate is the best-ranked note for the unreachable note's own name
    and description that an agent can already reach, so the new edge starts
    somewhere that is actually delivered. It is a DRAFT: ranking finds shared
    vocabulary, not shared meaning, which is why a person applies it.
    """
    try:
        text = note_path.read_text(encoding='utf-8', errors='replace')
    except Exception as e:
        print(f'[memory_health] could not read {note_path.name}: {e}', file=sys.stderr)
        return None
    fm = m._note_frontmatter(text)
    query = ' '.join([re.sub(r'[_\-]+', ' ', note_path.stem),
                      fm.get('description') or text[:400]])
    me = note_path.name.lower()
    for h in m._memory_search(project, query, LINK_SEARCH_DEPTH, expand=0, record=None):
        base = str(h.get('file') or '').split('#')[0].lower()
        if base != me and base in linkable and note_kind(base) == KIND_HAND:
            return base
    return None


def measure_canaries(m, project, topk, expand):
    """Planted-fact recall and the contradiction pairs, from the canary probe's
    own functions. None when no canaries are pinned on this install."""
    import fact_canary_probe as fc
    if not fc.CANARIES_FILE.exists():
        return None
    results, _ = fc.run_canaries(m, project, fc.load_jsonl(fc.CANARIES_FILE), topk, expand)
    delivered, pairs, pct = fc.effectiveness_of(results)
    out = {'delivered': delivered, 'pairs': pairs, 'recall': pct,
           'contradiction_passed': None, 'contradiction_total': 0}
    if fc.PAIRS_FILE.exists():
        cons = fc.run_contradictions(m, project, fc.load_jsonl(fc.PAIRS_FILE), topk, expand)
        out['contradiction_passed'], out['contradiction_total'] = fc.contradiction_passes(cons)
    return out


def _snapshot_time(d):
    """When a snapshot was taken: the UTC stamp memory-snapshot.py puts at the
    front of the directory name, else the directory's mtime."""
    hit = re.match(r'(\d{8}T\d{6}Z)', d.name)
    if hit:
        try:
            return datetime.strptime(hit.group(1), '%Y%m%dT%H%M%SZ').replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return datetime.fromtimestamp(d.stat().st_mtime, timezone.utc)


def newest_snapshot(root, now=None):
    """(directory name, age in hours) of the newest snapshot, or None."""
    now = now or _now()
    root = Path(root)
    best = None
    if root.is_dir():
        for d in root.iterdir():
            if not d.is_dir():
                continue
            when = _snapshot_time(d)
            if best is None or when > best[1]:
                best = (d.name, when)
    if best is None:
        return None
    return best[0], (now - best[1]).total_seconds() / 3600.0


# ── 2. the rules ────────────────────────────────────────────────────────────
# Each returns {'label', 'status', 'finding'}; `finding` is None on the quiet
# side of the threshold. A finding carries the hashes that keep it from being
# raised twice: delivery_review.finding's, so there is one definition of "the
# same finding".

def _hash(kind, subject):
    return _review.finding(kind, subject, '', '')['hash']


def _plural(n, word):
    return f'{n} {word}' if n == 1 else f'{n} {word}s'


def _age_text(hours):
    return f'{hours:.0f} hours' if hours < 72 else f'{hours / 24:.0f} days'


def _fit(head, notes, tail):
    """The body, with as many note lines as stay under the word limit."""
    shown = min(len(notes), MAIL_NOTES_SHOWN)
    while True:
        lines = head + notes[:shown]
        if len(notes) > shown:
            lines.append(f'- and {len(notes) - shown} more, listed in the run output')
        body = '\n'.join(lines + tail) + '\n'
        if len(body.split()) < MAIL_WORD_LIMIT or shown == 0:
            return body
        shown -= 1


def rule_hand_unreachable(split, proposals, seen):
    """(a) `proposals` maps an unreachable hand-written note to the note that
    should link to it (or None). One hash PER NOTE: the rule's single mail goes
    out when any of them is new, so a third note going dark is reported while
    two known ones stay quiet, and fixing one of several sends nothing."""
    hand = sorted(split[KIND_HAND])
    label = '(a) hand-written unreachable'
    if len(hand) < HAND_UNREACHABLE_ALARM:
        return {'label': label, 'status': f'{len(hand)} -> ok', 'finding': None}
    hashes = {n: _hash('hand-unreachable', n) for n in hand}
    new = [n for n in hand if hashes[n] not in seen]
    total = sum(len(v) for v in split.values())

    def line(n):
        target = proposals.get(n)
        stem = n.rsplit('.', 1)[0]
        if target:
            return f'- {n}: add [[{stem}]] to {target}'
        return f'- {n}: no related reachable note ranked; give it an index line'

    it = 'it' if len(hand) == 1 else 'them'
    they_stay = 'it stays' if len(hand) == 1 else 'they stay'
    head = [
        f'{_plural(len(hand), "hand-written memory note")} can reach no agent '
        f'({len(new)} new since the last report).',
        '',
        f'- Unreachable in total: {total} (hand-written {len(hand)}, positions '
        f'{len(split[KIND_POSITION])}, mints {len(split[KIND_MINT])}, system files '
        f'{len(split[KIND_SYSTEM])}).',
        f'- Nothing links to {it} and no pinned task retrieves {it}.',
        '',
        'Proposed links, drafts only (the run wrote nothing; a person applies them):',
    ]
    tail = ['', f'If nothing is done, {they_stay} invisible to every agent.']
    notes = [line(n) for n in new + [n for n in hand if n not in new]]
    return {'label': label,
            'status': f'{len(hand)} -> FINDING ({len(new)} new)',
            'finding': {'rule': 'a', 'hashes': sorted(hashes.values()),
                        'subject': f'{_plural(len(hand), "hand-written note")} unreachable',
                        'body': _fit(head, notes, tail)}}


def rule_recall(canary, baseline, prior_streak):
    """(b) Returns the rule result plus the streak to store. Compared at one
    decimal place, the precision the baseline is typed in and the number is
    printed at, so exactly ten points under is not a finding by float error."""
    label = '(b) planted-fact recall'
    if canary is None:
        return ({'label': label, 'finding': None,
                 'status': 'NOT MEASURED - no canaries pinned on this install'},
                prior_streak)
    recall = canary['recall']
    if baseline is None:
        return ({'label': label, 'finding': None,
                 'status': f'{recall:.1f}%, NOT MEASURED against a baseline - none '
                           'stored. Set it once: python tools/memory-eval/'
                           'memory_health.py --set-recall-baseline <percent>'},
                prior_streak)
    drop = round(float(baseline) - recall, 1)
    under = drop > RECALL_DROP_POINTS
    streak = prior_streak + 1 if under else 0
    status = (f'{recall:.1f}% vs baseline {float(baseline):.1f}% ('
              + (f'{drop:.1f} points under' if drop >= 0 else f'{-drop:.1f} points over'))
    if not under:
        return {'label': label, 'status': status + ') -> ok', 'finding': None}, streak
    status += f', run {streak} of {RECALL_CONSECUTIVE_RUNS})'
    if streak < RECALL_CONSECUTIVE_RUNS:
        return {'label': label, 'status': status + ' -> watching', 'finding': None}, streak
    body = '\n'.join([
        f'Planted-fact recall is {recall:.1f}% against a baseline of '
        f'{float(baseline):.1f}%: {drop:.1f} points under, {streak} runs in a row.',
        '',
        f'- {canary["delivered"]} of {canary["pairs"]} canary queries delivered their fact.',
        f'- Rule: more than {RECALL_DROP_POINTS:.0f} points under baseline in '
        f'{RECALL_CONSECUTIVE_RUNS} consecutive runs.',
        '- Which facts miss: python tools/memory-eval/fact_canary_probe.py',
        '',
        'No fix is drafted for this one: it means known facts are no longer '
        'reaching agents asked in your wording, and the probe output says which.',
    ]) + '\n'
    return ({'label': label, 'status': status + ' -> FINDING',
             'finding': {'rule': 'b',
                         'hashes': [_hash('recall-under-baseline', 'planted-fact recall')],
                         'subject': f'planted-fact recall {recall:.1f}%, {drop:.1f} '
                                    'points under baseline',
                         'body': body}}, streak)


def rule_contradiction(canary):
    """(c)"""
    label = '(c) contradiction pass rate'
    if canary is None or not canary['contradiction_total']:
        return {'label': label, 'finding': None,
                'status': 'NOT MEASURED - no contradiction pairs pinned on this install'}
    passed, total = canary['contradiction_passed'], canary['contradiction_total']
    if passed > CONTRADICTION_ALARM_PASSES:
        return {'label': label, 'status': f'{passed} of {total} -> ok', 'finding': None}
    body = '\n'.join([
        f'No correction outranks the stale fact it corrects: {passed} of '
        f'{_plural(total, "pinned contradiction pair")} pass{"es" if total == 1 else ""}.',
        '',
        '- An agent asking about these subjects is handed the outdated answer.',
        '- A pair passes only when the corrected note is delivered ahead of the stale one.',
        '- Which pairs, and at what rank: python tools/memory-eval/fact_canary_probe.py',
        '',
        'No fix is drafted: the stale record needs retiring or the correction '
        'needs a line that ranks, and that is a judgement about the fact.',
    ]) + '\n'
    return {'label': label, 'status': f'{passed} of {total} -> FINDING',
            'finding': {'rule': 'c',
                        'hashes': [_hash('contradiction-zero', 'contradiction pairs')],
                        'subject': f'contradiction pass rate {passed} of {total}',
                        'body': body}}


def rule_snapshot(newest):
    """(d) `newest` is `newest_snapshot()`'s result."""
    label = '(d) newest memory snapshot'
    fix = ['', 'Take one now: python tools/memory-snapshot.py --label manual',
           'The memory directory is outside git, so the snapshot is its only copy.']
    if newest is None:
        subject = 'no memory snapshot exists'
        status = 'none found -> FINDING'
        head = ['No memory snapshot exists under ~/.clayrune/memory-snapshots.', '',
                f'- Rule: the newest snapshot may be at most {SNAPSHOT_MAX_AGE_HOURS} hours old.']
    else:
        name, hours = newest
        if hours <= SNAPSHOT_MAX_AGE_HOURS:
            return {'label': label, 'finding': None,
                    'status': f'{hours:.1f} hours old ({name}) -> ok'}
        subject = f'newest memory snapshot is {_age_text(hours)} old'
        status = f'{hours:.1f} hours old ({name}) -> FINDING'
        head = [f'The newest memory snapshot is {_age_text(hours)} old; the limit is '
                f'{SNAPSHOT_MAX_AGE_HOURS} hours.', '',
                f'- Newest: {name}',
                '- Everything written to memory since then has no copy.']
    return {'label': label, 'status': status,
            'finding': {'rule': 'd', 'hashes': [_hash('snapshot-stale', 'memory snapshot')],
                        'subject': subject, 'body': '\n'.join(head + fix) + '\n'}}


# ── 3. delivery ─────────────────────────────────────────────────────────────

def _run_mailer(argv):
    """The one call into the existing mailer. Tests replace this."""
    return subprocess.run(argv, timeout=MAILER_TIMEOUT_S).returncode


def send(finding):
    """Mail one finding through tools/night-review/send_mail.py. True when the
    mailer exited 0. The body goes through a temp file outside the vault."""
    fd, path = tempfile.mkstemp(prefix='mc-memory-health-', suffix='.txt')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as fh:
            fh.write(finding['body'])
        rc = _run_mailer([sys.executable, str(SEND_MAIL),
                          '--subject', SUBJECT_PREFIX + finding['subject'],
                          '--body-file', path])
    except Exception as e:
        print(f'[memory_health] mailer did not run: {e}', file=sys.stderr)
        rc = 1
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    return rc == 0


def read_state():
    return _review.read_state(None, None, path=STATE_FILE)


def write_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _review.write_state(None, None, state, path=STATE_FILE)


def report(m, project, violations, linkable, topk, expand, notify=False):
    """Apply the four rules to this run and print the result. With `notify`,
    mail each finding not already raised and record what was raised; without
    it, print what would be sent and record nothing. Returns the findings that
    were (or would be) sent."""
    state = read_state()
    seen = set(state.get('flagged') or [])
    split = split_unreachable(violations)

    mem_dir = m._get_memory_path(project).parent
    on_disk = {f.name.lower(): f for f in mem_dir.glob('*.md')}
    linkable = {n.lower() for n in linkable}
    proposals = {n: propose_link(m, project, on_disk[n], linkable)
                 for n in split[KIND_HAND] if n in on_disk}

    canary = measure_canaries(m, project, topk, expand)
    recall_rule, streak = rule_recall(canary, state.get('recall_baseline'),
                                      int(state.get('recall_under_streak') or 0))
    rules = [rule_hand_unreachable(split, proposals, seen),
             recall_rule,
             rule_contradiction(canary),
             rule_snapshot(newest_snapshot(SNAP_ROOT))]

    print('  HEALTH RULES (a finding is mailed once, then stays quiet until it '
          'clears and recurs)')
    for r in rules:
        print(f'     {r["label"]:<29}: {r["status"]}')

    found = [r['finding'] for r in rules if r['finding']]
    fresh = [f for f in found if set(f['hashes']) - seen]
    flagged = set()
    for f in found:
        if f not in fresh:
            flagged.update(f['hashes'])
    if not fresh:
        print('  nothing new to send. A known condition stays quiet until it changes.')
    elif not notify:
        print(f'  WOULD SEND {_plural(len(fresh), "mail")} (not sent: the run was '
              'started without --notify)')
    for f in fresh:
        subject = SUBJECT_PREFIX + f['subject']
        if not notify:
            print(f'\n  --- subject: {subject}')
            for ln in f['body'].rstrip('\n').split('\n'):
                print(f'  | {ln}')
        elif send(f):
            print(f'  SENT: {subject}')
            flagged.update(f['hashes'])
        else:
            # Left unflagged, so the next run tries again. What was already
            # known stays known.
            print(f'  SEND FAILED, will retry next run: {subject}')
            flagged.update(set(f['hashes']) & seen)

    if notify:
        state['flagged'] = sorted(flagged)
        state['recall_under_streak'] = streak
        state['last_run'] = _now().isoformat()
        write_state(state)
    print('  Reports only. Nothing in the memory directory was written.')
    return fresh


def main():
    ap = argparse.ArgumentParser(
        description='Stored settings for the memory health rules. The rules '
                    'themselves run from eval.py.')
    ap.add_argument('--set-recall-baseline', type=float, metavar='PERCENT',
                    help='store the planted-fact recall baseline rule (b) compares '
                         'against (the effectiveness figure fact_canary_probe.py '
                         'printed when the canaries were pinned)')
    args = ap.parse_args()
    root = str(_harness.repo_root())
    if root not in sys.path:
        sys.path.insert(0, root)   # write_state needs mc.core
    state = read_state()
    if args.set_recall_baseline is not None:
        if not 0.0 < args.set_recall_baseline <= 100.0:
            raise SystemExit('the baseline is a percentage between 0 and 100')
        state['recall_baseline'] = round(args.set_recall_baseline, 1)
        state['recall_baseline_set'] = _now().isoformat()
        write_state(state)
    base = state.get('recall_baseline')
    print(f'state file      : {STATE_FILE}')
    print(f'recall baseline : {"not set" if base is None else f"{base:.1f}%"}')
    print(f'runs under it   : {int(state.get("recall_under_streak") or 0)}')
    print(f'findings raised : {len(state.get("flagged") or [])}')
    _harness.assert_no_server()


if __name__ == '__main__':
    main()
