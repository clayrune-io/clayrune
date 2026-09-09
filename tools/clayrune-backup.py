#!/usr/bin/env python3
"""Clayrune backup / restore CLI — Phase 1 of docs/BACKUP_EXPORT_SPEC.md.

Drives mc/backup.py directly, the SAME module the server's /api/backup/*
routes call. Deliberately does not import server.py or start Flask: this is
the recovery path for when the server won't start, so it must not depend on
it (spec §6).

    python tools/clayrune-backup.py preview
    python tools/clayrune-backup.py preview --exclude transcripts media
    python tools/clayrune-backup.py create
    python tools/clayrune-backup.py create --exclude transcripts
    python tools/clayrune-backup.py list
    python tools/clayrune-backup.py restore <path.crbackup>
    python tools/clayrune-backup.py restore <path.crbackup> --only records
    python tools/clayrune-backup.py restore <path.crbackup> --sandbox-root <dir>

`--sandbox-root` is a VERIFICATION-ONLY escape hatch (never used by the
server's own restore route): it prefixes every destination path so a
round-trip can be proven into a scratch directory instead of overwriting the
live install. Phase 1 is same-machine, real-path restore by design (spec
§4.1/§8 — path remapping is Phase 2); the flag exists for testing that
design without touching production state while doing it.
"""
import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from mc import backup as bk  # noqa: E402


def _fmt_bytes(n: int) -> str:
    f = float(n)
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if f < 1024 or unit == 'TB':
            return f'{f:.1f}{unit}'
        f /= 1024
    return f'{f:.1f}TB'


def _categories_from_exclude(exclude: list[str] | None) -> dict | None:
    if not exclude:
        return None
    return {c: False for c in exclude}


def _print_preview(preview: dict):
    print(f"size preview — generated {preview['generated_at']}\n")
    for name in ('records', 'artifacts', 'media', 'transcripts'):
        c = preview['categories'][name]
        state = 'ON ' if c['enabled'] else 'off'
        print(f"  [{state}] {name:<12} {_fmt_bytes(c['bytes']):>10}  ({c['files']} files)")
    u = preview['categories']['unprotected']
    state = 'ON ' if u['enabled'] else 'off'
    print(f"  [{state}] {'unprotected':<12} {_fmt_bytes(u['bytes']):>10}  ({u['files']} files)")
    for d in u.get('directories', []):
        flag = '  ** OVER 1GB **' if d.get('over_1gb') else ''
        print(f"        - {d['kind']:<7} {d['project_id']:<28} {_fmt_bytes(d['bytes']):>10}"
             f"  ({d['files']} files){flag}")
    print(f"  [off] {'vault':<12}        —   not_available in Phase 1 — never silently omitted")
    print(f"\n  TOTAL: {_fmt_bytes(preview['total_bytes'])}")
    if preview['warnings']:
        print(f"\n  {len(preview['warnings'])} warning(s):")
        for w in preview['warnings'][:20]:
            print(f"    - [{w['kind']}] {w['path']} {w.get('detail', '')}")
        if len(preview['warnings']) > 20:
            print(f"    ... and {len(preview['warnings']) - 20} more")


def cmd_preview(args):
    categories = _categories_from_exclude(args.exclude)
    preview = bk.size_preview(categories)
    _print_preview(preview)
    return 0


def cmd_create(args):
    categories = _categories_from_exclude(args.exclude)
    preview = bk.size_preview(categories)
    _print_preview(preview)
    print(f"\nwriting archive to {args.dest or bk._paths()['backup_dir']} ...")
    result = bk.create_backup(categories=categories,
                              dest_dir=Path(args.dest) if args.dest else None,
                              label=args.label)
    print(f"\ncreated: {result['path']}")
    print(f"  {result['files_written']} files written, {len(result['warnings'])} warnings")
    if result['warnings']:
        for w in result['warnings'][:20]:
            print(f"    - [{w['kind']}] {w['path']} {w.get('detail', '')}")
    return 0


def cmd_list(_args):
    backups = bk.list_backups()
    if not backups:
        print('no backups yet')
        return 0
    for b in backups:
        if 'error' in b:
            print(f"  {b['path']}  ERROR: {b['error']}")
            continue
        cats = ', '.join(k for k, v in (b.get('categories') or {}).items() if v)
        print(f"  {b['path']}")
        print(f"    {b['created_at']}  {_fmt_bytes(b['bytes'])}  {b['file_count']} files"
             f"  ({b['warning_count']} warnings)  categories: {cats}")
    return 0


def cmd_restore(args):
    manifest = json.loads(__import__('zipfile').ZipFile(args.archive).read('manifest.json'))
    print(bk.announcement_for(manifest))
    if manifest.get('format', 0) > bk.FORMAT_VERSION:
        print('REFUSING: this backup was made by a newer Clayrune — update first', file=sys.stderr)
        return 1
    if args.sandbox_root:
        print(f"** SANDBOX MODE ** — writing under {args.sandbox_root}, NOT to real paths\n")
    try:
        report = bk.restore_backup(
            Path(args.archive), categories=args.only,
            dest_root=Path(args.sandbox_root) if args.sandbox_root else None)
    except bk.BackupIntegrityError as e:
        print(f'ABORTED before any write: {e}', file=sys.stderr)
        return 1
    print(f"\nrestored categories: {', '.join(report['restored_categories']) or '(none)'}")
    for cat, stats in report['per_category'].items():
        print(f"  {cat}: {stats['restored']} restored, {stats['conflicts']} conflicts, "
             f"{stats['refused']} refused")
        for err in stats['errors'][:10]:
            print(f"    ! {err}")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)

    p = sub.add_parser('preview', help='live size preview, writes nothing')
    p.add_argument('--exclude', nargs='+', metavar='CATEGORY',
                   help='categories to opt OUT of (default: everything ON)')
    p.set_defaults(fn=cmd_preview)

    p = sub.add_parser('create', help='print the preview, then write the archive')
    p.add_argument('--exclude', nargs='+', metavar='CATEGORY')
    p.add_argument('--dest', help='destination dir (default: ~/.clayrune/backups)')
    p.add_argument('--label', help='suffix appended to the filename')
    p.set_defaults(fn=cmd_create)

    p = sub.add_parser('list', help='list archives under ~/.clayrune/backups')
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser('restore', help='restore an archive (attended use only)')
    p.add_argument('archive')
    p.add_argument('--only', nargs='+', metavar='CATEGORY',
                   help='restore only these categories (default: everything the archive has)')
    p.add_argument('--sandbox-root',
                   help='VERIFICATION ONLY: prefix every destination under this dir '
                        'instead of the real absolute paths')
    p.set_defaults(fn=cmd_restore)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == '__main__':
    sys.exit(main())
