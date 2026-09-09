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

    python tools/clayrune-backup.py export-project <project_id> --vault omit
    python tools/clayrune-backup.py export-project <project_id> --vault include --passphrase <p>
    python tools/clayrune-backup.py import <path.crbackup>
    python tools/clayrune-backup.py import <path.crbackup> --apply --new-path <dir>
    python tools/clayrune-backup.py import <path.crbackup> --apply \
        --project-resolution replace --schedule-resolution replace

`--sandbox-root` is a VERIFICATION-ONLY escape hatch (never used by the
server's own restore route): it prefixes every destination path so a
round-trip can be proven into a scratch directory instead of overwriting the
live install. Phase 1 is same-machine, real-path restore by design (spec
§4.1/§8 — path remapping is Phase 2); the flag exists for testing that
design without touching production state while doing it.

`export-project`/`import` are Phase 2 (spec §7): per-project portability,
with real path remapping (§8) — no sandbox flag needed there because the
remap itself already writes to wherever `--new-path` says, not to the live
absolute paths, unless you point it back at them on purpose. `import`
defaults to a dry run (the §4.4 collision report); pass `--apply` to commit.
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


def cmd_export_project(args):
    vault_arg = {'include': True, 'omit': False, None: None}[args.vault]
    categories = _categories_from_exclude(args.exclude)
    try:
        result = bk.export_project(
            args.project_id, categories=categories, vault=vault_arg,
            vault_passphrase=args.passphrase, label=args.label,
            dest_dir=Path(args.dest) if args.dest else None)
    except bk.BackupError as e:
        print(f'ERROR: {e}', file=sys.stderr)
        return 1
    print(f"exported project '{args.project_id}' -> {result['path']}")
    print(f"  {result['files_written']} files written, {len(result['warnings'])} warnings")
    print(f"  vault: {result['manifest']['vault_status']}")
    for w in result['warnings'][:20]:
        print(f"    - [{w['kind']}] {w['path']} {w.get('detail', '')}")
    print('\nrepo checklist (this archive does not carry the repo itself):')
    for line in result['repo_checklist']:
        print(f'  - {line}')
    return 0


def _print_dry_run(report: dict):
    print(report['announcement'])
    print(f"\narchive kind: {report['manifest']['kind']}   "
         f"vault: {report['vault_status']}   contains_secrets: {report['contains_secrets']}")
    for cls, items in report['collisions'].items():
        if not items:
            continue
        print(f"\n{cls} collisions:")
        for it in items:
            marker = 'LOCAL COPY EXISTS' if it.get('exists_locally') else 'no local collision'
            label = it.get('id') or it.get('name')
            print(f"  - {label}  [{marker}]  options: {', '.join(it['options'])}"
                 f" (default: {it['default']})")
            if it.get('note'):
                print(f"      note: {it['note']}")
    for pid, required in report['path_remap_required'].items():
        if required:
            print(f"\nproject '{pid}': recorded project_path does not exist on this machine — "
                 f"--new-path is required to apply")
    print('\nrepo checklist:')
    for line in report['repo_checklist']:
        print(f'  - {line}')


def cmd_import(args):
    if not args.apply:
        try:
            report = bk.import_dry_run(Path(args.archive))
        except bk.BackupError as e:
            print(f'ERROR: {e}', file=sys.stderr)
            return 1
        print('** DRY RUN ** — nothing written. Pass --apply to commit.\n')
        _print_dry_run(report)
        return 0

    try:
        report = bk.import_project(
            Path(args.archive),
            project_resolution=args.project_resolution,
            schedule_resolution=args.schedule_resolution,
            new_project_path=args.new_path,
            vault_passphrase=args.passphrase)
    except bk.BackupIntegrityError as e:
        print(f'ABORTED before any write: {e}', file=sys.stderr)
        return 1
    except bk.BackupError as e:
        print(f'ERROR: {e}', file=sys.stderr)
        return 1

    if report.get('status') == 'skipped':
        print(f"skipped: {report['reason']}")
        return 0

    print(f"imported '{report['old_project_id']}' -> '{report['final_project_id']}'")
    print(f"  project_path: {report['old_project_path']} -> {report['new_project_path']}"
         f"  (remapped: {report['path_remapped']})")
    for cat, n in report['restored'].items():
        print(f"  {cat}: {n} file(s) restored")
    print(f"  schedules: {report['schedules_imported']} imported, "
         f"{report['schedules_skipped']} skipped (all arrive disabled)")
    print(f"  vault: {report['vault']}")
    if report.get('pre_replace_copy'):
        print(f"  pre-replace safety copy: {report['pre_replace_copy']}")
    if report['warnings']:
        print(f"  {len(report['warnings'])} warning(s):")
        for w in report['warnings'][:20]:
            print(f"    - {w}")
    print('\nrepo checklist:')
    for line in report['repo_checklist']:
        print(f'  - {line}')
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

    p = sub.add_parser('export-project', help='export one project (spec §4.1) for migration')
    p.add_argument('project_id')
    p.add_argument('--vault', choices=['include', 'omit'], default=None,
                   help='the vault question — unanswered refuses to export (spec §4.2)')
    p.add_argument('--passphrase', help='required with --vault include')
    p.add_argument('--exclude', nargs='+', metavar='CATEGORY')
    p.add_argument('--dest', help='destination dir (default: ~/.clayrune/backups)')
    p.add_argument('--label')
    p.set_defaults(fn=cmd_export_project)

    p = sub.add_parser('import', help='import a project export (dry run unless --apply)')
    p.add_argument('archive')
    p.add_argument('--apply', action='store_true', help='commit the import (default: dry run)')
    p.add_argument('--project-resolution', choices=['skip', 'replace', 'import-as-copy'],
                   default='skip')
    p.add_argument('--schedule-resolution', choices=['skip', 'replace'], default='skip')
    p.add_argument('--new-path', dest='new_path',
                   help='remap project_path to this directory (spec §8)')
    p.add_argument('--passphrase', help='vault passphrase, if the archive contains secrets')
    p.set_defaults(fn=cmd_import)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == '__main__':
    sys.exit(main())
