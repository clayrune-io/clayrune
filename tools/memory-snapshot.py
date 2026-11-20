#!/usr/bin/env python3
"""Snapshot and restore a project's memory corpus. Every rollback in the memory
plan depends on this file being correct.

    python tools/memory-snapshot.py --label pre-s3
    python tools/memory-snapshot.py --list
    python tools/memory-snapshot.py --verify <snapshot-dir>
    python tools/memory-snapshot.py --restore <snapshot-dir> --only foo.md bar.md

The snapshot code itself (copy, manifest, verify) lives in `mc/memory_snapshot.py`
so the server's daily job and this tool take the same snapshot. This file keeps
the command line and the restore guard. The label prefix `auto-` belongs to the
daily job, which prunes its own snapshots: `--label auto-...` is refused here so
a manual snapshot can never be one the job deletes.

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
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / 'memory-eval'))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import _harness  # noqa: E402
from mc import memory_snapshot as _snap  # noqa: E402

SNAP_ROOT = _snap.default_root()
PROTECTED = set(_snap.PROTECTED)
# Windows and default macOS filesystems are case-insensitive: `memory.md` IS MEMORY.md.
_PROTECTED_FOLDED = {n.casefold() for n in PROTECTED}


sha256 = _snap.sha256
ordered_files = _snap.ordered_files


def memory_dir():
    m, project = _harness.wire()
    return m._get_memory_path(project).parent


def create(label):
    if label.startswith(_snap.AUTO_LABEL_PREFIX):
        raise SystemExit(f'refusing --label {label}: the prefix {_snap.AUTO_LABEL_PREFIX!r} is '
                         'reserved for the daily job, which deletes its own old snapshots')
    try:
        dest, man = _snap.create_snapshot(memory_dir(), SNAP_ROOT, label)
    except _snap.SnapshotError as e:
        raise SystemExit(str(e))
    print(f'snapshot: {dest}')
    print(f'  {man["count"]} files, {sum(e["size"] for e in man["files"])} bytes')
    return dest


def verify(snap):
    snap = Path(snap)
    r = _snap.verify_snapshot(snap)
    for kind, name in r['problems']:
        print(f'  {"MISSING" if kind == "missing" else "SHA MISMATCH"} {name}')
    bad = sum(1 for kind, _ in r['problems'] if kind == 'mismatch')
    missing = len(r['problems']) - bad
    print(f'verify {snap.name}: {r["good"]}/{r["count"]} ok, {bad} mismatched, {missing} missing')
    return r['ok']


def restore(snap, only):
    """Scoped topic-file restore. See the module docstring for why this refuses
    to touch MEMORY.md and the archive."""
    snap = Path(snap)
    # A name must be a bare file name: `./MEMORY.md` or `sub/../MEMORY.md`
    # would pass an exact-string compare and still land on the live index.
    pathy = [n for n in only if Path(n).name != n or n in ('', '.', '..')]
    if pathy:
        raise SystemExit(f'refusing {", ".join(pathy)}: --only takes bare file names, no paths')
    bad = [n for n in only if n.casefold() in _PROTECTED_FOLDED]
    if bad:
        raise SystemExit(
            f'refusing to restore {", ".join(bad)}. Restoring these rolls back every\n'
            'watermark and supersede since the snapshot, silently reviving retired\n'
            'state — that is not a rollback. Do it by hand after reading the diff.')
    dst = memory_dir()
    man = json.loads((snap / 'manifest.json').read_text(encoding='utf-8'))
    hashes = {e['name']: e['sha256'] for e in man['files']}
    done = []
    # The daily job does not copy a memory directory while a restore writes it.
    with _snap.restoring(f'memory-snapshot --restore {snap.name}'):
        for name in only:
            src = snap / 'memory' / name
            if not src.exists():
                raise SystemExit(f'{name} is not in {snap}')
            if name in hashes and sha256(src) != hashes[name]:
                raise SystemExit(f'{name} in {snap} no longer matches its recorded hash; '
                                 'run --verify. Nothing restored.')
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
