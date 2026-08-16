#!/usr/bin/env python3
"""Snapshot and restore a project's memory corpus. Every rollback in the memory
plan depends on this file being correct.

    python tools/memory-snapshot.py --label pre-s3
    python tools/memory-snapshot.py --list
    python tools/memory-snapshot.py --verify <snapshot-dir>
    python tools/memory-snapshot.py --restore <snapshot-dir> --only foo.md bar.md

WHERE SNAPSHOTS LIVE, and why it is not negotiable:

  * NOT `data/projects/` — `load_projects()` treats every `*.json` there as a
    project record, so a stray file 500s both restart endpoints. CLAUDE.md flags
    this as load-bearing.
  * NOT `_scratch/` — disposable by definition and gitignored, so it is invisible
    rather than durable. A rollback artifact must outlive a cleanup.
  * NOT the repo at all — the corpus is operator data.
  * `~/.clayrune/` is where this project already keeps durable operator state
    (secrets store, browser profiles, cloudflared PID ledger). Snapshots join it.

COPY ORDER IS LOAD-BEARING. MEMORY.md first, then MEMORY_ARCHIVE.md, then topic
files. Floor eviction moves lines OUT of MEMORY.md and INTO the archive, so if an
eviction lands mid-snapshot this order yields at worst a DUPLICATED line. The
reverse order can lose one outright.

RESTORE IS DELIBERATELY CRIPPLED (2026-08-16). `--only` is REQUIRED and MEMORY.md
and MEMORY_ARCHIVE.md may never be restore targets. The buildability review found
that a whole-dir restore is not a rollback at all: rolling MEMORY.md back to an
older copy also rolls back every watermark and every supersede that happened
since, silently reintroducing state the system had correctly retired. Topic files
have no such hazard — nothing in `mc/memory.py` writes them (verified: every
write site targets MEMORY.md or the archive) — so a scoped topic-file restore is
sound and is the only restore offered. To roll MEMORY.md back, do it by hand,
with your eyes open, having read the diff.
"""

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / 'memory-eval'))
import _harness  # noqa: E402

SNAP_ROOT = Path.home() / '.clayrune' / 'memory-snapshots'
PROTECTED = {'MEMORY.md', 'MEMORY_ARCHIVE.md'}


def sha256(p):
    h = hashlib.sha256()
    with open(p, 'rb') as fh:
        for chunk in iter(lambda: fh.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


def memory_dir():
    m, project = _harness.wire()
    return m._get_memory_path(project).parent


def ordered_files(src):
    """MEMORY.md, then the archive, then everything else. See module docstring."""
    files = sorted(src.glob('*.md'))
    head = [f for f in files if f.name == 'MEMORY.md']
    arch = [f for f in files if f.name == 'MEMORY_ARCHIVE.md']
    rest = [f for f in files if f.name not in PROTECTED]
    return head + arch + rest


def create(label):
    src = memory_dir()
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    dest = SNAP_ROOT / f'{stamp}-{label}'
    (dest / 'memory').mkdir(parents=True)
    entries = []
    for f in ordered_files(src):
        target = dest / 'memory' / f.name
        shutil.copy2(f, target)
        entries.append({'name': f.name, 'size': target.stat().st_size,
                        'sha256': sha256(target)})
    (dest / 'manifest.json').write_text(json.dumps({
        'created': datetime.now(timezone.utc).isoformat(),
        'label': label,
        'source': str(src),
        'count': len(entries),
        'files': entries,
    }, indent=2), encoding='utf-8')
    print(f'snapshot: {dest}')
    print(f'  {len(entries)} files, {sum(e["size"] for e in entries)} bytes')
    return dest


def verify(snap):
    snap = Path(snap)
    man = json.loads((snap / 'manifest.json').read_text(encoding='utf-8'))
    ok = bad = missing = 0
    for e in man['files']:
        p = snap / 'memory' / e['name']
        if not p.exists():
            missing += 1
            print(f'  MISSING {e["name"]}')
            continue
        if sha256(p) == e['sha256']:
            ok += 1
        else:
            bad += 1
            print(f'  SHA MISMATCH {e["name"]}')
    print(f'verify {snap.name}: {ok}/{man["count"]} ok, {bad} mismatched, {missing} missing')
    return bad == 0 and missing == 0 and ok == man['count']


def restore(snap, only):
    """Scoped topic-file restore. See the module docstring for why this refuses
    to touch MEMORY.md and the archive."""
    snap = Path(snap)
    bad = [n for n in only if n in PROTECTED]
    if bad:
        raise SystemExit(
            f'refusing to restore {", ".join(bad)}. Restoring these rolls back every\n'
            'watermark and supersede since the snapshot, silently reviving retired\n'
            'state — that is not a rollback. Do it by hand after reading the diff.')
    dst = memory_dir()
    done = []
    for name in only:
        src = snap / 'memory' / name
        if not src.exists():
            raise SystemExit(f'{name} is not in {snap}')
        shutil.copy2(src, dst / name)
        done.append(name)
    print(f'restored {len(done)} file(s) into {dst}:')
    for n in done:
        print(f'  {n}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--label', default='manual')
    ap.add_argument('--list', action='store_true')
    ap.add_argument('--verify')
    ap.add_argument('--restore')
    ap.add_argument('--only', nargs='+',
                    help='REQUIRED with --restore: the topic files to restore')
    args = ap.parse_args()

    if args.list:
        if not SNAP_ROOT.is_dir():
            print('no snapshots yet')
            return 0
        for d in sorted(SNAP_ROOT.iterdir()):
            man = d / 'manifest.json'
            n = json.loads(man.read_text(encoding='utf-8'))['count'] if man.exists() else '?'
            print(f'  {d.name}  ({n} files)')
        return 0
    if args.verify:
        return 0 if verify(args.verify) else 1
    if args.restore:
        if not args.only:
            raise SystemExit(
                '--restore requires --only <file.md> ... . A whole-dir restore is not\n'
                'offered: it would roll MEMORY.md back and revive retired state.')
        restore(args.restore, args.only)
        return 0
    create(args.label)
    return 0


if __name__ == '__main__':
    sys.exit(main())
