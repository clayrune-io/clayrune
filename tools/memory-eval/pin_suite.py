#!/usr/bin/env python3
"""Pin the evaluation suite so every stage of the memory plan is measured against
the SAME tasks.

    python tools/memory-eval/pin_suite.py            # write suite-v1
    python tools/memory-eval/pin_suite.py --check    # re-extract, compare sha256

THE PROBLEM THIS SOLVES. Every probe so far re-derived its task set from whatever
transcripts happened to be on disk that day, so the denominator moved between
runs: 142, then 170, then 179, then 180 tasks. A stage that "improved
reachability by 5 files" against a moving suite has proved nothing. Worse, the
migration plan gates stages on numeric thresholds — an abort rule comparing
today's run against a differently-sized suite fires spuriously or, far worse,
fails to fire.

So the suite is frozen: the task text AND the explicit list of transcript files
it came from. Re-extraction reads the pinned manifest, not the live session set,
which is what makes it deterministic as new sessions land.

OUTPUT IS GITIGNORED AND MUST STAY SO. `suite-v1.jsonl` holds VERBATIM operator
prompts. `.gitignore` carries `data/memory-eval/`, added in the same commit as
this file; `build-macos.spec` bundles only `data/agent_reference`, checked. It
also must never live under `data/projects/` — `load_projects()` treats every
`*.json` there as a project record and a stray file 500s both restart endpoints.
"""

import argparse
import glob
import hashlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _harness  # noqa: E402

SUITE_DIR = _harness.repo_root() / 'data' / 'memory-eval'
SUITE = SUITE_DIR / 'suite-v1.jsonl'
MANIFEST = SUITE_DIR / 'suite-v1.manifest.json'
CLAUDE_PROJECTS = Path.home() / '.claude' / 'projects'
MIN_TASK_CHARS = 25


def first_user_task(path):
    """The dispatched task = the first real user message of a session.

    Skips blocks starting with '<' — those are harness-injected system reminders,
    not something a human asked for.
    """
    try:
        fh = open(path, 'r', encoding='utf-8', errors='replace')
    except Exception:
        return None
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
            return text[:2000]
    return None


def discover():
    """Every mission_control transcript on disk, sorted for determinism."""
    dirs = [x for x in os.listdir(CLAUDE_PROJECTS)
            if 'mission-control' in x and 'scratch' not in x and 'demo' not in x]
    files = []
    for x in dirs:
        files += glob.glob(os.path.join(CLAUDE_PROJECTS, x, '*.jsonl'))
    return sorted(files)


def extract(files):
    """(rows, trivial_count). Trivial follow-ups are excluded: taking the first
    user message verbatim otherwise includes sessions whose opener is 'ok' or
    'continue'. Those are not dispatched tasks, and they inflated an early
    '29% of tasks surface nothing' figure that was pure artifact."""
    rows, trivial = [], 0
    for f in files:
        t = first_user_task(f)
        if not t:
            continue
        if len(t) < MIN_TASK_CHARS:
            trivial += 1
            continue
        rows.append({'source': Path(f).relative_to(CLAUDE_PROJECTS).as_posix(), 'task': t})
    rows.sort(key=lambda r: r['source'])
    return rows, trivial


def serialise(rows):
    return ''.join(json.dumps(r, ensure_ascii=False, sort_keys=True) + '\n'
                   for r in rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--check', action='store_true',
                    help='re-extract from the pinned manifest and compare sha256')
    args = ap.parse_args()

    if args.check:
        if not MANIFEST.exists():
            raise SystemExit('no pinned suite yet — run without --check first')
        man = json.loads(MANIFEST.read_text(encoding='utf-8'))
        # Re-extract from the PINNED file list, not from discover(). This is the
        # whole point: new sessions landing must not change the suite.
        #
        # Paths are stored dir-qualified. An earlier cut stored bare basenames
        # plus a single `dir`, which silently dropped 22 of 180 transcripts —
        # mission_control's history spans SEVERAL encoded project dirs (the
        # underscore/dash variants `_native_memory_path` already has to reconcile),
        # so one dir cannot address them all. The --check gate caught it, which is
        # the only reason this is a comment and not a wrong baseline.
        pinned = [str(CLAUDE_PROJECTS / rel) for rel in man['files']]
        missing = [p for p in pinned if not os.path.exists(p)]
        rows, _ = extract(pinned)
        got = hashlib.sha256(serialise(rows).encode('utf-8')).hexdigest()
        same = got == man['sha256']
        print(f'pinned n={man["n"]}  re-extracted n={len(rows)}')
        print(f'sha256 {"MATCH" if same else "DIFFER"}')
        if missing:
            print(f'  {len(missing)} pinned transcript(s) missing from disk')
        return 0 if (same and not missing) else 1

    files = discover()
    rows, trivial = extract(files)
    SUITE_DIR.mkdir(parents=True, exist_ok=True)
    body = serialise(rows)
    SUITE.write_text(body, encoding='utf-8')
    MANIFEST.write_text(json.dumps({
        'n': len(rows),
        'sha256': hashlib.sha256(body.encode('utf-8')).hexdigest(),
        # dir-qualified and posix-normalised so the manifest is portable and so a
        # multi-dir history round-trips. See the --check branch.
        'files': [Path(f).relative_to(CLAUDE_PROJECTS).as_posix() for f in files],
        'trivial_excluded': trivial,
        'min_task_chars': MIN_TASK_CHARS,
    }, indent=2), encoding='utf-8')
    print(f'pinned {len(rows)} tasks ({trivial} trivial excluded) -> {SUITE}')
    print(f'sha256 {hashlib.sha256(body.encode("utf-8")).hexdigest()}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
