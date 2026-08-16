#!/usr/bin/env python3
"""Memory-system invariants. REPORTS ONLY — changes nothing, ever.

    python tools/memory-eval/eval.py
    python tools/memory-eval/eval.py --inject-canary   # prove the check can fail

THE INVARIANT (M1)

    No note may be both UNCITED and UNREACHABLE.

A note reaches an agent by exactly two channels: the always-loaded index cites it
(the push-by-residency channel), or the BM25 read floor surfaces it for some real
task (the push-by-ranking channel). The pull channel — an agent choosing to go
open a file — is measured at 5% of sessions with 66 of 76 notes never opened
once, so it is not a channel you may rely on.

A note with neither is not "archived". It is deleted, with a file on disk as a
receipt. That is the failure this whole redesign exists to prevent, and it is
silent by construction, which is why it needs a gate rather than a habit.

Also reports:
  M5 — corpus unit counts by class, so a change in what the ranker indexes shows
       up as a number rather than as a surprise.
  M8 — that `server` was never imported. Importing it autostarts the tunnel
       supervisor whose orphan reaper kills the operator's cloudflared; this is
       asserted on `sys.modules`, NOT by grepping output, because the reaper runs
       on a daemon thread and a grep races it.
  The CONDENSE DEADBAND, published rather than assumed — see below.
"""

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _harness  # noqa: E402

SUITE = _harness.repo_root() / 'data' / 'memory-eval' / 'suite-v1.jsonl'


def load_suite():
    if not SUITE.exists():
        raise SystemExit(f'no pinned suite at {SUITE} — run pin_suite.py first')
    return [json.loads(ln)['task'] for ln in
            SUITE.read_text(encoding='utf-8').splitlines() if ln.strip()]


def cited_notes(mem_dir, index_name):
    """Every note referenced from the index or from another note.

    Counts BOTH markdown links `[x](file.md)` and `[[wikilinks]]` — the link
    layer made wikilinks real retrieval edges in 2026-08-09, so a wikilink is a
    citation, not decoration.
    """
    cited = set()
    for f in sorted(mem_dir.glob('*.md')):
        try:
            txt = f.read_text(encoding='utf-8', errors='replace')
        except Exception:
            continue
        for target in re.findall(r'\]\(([^)]+\.md)\)', txt):
            cited.add(os.path.basename(target).lower())
        for target in re.findall(r'\[\[([^\]]+)\]\]', txt):
            name = target.strip().lower()
            cited.add(name if name.endswith('.md') else name + '.md')
    cited.discard(index_name.lower())
    return cited


def reachable_notes(m, project, tasks, topk, expand):
    """Notes the read floor surfaces for at least one real task."""
    seen = set()
    for t in tasks:
        for h in m._memory_search(project, t, topk, expand=expand):
            seen.add(h['file'].split('#')[0].lower())
    return seen


def deadband(m, project):
    """Publish which condense trigger can actually fire. Do not assume — this
    item's history has two contradictory accounts of it, and BOTH are half right.

    Structured mode (live) fires on `lines > index_line_budget` OR
    `bytes > _index_byte_cap()`. The mechanical floor evicts managed entries at
    `lines > index_line_hard_floor` OR `bytes > _index_byte_cap() - 1024`.

    So the two halves behave OPPOSITELY, which is why one reviewer said condense
    can never fire and another said it can:

      BYTE  floor 23552 < trigger 24576  -> the floor evicts first, so the byte
                                            trigger is unreachable. Suppressed.
      LINE  trigger 160 < floor 185      -> the trigger fires first. Live.

    Any fix to this deadband must therefore cover the LINE trigger too; a
    byte-only fix leaves the working half alone and the broken half broken.
    """
    from mc import state
    mem_path = m._get_memory_path(project)
    text = mem_path.read_text(encoding='utf-8', errors='replace')
    mode = (state.CONFIG.get('condense_mode', 'agent') or 'agent')
    byte_cap = m._index_byte_cap()
    byte_floor = m._index_byte_floor()
    line_budget = int(state.CONFIG.get('index_line_budget', 160) or 160)
    line_floor = int(state.CONFIG.get('index_line_hard_floor', 185) or 185)
    return {
        'mode': mode,
        'lines': len(text.splitlines()),
        'bytes': len(text.encode('utf-8')),
        'line_trigger': line_budget, 'line_floor': line_floor,
        'byte_trigger': byte_cap, 'byte_floor': byte_floor,
        'byte_half_suppressed': byte_floor < byte_cap,
        'line_half_live': line_budget < line_floor,
    }


def run(corpus_snapshot=None, canary=False):
    m, project = _harness.wire(corpus_snapshot=corpus_snapshot)
    topk, expand = _harness.live_signature()
    mem_path = m._get_memory_path(project)
    mem_dir = mem_path.parent
    arch_name = m._get_archive_path(project).name

    if canary:
        # Prove the check CAN fail. A gate that cannot fail is not a gate — so
        # inject an uncited, unrankable note into a COPY and expect +1 violation.
        tmp = Path(tempfile.mkdtemp(prefix='mc-eval-canary-'))
        (tmp / 'memory').mkdir()
        for f in mem_dir.glob('*.md'):
            shutil.copy2(f, tmp / 'memory' / f.name)
        (tmp / 'memory' / 'zzz_canary_unreachable.md').write_text(
            'Qqxzzy vorplex thrunge. Nothing links here and no real task shares '
            'this vocabulary.\n', encoding='utf-8')
        return run(corpus_snapshot=str(tmp), canary=False)

    tasks = load_suite()
    topics = set(_harness.topic_files(m, project))
    topics_lc = {t.lower() for t in topics}

    cited = cited_notes(mem_dir, mem_path.name) & topics_lc
    reach = reachable_notes(m, project, tasks, topk, expand) & topics_lc
    violations = sorted(topics_lc - cited - reach)

    print(f'corpus: {len(topics)} topic files | suite: {len(tasks)} tasks '
          f'| signature topk={topk} expand={expand}')
    print(f'  cited by index or another note : {len(cited)}')
    print(f'  reachable via the read floor   : {len(reach)}')
    print(f'  M1 VIOLATIONS (uncited AND unreachable): {len(violations)}')
    for v in violations:
        print(f'     ! {v}')

    units = m._mem_corpus(mem_dir, mem_path.name, arch_name)
    by_cls = {}
    for u in units:
        by_cls[u['cls']] = by_cls.get(u['cls'], 0) + 1
    print(f'  M5 corpus units: {len(units)} {by_cls}')

    d = deadband(m, project)
    print(f'  M8 server imported: {"server" in sys.modules}')
    print(f'  CONDENSE DEADBAND (mode={d["mode"]}): '
          f'index at {d["lines"]} lines / {d["bytes"]} bytes')
    print(f'     byte: trigger {d["byte_trigger"]} vs floor {d["byte_floor"]} '
          f'-> {"SUPPRESSED (floor evicts first)" if d["byte_half_suppressed"] else "live"}')
    print(f'     line: trigger {d["line_trigger"]} vs floor {d["line_floor"]} '
          f'-> {"LIVE (trigger fires first)" if d["line_half_live"] else "suppressed"}')
    _harness.assert_no_server()
    return violations


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--corpus-snapshot', default=None)
    ap.add_argument('--inject-canary', action='store_true',
                    help='inject a synthetic uncited+unreachable note into a COPY '
                         'and confirm the invariant catches it')
    args = ap.parse_args()
    v = run(corpus_snapshot=args.corpus_snapshot, canary=args.inject_canary)
    return 0 if not v else 1


if __name__ == '__main__':
    sys.exit(main())
