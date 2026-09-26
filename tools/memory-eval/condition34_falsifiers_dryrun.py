#!/usr/bin/env python3
"""Condition 34 falsifiers (MEMORY_DESIGN_V2_SPEC.md sec 8.9) — DRY RUN ONLY.

    python tools/memory-eval/condition34_falsifiers_dryrun.py

Prints the three `write_position` records Condition 34 calls for, rendered
through the REAL `mc.memory.write_position` path so the output is exactly the
markdown that would land on disk — but written to a throwaway temp directory
this process creates and deletes, never to any project's real memory dir.
There is no flag to point this at a live project: that is deliberate. The
build brief for MC-944 step 8 is explicit that these three get reviewed by a
human before they are POSTed for real; this script's whole job is to produce
that reviewable text, not to post it.

After review, POST for real the ordinary way: `mc.memory.write_position`
against the target project, or the agent-facing route that wraps it.

Falsifiers, from sec 8.9's table:

  1. keep one hop, build no depth   -- holds_while: topic_notes < 1000
  2. the corpus cache holds         -- holds_while: topic_notes < 1500
  3. the archive quota stays at 2   -- holds_while: delivered(archive) < 0.15

Each is framed as a declined-to-build/declined-to-change position, matching
every other standing position in this repo (a decision NOT to do something),
because that is what all three actually are: a recommendation made under a
condition, with the condition made evaluable so the weekly positions-review
job (mc-position-review) can trip it automatically instead of the promise
just sitting in prose forever.
"""
import shutil
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import mc.memory as mem  # noqa: E402

FALSIFIERS = [
    dict(
        subject='build chain-walking depth beyond one hop',
        verdict='declined',
        reason=(
            "Measured over 349 tasks: 54 of ~86 topic units have ever arrived via a "
            "link, only 2 of those ever arrived ONLY via a link, and hop-2-only "
            "nodes are near zero at this corpus size. Content-addressed ranking "
            "carries the load; the one hop that IS kept is reserved for supersession "
            "(sec 8.7-8.8), not multi-hop traversal. Building depth now would be "
            "speculative construction against a corpus with no chains to justify it "
            "(sec 8.9's closing argument)."
        ),
        holds_while='topic_notes < 1000',
        durability='measured',
        revisit_if=(
            'topic_notes reaches 1000 (re-measure the hop distribution) OR '
            'hop-2-only nodes exceed 5% of the corpus OR any chain exceeds length 2'
        ),
    ),
    dict(
        subject='replace the corpus cache with a different build/cache strategy',
        verdict='declined',
        reason=(
            "sec 13.3 identifies the corpus cache's first break point at 1,500 "
            "topic notes (rebuild cost crossing the per-turn budget). Below that, "
            "the existing cache-on-build-with-invalidation design holds and a "
            "replacement would be solving a problem that has not occurred yet."
        ),
        holds_while='topic_notes < 1500',
        durability='measured',
        revisit_if='topic_notes reaches 1500 (sec 13.3\'s first break)',
    ),
    dict(
        subject='raise the archive read-floor quota above 2 slots',
        verdict='declined',
        reason=(
            "This is the existing archive-quota position's own reopening clause, "
            "made evaluable per Condition 34 rather than left as prose: the archive "
            "class is delivered in a minority of read-floor slots today, and the "
            "quota was set at 2 against that measurement, not as an arbitrary cap."
        ),
        holds_while='delivered(archive) < 0.15',
        durability='measured',
        revisit_if='the delivered(archive) share reaches or exceeds 0.15',
    ),
]


def main():
    tmp = Path(tempfile.mkdtemp(prefix='c34-falsifiers-dryrun-'))
    orig_get_memory_path = mem._get_memory_path
    project = {'id': 'DRY-RUN-NOT-A-REAL-PROJECT'}
    try:
        mem._get_memory_path = lambda p: tmp / 'MEMORY.md'
        print(f"# Condition 34 falsifiers — DRY RUN, written under {tmp}")
        print("# (never against a real project's memory dir; nothing here is posted)\n")
        for f in FALSIFIERS:
            fname = mem.write_position(
                project, f['subject'], f['verdict'], f['reason'],
                holds_while=f['holds_while'], durability=f['durability'],
                revisit_if=f['revisit_if'])
            path = tmp / fname
            print(f"{'=' * 78}\n# {path.name}\n{'=' * 78}")
            print(path.read_text(encoding='utf-8'))
            print()
    finally:
        mem._get_memory_path = orig_get_memory_path
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    main()
