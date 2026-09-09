"""Clayrune backup / restore — Phase 1 + Phase 2 of docs/BACKUP_EXPORT_SPEC.md.

Phase 1: full-install backup + restore, complete by default, same-machine
semantics only. Phase 2 (below the Phase 1 code, "Phase 2" banner comment):
per-project export/import with a dry-run collision report (§4.4), the vault
re-encrypt category (§4.2, via ``mc.secrets_store.export_all_for_backup`` /
``import_all_from_backup`` — no plaintext ever passes through this module),
and the path-remap machinery §8 exists for: a project's memory vault
directory *is* its encoded absolute ``project_path``, so importing onto a
machine where that path differs must rewrite the project record, re-encode
and relocate the vault dir, and remap every other project-scoped archive
member (records, artifacts, transcripts, unprotected) to the new path/id
consistently — a missed one silently orphans that surface, not an error.
Manifest format 1, including the ``categories`` field (spec §4.5) so it never
needed a format bump to grow into this.

Importable WITHOUT server.py (spec §6, same isolation rule as distiller.py) —
every path is resolved independently here, the way ``mc/db.py`` and
``tools/backlog-key-backfill.py`` already do for the project-records dir, so
``tools/clayrune-backup.py`` can restore a broken install with no server
running. Nothing in this module imports ``server``.

Categories (spec §4.6, all default ON — Ron's "everything unless the user
explicitly unticks" ruling):
  records      — project records + sidecars, config/settings/schedules,
                 hiveminds, data/skills, memory vaults, characters,
                 ~/.claude/skills
  artifacts    — docs/_journal per project, data/media, data/reply_archive,
                 data/maintenance_reports, data/projects/<id>/ workspaces
  media        — data/uploads
  transcripts  — ~/.claude/projects/**/*.jsonl + ~/.codex/sessions
  unprotected  — §4.8: untracked-not-ignored files in git checkouts (10MB
                 per-file ceiling) + whole non-git registered project dirs
                 (junk-dir names excluded), per-directory line items
  vault        — NOT AVAILABLE in Phase 1 (Phase 2 does the re-encrypt
                 machinery, spec §4.2/§4.9). Always recorded, never silently
                 omitted (spec §4.5 "categories is load-bearing").

DATA_DIR discipline (CLAUDE.md, spec §5): nothing this module writes lands
under ``data/projects/`` except restored project records + their already
sidecar-suffix-excluded files — every restore write into DATA_DIR is checked
against ``EXCLUDED_SIDECAR_SUFFIXES`` first, and a stray name is refused, not
written. Archives themselves live under ``~/.clayrune/backups/`` (§5),
alongside restore points and the secrets vault — never under the repo, so
there is nothing here for git to see and nothing to gitignore.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from mc.blueprints.project_routes import EXCLUDED_SIDECAR_SUFFIXES
from mc import secrets_store as _secrets_store
from mc.core import _log
from mc.secrets_store import clayrune_home

# ── Path resolution — standalone, mirrors server._resolve_dirs() dev-mode
#    shape (MC_DATA_DIR wins; else the repo root, two levels up from this
#    file: mc/backup.py -> mc/ -> repo root). See mc/db.py._resolve_default_path
#    and tools/backlog-key-backfill.py._resolve_data_dir for the same idiom. ──

REPO_ROOT = Path(__file__).resolve().parent.parent


def _data_root() -> Path:
    override = os.environ.get('MC_DATA_DIR')
    return Path(override) if override else REPO_ROOT


def _paths():
    """Recomputed on every call (not cached at import time) so tests can flip
    MC_DATA_DIR / CLAYRUNE_HOME / HOME via monkeypatch and see it take effect,
    the same reason mc/db.py's resolver is a function, not a module constant."""
    root = _data_root()
    data = root / 'data'
    home = Path(os.environ.get('USERPROFILE') or os.environ.get('HOME') or str(Path.home()))
    claude_dir = home / '.claude'
    return {
        'root': root,
        'data': data,
        'data_dir': data / 'projects',          # == DATA_DIR elsewhere
        'uploads_dir': data / 'uploads',
        'config_json': root / 'config.json',     # server.CONFIG_PATH's real dev-mode location
        'settings_json': data / 'settings.json',
        'grid_layout_json': data / 'grid_layout.json',
        'schedules_json': data / 'schedules.json',
        'agent_labels_json': data / 'agent_labels.json',
        'session_labels_json': data / 'session_labels.json',
        'notifications_json': data / 'notifications.json',
        'shared_rules_md': data / 'SHARED_RULES.md',
        'mc_builtin_mcps': data / 'mc_builtin_mcps_global.json',
        'hiveminds_dir': data / 'hiveminds',
        'skills_dir': data / 'skills',
        'mcp_dir': data / 'mcp',
        'media_dir': data / 'media',
        'reply_archive_dir': data / 'reply_archive',
        'maintenance_reports_dir': data / 'maintenance_reports',
        'memory_fallback_dir': data / 'memory',   # projects with no project_path
        'claude_dir': claude_dir,
        'claude_projects_dir': claude_dir / 'projects',
        'claude_agents_dir': claude_dir / 'agents',
        'claude_skills_dir': claude_dir / 'skills',
        'codex_sessions_dir': home / '.codex' / 'sessions',
        'backup_dir': clayrune_home() / 'backups',
        'incoming_dir': clayrune_home() / 'backups' / '_incoming',
    }


# Spec §4.8: files git already tracks/ignores are git's problem, not ours;
# regenerable tool output inside a NON-git registered project dir is the same
# class .gitignore filters for a checkout, so the same names are excluded.
JUNK_DIR_NAMES = {'node_modules', '.venv', 'venv', '__pycache__', '.cache', 'dist', 'build'}

# Spec §4.8(a): a stray dataset or video in an otherwise-tracked checkout must
# not silently balloon the default archive. Does NOT apply inside sub-class
# (b) non-git dirs — spec is explicit that a ceiling there would gut the very
# content the line item exists to carry.
CHECKOUT_FILE_CEILING = 10 * 1024 * 1024

DEFAULT_CATEGORIES = ('records', 'artifacts', 'media', 'transcripts', 'unprotected')

FORMAT_VERSION = 1


class BackupError(Exception):
    pass


class BackupFormatError(BackupError):
    """Archive format is newer than this reader supports (spec §4.5)."""


class BackupIntegrityError(BackupError):
    """A file's sha256 didn't match before any write happened (spec §4.5)."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _clayrune_version() -> str:
    """Best-effort ``git describe`` — advisory only, never fails the backup."""
    try:
        out = subprocess.run(
            ['git', 'describe', '--tags', '--always', '--dirty'],
            cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=5)
        v = out.stdout.strip()
        if v:
            return v
    except Exception:
        pass
    return 'unknown'


# ── Registered projects ───────────────────────────────────────────────────────

def _iter_registered_projects(data_dir: Path) -> list[dict]:
    """Every real project record — mirrors load_projects()' sidecar exclusion.
    Standalone (no wire()) by design: this must work with no server running."""
    out = []
    if not data_dir.is_dir():
        return out
    for f in sorted(data_dir.glob('*.json')):
        if f.name.endswith(EXCLUDED_SIDECAR_SUFFIXES):
            continue
        try:
            rec = json.loads(f.read_text(encoding='utf-8'))
        except Exception:
            continue
        if not isinstance(rec, dict):
            continue
        rec.setdefault('id', f.stem)
        out.append(rec)
    return out


def _is_git_repo(path: Path) -> bool:
    return (path / '.git').exists()


def _git_info(path: Path) -> tuple[Optional[str], Optional[str]]:
    """(remote_url, head_sha), best-effort, never raises. Advisory pointer only
    (spec §4.1) — Phase 1 doesn't act on it, but format 1 carries it forever."""
    def _run(args):
        try:
            r = subprocess.run(['git', '-C', str(path)] + args,
                               capture_output=True, text=True, timeout=5)
            return r.stdout.strip() if r.returncode == 0 else None
        except Exception:
            return None
    if not _is_git_repo(path):
        return None, None
    return _run(['config', '--get', 'remote.origin.url']), _run(['rev-parse', 'HEAD'])


def _untracked_not_ignored(path: Path) -> Optional[list[str]]:
    try:
        r = subprocess.run(
            ['git', '-C', str(path), 'ls-files', '--others', '--exclude-standard'],
            capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            return None
        return [ln for ln in r.stdout.splitlines() if ln]
    except Exception:
        return None


def _encode_project_path(project_path: str) -> Optional[str]:
    """Same encoding as mc.memory._encode_project_path, reimplemented here
    (rather than imported) so this module never depends on mc.memory's
    wire()-injected globals — importing mc.memory without calling its wire()
    first leaves CLAUDE_HOME as None, and this module must work standalone."""
    if not project_path:
        return None
    try:
        resolved = str(Path(project_path).resolve())
    except Exception:
        return None
    return resolved.replace(':', '-').replace('\\', '-').replace('/', '-')


def _memory_dir_for(project: dict, paths: dict) -> Optional[Path]:
    """The directory holding a project's memory vault (MEMORY.md, archive,
    topic files, position files, …) — native Claude location preferred,
    falling back to nothing (the MC fallback is a single flat file, handled
    separately in _memory_files_for)."""
    pp = project.get('project_path', '')
    encoded = _encode_project_path(pp)
    if not encoded:
        return None
    candidates = [paths['claude_projects_dir'] / encoded / 'memory']
    alt = encoded.replace('_', '-')
    if alt != encoded:
        candidates.append(paths['claude_projects_dir'] / alt / 'memory')
    for d in candidates:
        if d.is_dir():
            return d
    return None


def _memory_files_for(project: dict, paths: dict) -> list[Path]:
    """Ordered file list for one project's memory corpus. Copy order is
    load-bearing (spec §2/§6, tools/memory-snapshot.py precedent): MEMORY.md
    first, then the archive, then everything else, so a concurrent
    floor-eviction duplicates a line at worst instead of losing one."""
    d = _memory_dir_for(project, paths)
    if d is not None:
        files = [f for f in d.rglob('*') if f.is_file()]
        head = [f for f in files if f.name == 'MEMORY.md']
        arch = [f for f in files if f.name == 'MEMORY_ARCHIVE.md']
        rest = [f for f in files if f.name not in ('MEMORY.md', 'MEMORY_ARCHIVE.md')]
        return head + arch + sorted(rest)
    # MC fallback location: one flat file per project (no topic files).
    fallback = paths['memory_fallback_dir'] / f"{project['id']}.md"
    return [fallback] if fallback.is_file() else []


# ── File-entry model ──────────────────────────────────────────────────────────

@dataclass
class _Entry:
    dest: Path            # absolute path this file lives at / restores to
    category: str
    arc_prefix: str        # zip member = f"{arc_prefix}/{relpath}"
    relpath: str
    reserialize_json: bool = False


@dataclass
class _Warning:
    kind: str               # 'oversize_skipped' | 'junkdir_skipped' | 'unreadable' | 'vanished' | 'refused'
    path: str
    detail: str = ''
    bytes: int = 0


def _dir_size(root: Path) -> int:
    total = 0
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            try:
                total += (Path(dirpath) / name).stat().st_size
            except OSError:
                continue
    return total


def _walk_dir(root: Path, category: str, arc_prefix: str,
              skip_dirnames: frozenset = frozenset()) -> list[_Entry]:
    entries = []
    if not root.is_dir():
        return entries
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip_dirnames]
        for name in filenames:
            p = Path(dirpath) / name
            rel = p.relative_to(root).as_posix()
            entries.append(_Entry(dest=p, category=category, arc_prefix=arc_prefix, relpath=rel))
    return entries


# ── Per-category enumeration (shared by size-preview and create) ────────────

def _enumerate_records(paths: dict, projects: list[dict]) -> tuple[list[_Entry], list[_Warning]]:
    entries: list[_Entry] = []
    warnings: list[_Warning] = []
    dd = paths['data_dir']
    if dd.is_dir():
        for f in sorted(dd.iterdir()):
            if f.is_file() and f.suffix in ('.json', '.jsonl'):
                entries.append(_Entry(dest=f, category='records', arc_prefix='records/projects',
                                      relpath=f.name, reserialize_json=(f.suffix == '.json')))
    for key, arc in (
        ('config_json', 'records/config'), ('settings_json', 'records/config'),
        ('grid_layout_json', 'records/config'), ('schedules_json', 'records/config'),
        ('agent_labels_json', 'records/config'), ('session_labels_json', 'records/config'),
        ('notifications_json', 'records/config'), ('shared_rules_md', 'records/config'),
        ('mc_builtin_mcps', 'records/config'),
    ):
        f = paths[key]
        if f.is_file():
            entries.append(_Entry(dest=f, category='records', arc_prefix=arc, relpath=f.name,
                                  reserialize_json=(f.suffix == '.json')))
    for key, arc in (('hiveminds_dir', 'records/hiveminds'), ('skills_dir', 'records/data-skills'),
                     ('mcp_dir', 'records/mcp')):
        entries += _walk_dir(paths[key], 'records', arc)
    for proj in projects:
        for f in _memory_files_for(proj, paths):
            d = _memory_dir_for(proj, paths)
            base = d if d is not None else paths['memory_fallback_dir']
            try:
                rel = f.relative_to(base).as_posix()
            except ValueError:
                rel = f.name
            entries.append(_Entry(dest=f, category='records',
                                  arc_prefix=f"records/memory/{proj['id']}", relpath=rel))
    entries += _walk_dir(paths['claude_agents_dir'], 'records', 'records/characters')
    entries += _walk_dir(paths['claude_skills_dir'], 'records', 'records/home-skills')
    return entries, warnings


def _enumerate_artifacts(paths: dict, projects: list[dict]) -> tuple[list[_Entry], list[_Warning]]:
    entries: list[_Entry] = []
    warnings: list[_Warning] = []
    for proj in projects:
        pp = proj.get('project_path', '')
        if pp:
            journal = Path(pp) / 'docs' / '_journal'
            entries += _walk_dir(journal, 'artifacts', f"artifacts/journal/{proj['id']}")
        ws = paths['data_dir'] / proj['id']
        if ws.is_dir():
            entries += _walk_dir(ws, 'artifacts', f"artifacts/workspace/{proj['id']}")
    entries += _walk_dir(paths['media_dir'], 'artifacts', 'artifacts/media')
    entries += _walk_dir(paths['reply_archive_dir'], 'artifacts', 'artifacts/reply_archive')
    entries += _walk_dir(paths['maintenance_reports_dir'], 'artifacts', 'artifacts/maintenance_reports')
    return entries, warnings


def _enumerate_media(paths: dict, _projects: list[dict]) -> tuple[list[_Entry], list[_Warning]]:
    return _walk_dir(paths['uploads_dir'], 'media', 'media/uploads'), []


def _enumerate_transcripts(paths: dict, _projects: list[dict]) -> tuple[list[_Entry], list[_Warning]]:
    entries: list[_Entry] = []
    root = paths['claude_projects_dir']
    if root.is_dir():
        for f in root.rglob('*.jsonl'):
            if f.is_file():
                rel = f.relative_to(root).as_posix()
                entries.append(_Entry(dest=f, category='transcripts',
                                      arc_prefix='transcripts/claude', relpath=rel))
    entries += _walk_dir(paths['codex_sessions_dir'], 'transcripts', 'transcripts/codex')
    return entries, []


def _enumerate_unprotected(paths: dict, projects: list[dict],
                           exclude_projects: frozenset = frozenset()
                           ) -> tuple[list[_Entry], list[_Warning], list[dict]]:
    """Returns (entries, warnings, directory_line_items) — spec §4.8. Each
    registered project with a real project_path gets exactly one line item,
    either a bounded 'checkout' sweep or an unbounded 'nongit' whole-dir sweep."""
    entries: list[_Entry] = []
    warnings: list[_Warning] = []
    lines: list[dict] = []
    for proj in projects:
        pid = proj['id']
        pp = proj.get('project_path', '')
        if not pp or pid in exclude_projects:
            continue
        p = Path(pp)
        if not p.is_dir():
            warnings.append(_Warning('vanished', pp, f"project {pid}: project_path does not exist"))
            continue
        # Self-reference guard: if this checkout IS the install whose data/
        # dir we're already archiving whole under records/artifacts/media,
        # its own data/ subtree must not ALSO be swept as "unprotected work".
        # Found for real on this install: data/agent_labels.json isn't
        # gitignored, so it showed up in git ls-files too — 359 of
        # mission_control's 453 "untracked" files were under data/, and one
        # that changed between the two sweep passes produced a spurious
        # restore conflict. Skip anything under the resolved data dir here.
        try:
            own_data_rel = paths['data'].resolve().relative_to(p.resolve()).as_posix()
        except (ValueError, OSError):
            own_data_rel = None
        if _is_git_repo(p):
            names = _untracked_not_ignored(p)
            if names is None:
                warnings.append(_Warning('unreadable', pp, f"project {pid}: git ls-files failed"))
                continue
            if own_data_rel is not None:
                skip_bytes = 0
                skip_count = 0
                kept = []
                for rel in names:
                    if rel == own_data_rel or rel.startswith(own_data_rel + '/'):
                        skip_count += 1
                        try:
                            skip_bytes += (p / rel).stat().st_size
                        except OSError:
                            pass
                        continue
                    kept.append(rel)
                if skip_count:
                    warnings.append(_Warning('own_data_dir_skipped', str(paths['data']),
                                             f"project {pid}: already covered by records/artifacts/media, "
                                             f"not swept again as unprotected ({skip_count} files)",
                                             bytes=skip_bytes))
                names = kept
            line_bytes = line_files = 0
            for rel in names:
                f = p / rel
                try:
                    size = f.stat().st_size
                except OSError:
                    warnings.append(_Warning('vanished', str(f), f"project {pid}: disappeared during sweep"))
                    continue
                if size > CHECKOUT_FILE_CEILING:
                    warnings.append(_Warning('oversize_skipped', str(f),
                                             f"project {pid}: over the 10MB checkout ceiling", bytes=size))
                    continue
                entries.append(_Entry(dest=f, category='unprotected',
                                      arc_prefix=f"unprotected/checkout/{pid}", relpath=rel))
                line_bytes += size
                line_files += 1
            lines.append({'project_id': pid, 'kind': 'checkout', 'path': str(p),
                          'bytes': line_bytes, 'files': line_files})
        else:
            before = len(entries)
            line_bytes = 0
            for dirpath, dirnames, filenames in os.walk(p):
                junk = [d for d in dirnames if d in JUNK_DIR_NAMES]
                for jd in junk:
                    jpath = Path(dirpath) / jd
                    jbytes = _dir_size(jpath)
                    warnings.append(_Warning('junkdir_skipped', str(jpath),
                                             f"project {pid}: regenerable tool output, excluded", bytes=jbytes))
                dirnames[:] = [d for d in dirnames if d not in JUNK_DIR_NAMES]
                for name in filenames:
                    f = Path(dirpath) / name
                    try:
                        size = f.stat().st_size
                    except OSError:
                        warnings.append(_Warning('vanished', str(f), f"project {pid}: disappeared during sweep"))
                        continue
                    rel = f.relative_to(p).as_posix()
                    entries.append(_Entry(dest=f, category='unprotected',
                                          arc_prefix=f"unprotected/nongit/{pid}", relpath=rel))
                    line_bytes += size
            lines.append({'project_id': pid, 'kind': 'nongit', 'path': str(p),
                          'bytes': line_bytes, 'files': len(entries) - before,
                          'over_1gb': line_bytes > 1024 ** 3})
    return entries, warnings, lines


_ENUMERATORS: dict[str, Callable] = {
    'records': _enumerate_records,
    'artifacts': _enumerate_artifacts,
    'media': _enumerate_media,
    'transcripts': _enumerate_transcripts,
}


def _normalize_categories(categories: Optional[dict]) -> dict:
    """No categories object => full default, everything ON (spec §6). An
    explicit object opts OUT per category only — a category missing from the
    dict stays ON, matching 'the checklist is opt-out' (§4.6)."""
    out = {c: True for c in DEFAULT_CATEGORIES}
    if categories:
        for k, v in categories.items():
            if k in out:
                out[k] = v
    return out


def _unprotected_exclusions(categories: dict) -> frozenset:
    u = categories.get('unprotected')
    if isinstance(u, dict):
        return frozenset(u.get('exclude_projects') or [])
    return frozenset()


def _unprotected_enabled(categories: dict) -> bool:
    u = categories.get('unprotected')
    if isinstance(u, dict):
        return True  # dict form = "on, minus these directories"
    return bool(u)


# ── Size preview (spec §4.6 — GET /api/backup/size-preview) ─────────────────

def size_preview(categories: Optional[dict] = None) -> dict:
    cats = _normalize_categories(categories)
    paths = _paths()
    projects = _iter_registered_projects(paths['data_dir'])
    result: dict[str, Any] = {'generated_at': _now_iso(), 'categories': {}, 'warnings': [], 'total_bytes': 0}

    for name in ('records', 'artifacts', 'media', 'transcripts'):
        if not cats.get(name):
            result['categories'][name] = {'enabled': False, 'bytes': 0, 'files': 0}
            continue
        entries, warns = _ENUMERATORS[name](paths, projects)
        total = 0
        n = 0
        for e in entries:
            try:
                total += e.dest.stat().st_size
                n += 1
            except OSError:
                warns.append(_Warning('vanished', str(e.dest), 'disappeared during preview'))
        result['categories'][name] = {'enabled': True, 'bytes': total, 'files': n}
        result['warnings'] += [w.__dict__ for w in warns]
        result['total_bytes'] += total

    if _unprotected_enabled(cats):
        _, warns, lines = _enumerate_unprotected(paths, projects, _unprotected_exclusions(cats))
        total = sum(l['bytes'] for l in lines)
        result['categories']['unprotected'] = {
            'enabled': True, 'bytes': total,
            'files': sum(l['files'] for l in lines), 'directories': lines,
        }
        result['warnings'] += [w.__dict__ for w in warns]
        result['total_bytes'] += total
    else:
        result['categories']['unprotected'] = {'enabled': False, 'bytes': 0, 'files': 0, 'directories': []}

    result['categories']['vault'] = {'status': 'not_available'}
    return result


# ── Create (spec §6 — POST /api/backup/create) ───────────────────────────────

def _filename_for(cats: dict, unprotected_excl: frozenset) -> str:
    date = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    off = [c for c in DEFAULT_CATEGORIES if not cats.get(c)]
    if unprotected_excl and cats.get('unprotected'):
        off = off + ['unprotected-partial']
    if not off:
        return f'clayrune-{date}-full.crbackup'
    return f"clayrune-{date}-full-minus-{'-'.join(off)}.crbackup"


def _read_and_maybe_reserialize(entry: _Entry) -> bytes:
    raw = entry.dest.read_bytes()
    if not entry.reserialize_json:
        return raw
    # Consistency-without-stopping-the-server (spec §6): parse, then
    # re-serialize, so a concurrent half-write surfaces as a JSONDecodeError
    # here rather than as silently archived garbage. Retry once (the write
    # may have completed a millisecond later) before giving up on this file.
    try:
        obj = json.loads(raw.decode('utf-8'))
    except Exception:
        raw = entry.dest.read_bytes()  # one retry — the write may have finished a millisecond later
        obj = json.loads(raw.decode('utf-8'))  # raises on a second failure — caller records it as unreadable
    # save_project()/write_text() write in TEXT mode, which on Windows
    # translates '\n' -> '\r\n' — every project record on this platform is
    # CRLF. json.dumps always emits bare '\n'; encoding that as-is would
    # silently flip every JSON state file's line endings on its way into the
    # archive — caught by diffing a real restored file against the live one
    # (looked like "every line changed" though the content was identical).
    # Match the newline style the file was already using rather than
    # imposing this platform's default, so a restore reproduces it exactly.
    newline = '\r\n' if b'\r\n' in raw else '\n'
    text = json.dumps(obj, indent=2, ensure_ascii=False)
    if newline != '\n':
        text = text.replace('\n', newline)
    return text.encode('utf-8')


def create_backup(categories: Optional[dict] = None, dest_dir: Optional[Path] = None,
                  label: Optional[str] = None) -> dict:
    cats = _normalize_categories(categories)
    if not any(cats.get(c) for c in DEFAULT_CATEGORIES):
        raise BackupError('refusing to create an empty archive — untick fewer categories')

    paths = _paths()
    projects = _iter_registered_projects(paths['data_dir'])
    unprotected_excl = _unprotected_exclusions(cats)

    all_entries: list[_Entry] = []
    warnings: list[_Warning] = []
    for name in ('records', 'artifacts', 'media', 'transcripts'):
        if cats.get(name):
            entries, warns = _ENUMERATORS[name](paths, projects)
            all_entries += entries
            warnings += warns
    if _unprotected_enabled(cats):
        entries, warns, _lines = _enumerate_unprotected(paths, projects, unprotected_excl)
        all_entries += entries
        warnings += warns

    dest_dir = Path(dest_dir) if dest_dir else paths['backup_dir']
    dest_dir.mkdir(parents=True, exist_ok=True)
    fname = _filename_for(cats, unprotected_excl)
    if label:
        fname = fname.replace('.crbackup', f'-{label}.crbackup')
    final_path = dest_dir / fname
    tmp_path = dest_dir / f'.{fname}.partial'

    files_manifest: dict[str, dict] = {}
    written = 0
    with zipfile.ZipFile(tmp_path, 'w', compression=zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
        for e in all_entries:
            arcname = f"{e.arc_prefix}/{e.relpath}"
            try:
                data = _read_and_maybe_reserialize(e)
            except FileNotFoundError:
                warnings.append(_Warning('vanished', str(e.dest), 'gone before it could be read'))
                continue
            except (OSError, PermissionError) as exc:
                # File-by-file, never copytree (spec §6, the `nul` incident):
                # one locked/unreadable file skips-and-logs, never aborts.
                warnings.append(_Warning('unreadable', str(e.dest), f'{exc.__class__.__name__}: {exc}'))
                continue
            except Exception as exc:  # malformed JSON that never recovered on retry
                warnings.append(_Warning('unreadable', str(e.dest), f'parse failed: {exc}'))
                continue
            zf.writestr(arcname, data)
            files_manifest[arcname] = {
                'sha256': _sha256_bytes(data), 'bytes': len(data),
                'dest': str(e.dest), 'category': e.category,
            }
            written += 1

        manifest = {
            'format': FORMAT_VERSION,
            'created_at': _now_iso(),
            'clayrune_version': _clayrune_version(),
            'kind': 'full',
            'categories': {
                'records': bool(cats.get('records')), 'artifacts': bool(cats.get('artifacts')),
                'media': bool(cats.get('media')), 'transcripts': bool(cats.get('transcripts')),
                'unprotected': _unprotected_enabled(cats), 'vault': False,
            },
            'unprotected_excluded_projects': sorted(unprotected_excl),
            'vault_status': 'not_available',
            'contains_secrets': False,
            'projects': [
                {'id': p['id'], 'project_path': p.get('project_path', ''),
                'git_remote': (g := _git_info(Path(p.get('project_path', '') or '.')))[0],
                'git_head': g[1]}
                for p in projects
            ],
            'files': files_manifest,
            'warnings': [w.__dict__ for w in warnings],
        }
        zf.writestr('manifest.json', json.dumps(manifest, indent=2, ensure_ascii=False))

    os.replace(tmp_path, final_path)  # atomic: a half-written archive never appears as a finished one
    return {'path': str(final_path), 'manifest': manifest, 'files_written': written,
           'warnings': manifest['warnings']}


# ── List ──────────────────────────────────────────────────────────────────────

def list_backups(dest_dir: Optional[Path] = None) -> list[dict]:
    paths = _paths()
    d = Path(dest_dir) if dest_dir else paths['backup_dir']
    out = []
    if not d.is_dir():
        return out
    for f in sorted(d.glob('*.crbackup')):
        try:
            with zipfile.ZipFile(f) as zf:
                manifest = json.loads(zf.read('manifest.json'))
        except Exception as exc:
            out.append({'path': str(f), 'error': f'unreadable: {exc}'})
            continue
        out.append({
            'path': str(f), 'bytes': f.stat().st_size,
            'created_at': manifest.get('created_at'), 'kind': manifest.get('kind'),
            'format': manifest.get('format'), 'categories': manifest.get('categories'),
            'file_count': len(manifest.get('files', {})),
            'warning_count': len(manifest.get('warnings', [])),
        })
    return sorted(out, key=lambda r: r.get('created_at') or '', reverse=True)


# ── Restore (spec §4.7/§4.8/§6 — POST /api/backup/restore) ──────────────────

def _absent_categories(manifest: dict) -> list[str]:
    cats = manifest.get('categories', {})
    return [c for c in ('records', 'artifacts', 'media', 'transcripts', 'unprotected', 'vault')
           if not cats.get(c)]


def announcement_for(manifest: dict) -> str:
    absent = [c for c in _absent_categories(manifest) if c != 'vault']
    vault = manifest.get('vault_status', 'not_available')
    parts = []
    if absent:
        parts.append('This archive has no ' + ' and no '.join(absent) + '.')
    else:
        parts.append('This archive contains every category.')
    parts.append(f'The vault question was answered: {vault}.')
    return ' '.join(parts)


def _remap(dest: Path, dest_root: Optional[Path]) -> Path:
    """Sandbox hook for verification only (never used by the API/CLI's real
    restore path — spec §4.1/§8 explicitly scope Phase 1 to real absolute
    paths). Lets a round-trip test prove restore fidelity into a temp
    directory instead of overwriting the live install."""
    if dest_root is None:
        return dest
    drive, tail = os.path.splitdrive(str(dest))
    tail = tail.lstrip('\\/')
    return dest_root / (drive.rstrip(':') or 'noDrive') / tail


def _validate_records_write(dest: Path, paths: dict, manifest: dict) -> Optional[str]:
    """Refuse any restore write into DATA_DIR whose name isn't a known
    project id or an EXCLUDED_SIDECAR_SUFFIXES sidecar (spec §5) — the tamper
    defense for a hand-edited archive. Returns an error string, or None if OK."""
    try:
        dd = paths['data_dir'].resolve()
        if dest.resolve().parent != dd:
            return None  # not a DATA_DIR write at all
    except OSError:
        return None
    name = dest.name
    known_ids = {p['id'] for p in manifest.get('projects', [])}
    if name.endswith(EXCLUDED_SIDECAR_SUFFIXES) or name.endswith('.jsonl'):
        return None
    if name.endswith('.json') and name[:-5] in known_ids:
        return None
    return f'refused: {name} is not a known project id or sidecar suffix — will not write into DATA_DIR'


def restore_backup(archive_path: Path, categories: Optional[list[str]] = None,
                   dest_root: Optional[Path] = None) -> dict:
    archive_path = Path(archive_path)
    paths = _paths()
    with zipfile.ZipFile(archive_path) as zf:
        manifest = json.loads(zf.read('manifest.json'))
        fmt = manifest.get('format', 0)
        if fmt > FORMAT_VERSION:
            raise BackupFormatError(
                'this backup was made by a newer Clayrune — update first')

        available = {c for c in ('records', 'artifacts', 'media', 'transcripts', 'unprotected')
                    if manifest.get('categories', {}).get(c)}
        selected = available if categories is None else (available & set(categories))

        # Stage: verify EVERY selected file's sha256 into memory/temp before
        # any write happens (spec §4.5/§6) — one bad hash aborts pre-write.
        staged: list[tuple[str, dict, bytes]] = []
        for arcname, meta in manifest.get('files', {}).items():
            if meta.get('category') not in selected:
                continue
            data = zf.read(arcname)
            if _sha256_bytes(data) != meta.get('sha256'):
                raise BackupIntegrityError(f'sha256 mismatch for {arcname} — aborting before any write')
            staged.append((arcname, meta, data))

        report: dict[str, Any] = {
            'archive': str(archive_path),
            'announcement': announcement_for(manifest),
            'absent_categories': _absent_categories(manifest),
            'restored_categories': sorted(selected),
            'per_category': {c: {'restored': 0, 'conflicts': 0, 'refused': 0, 'errors': []} for c in selected},
        }

        for arcname, meta, data in staged:
            cat = meta['category']
            dest = Path(meta['dest'])
            dest = _remap(dest, Path(dest_root) if dest_root else None)
            bucket = report['per_category'][cat]

            refusal = _validate_records_write(dest, paths, manifest) if dest_root is None else None
            if refusal:
                bucket['refused'] += 1
                bucket['errors'].append(refusal)
                continue

            try:
                if cat == 'unprotected' and dest.exists():
                    existing = dest.read_bytes()
                    if existing != data:
                        conflict_path = dest.with_name(dest.name + '.crbak-restored')
                        conflict_path.parent.mkdir(parents=True, exist_ok=True)
                        conflict_path.write_bytes(data)
                        bucket['conflicts'] += 1
                        continue  # existing file is kept untouched — see module docstring
                    # byte-identical: nothing to do, but still counts as restored
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
                bucket['restored'] += 1
            except OSError as exc:
                bucket['errors'].append(f'{dest}: {exc}')

        return report


# ═══════════════════════════════════════════════════════════════════════════
# Phase 2 — per-project export/import (spec §4.1/§4.2/§4.4/§8, build order §7)
# ═══════════════════════════════════════════════════════════════════════════
#
# A project export carries only that project's own slice: its record +
# sidecars, its memory vault, AGENT_RULES.md/.mcp.json from project_path, its
# own schedules — NOT global surfaces (characters, ~/.claude/skills, other
# projects' config) the way a full backup does. That is spec §4.1's list,
# verbatim.
#
# The hivemind half of §4.1's "schedules/hivemind references" is deliberately
# NOT implemented here: spec §9 open question 3 leaves "whether hivemind
# archives travel, or only live hiveminds" explicitly unresolved, and
# data/hiveminds/<id>/ has no single project-scoping key that's safe to
# filter on without risking a silent miss. Schedules DO have one
# (`project_id` on each entry in data/schedules.json) and are the one of the
# task's three named collision classes that is genuinely archive content, so
# they're implemented; hivemind export is left for whoever answers §9 Q3.

def _project_or_raise(projects: list[dict], project_id: str) -> dict:
    p = next((x for x in projects if x['id'] == project_id), None)
    if p is None:
        raise BackupError(f"no registered project '{project_id}'")
    return p


def _load_schedules(paths: dict) -> list[dict]:
    p = paths['schedules_json']
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding='utf-8'))
        return data if isinstance(data, list) else []
    except Exception as e:
        _log(f"[backup] schedules.json unreadable, treating as empty: {e}")
        return []


def _save_schedules(paths: dict, schedules: list[dict]) -> None:
    """Same CRLF-preservation as ``_read_and_maybe_reserialize`` (module
    docstring's newline-flip bug) — this writes forward into a live file
    other code reads, not into a zip, so the same care applies directly."""
    p = paths['schedules_json']
    p.parent.mkdir(parents=True, exist_ok=True)
    newline = '\n'
    if p.is_file():
        try:
            if b'\r\n' in p.read_bytes():
                newline = '\r\n'
        except OSError:
            pass
    text = json.dumps(schedules, indent=2, ensure_ascii=False)
    if newline != '\n':
        text = text.replace('\n', newline)
    p.write_bytes(text.encode('utf-8'))


# ── Per-project enumeration (mirrors the full-install enumerators, but
#    scoped to one project id / one project_path instead of every registered
#    project) ───────────────────────────────────────────────────────────────

def _enumerate_project_records(paths: dict, project: dict) -> tuple[list[_Entry], list[_Warning]]:
    entries: list[_Entry] = []
    warnings: list[_Warning] = []
    pid = project['id']
    dd = paths['data_dir']
    if dd.is_dir():
        for f in sorted(dd.iterdir()):
            if not f.is_file() or f.suffix not in ('.json', '.jsonl'):
                continue
            if f.name != f'{pid}.json' and not f.name.startswith(f'{pid}_'):
                continue
            entries.append(_Entry(dest=f, category='records', arc_prefix='records/projects',
                                  relpath=f.name, reserialize_json=(f.suffix == '.json')))
    d = _memory_dir_for(project, paths)
    base = d if d is not None else paths['memory_fallback_dir']
    for f in _memory_files_for(project, paths):
        try:
            rel = f.relative_to(base).as_posix()
        except ValueError:
            rel = f.name
        entries.append(_Entry(dest=f, category='records',
                              arc_prefix=f'records/memory/{pid}', relpath=rel))
    pp = project.get('project_path', '')
    if pp:
        rules = Path(pp) / 'AGENT_RULES.md'
        if rules.is_file():
            entries.append(_Entry(dest=rules, category='records',
                                  arc_prefix='records/rules', relpath='AGENT_RULES.md'))
        mcp = Path(pp) / '.mcp.json'
        if mcp.is_file():
            entries.append(_Entry(dest=mcp, category='records', arc_prefix='records/mcp_project',
                                  relpath='.mcp.json', reserialize_json=True))
    return entries, warnings


def _enumerate_project_artifacts(paths: dict, project: dict) -> tuple[list[_Entry], list[_Warning]]:
    entries: list[_Entry] = []
    pid = project['id']
    pp = project.get('project_path', '')
    if pp:
        entries += _walk_dir(Path(pp) / 'docs' / '_journal', 'artifacts', f'artifacts/journal/{pid}')
    ws = paths['data_dir'] / pid
    if ws.is_dir():
        entries += _walk_dir(ws, 'artifacts', f'artifacts/workspace/{pid}')
    return entries, []


def _enumerate_project_media(paths: dict, project: dict) -> tuple[list[_Entry], list[_Warning]]:
    """Uploads carry no per-project directory of their own; every stored
    attachment filename is prefixed ``<project_id>_<item_id>_<uuid>`` at
    upload time (project_routes.py), which is the only scoping key available."""
    entries: list[_Entry] = []
    pid = project['id']
    root = paths['uploads_dir']
    if root.is_dir():
        for f in root.iterdir():
            if f.is_file() and f.name.startswith(f'{pid}_'):
                entries.append(_Entry(dest=f, category='media', arc_prefix='media/uploads', relpath=f.name))
    return entries, []


def _enumerate_project_transcripts(paths: dict, project: dict) -> tuple[list[_Entry], list[_Warning]]:
    entries: list[_Entry] = []
    pp = project.get('project_path', '')
    if not pp:
        return entries, []
    encoded = _encode_project_path(pp)
    if not encoded:
        return entries, []
    for enc in {encoded, encoded.replace('_', '-')}:
        root = paths['claude_projects_dir'] / enc
        if not root.is_dir():
            continue
        for f in root.rglob('*.jsonl'):
            if f.is_file():
                rel = f.relative_to(root).as_posix()
                entries.append(_Entry(dest=f, category='transcripts',
                                      arc_prefix=f'transcripts/claude/{enc}', relpath=rel))
    return entries, []


# ── Vault tri-state (spec §4.2) ─────────────────────────────────────────────

def _resolve_vault_choice(vault: Optional[bool], vault_passphrase: Optional[str],
                          unattended: bool) -> str:
    """Returns the vault_status string, or raises BackupError. The question
    starts UNSET (None) and an attended export must answer it explicitly —
    no default is picked in either direction (Ron's ruling, §4.2). An
    unattended run never gets to answer 'true': secret-bearing export is
    attended-only, so it always records 'not_asked'."""
    if unattended:
        if vault is True:
            raise BackupError('vault export is attended-only — refused for this trigger type')
        return 'not_asked'
    if vault is None:
        raise BackupError(
            "the vault question is unanswered — pass vault=True (include, needs a "
            "passphrase) or vault=False (omit); the export refuses to guess (spec §4.2)")
    if vault is True:
        if not vault_passphrase:
            raise BackupError('vault=True requires a passphrase')
        return 'included'
    return 'omitted'


def repo_checklist_for(repo_pointer: dict, mapped_path: Optional[str] = None) -> list[str]:
    """The §4.1 "clone <remote> at <sha> into <mapped path>" checklist — the
    honest story for why the archive never carries the repo itself."""
    target = mapped_path or repo_pointer.get('project_path') or '<project path>'
    if repo_pointer.get('is_git_repo') and repo_pointer.get('git_remote'):
        sha = repo_pointer.get('git_head') or 'HEAD'
        return [
            f"clone {repo_pointer['git_remote']} at {sha} into {target}",
            "the project shows degraded (repo missing) until that path exists",
        ]
    if repo_pointer.get('is_git_repo'):
        return [f"this checkout had no configured remote at export time — "
               f"copy the repo directory manually into {target}"]
    return [f"project_path was not a git repo at export time — "
           f"copy the directory manually into {target}"]


# ── Export ───────────────────────────────────────────────────────────────────

def export_project(project_id: str, *, categories: Optional[dict] = None,
                   vault: Optional[bool] = None, vault_passphrase: Optional[str] = None,
                   dest_dir: Optional[Path] = None, label: Optional[str] = None,
                   unattended: bool = False) -> dict:
    paths = _paths()
    projects = _iter_registered_projects(paths['data_dir'])
    project = _project_or_raise(projects, project_id)
    cats = _normalize_categories(categories)
    if not any(cats.get(c) for c in DEFAULT_CATEGORIES):
        raise BackupError('refusing to create an empty archive — untick fewer categories')

    vault_status = _resolve_vault_choice(vault, vault_passphrase, unattended)

    per_cat_fn: dict[str, Callable] = {
        'records': _enumerate_project_records,
        'artifacts': _enumerate_project_artifacts,
        'media': _enumerate_project_media,
        'transcripts': _enumerate_project_transcripts,
    }
    all_entries: list[_Entry] = []
    warnings: list[_Warning] = []
    for name, fn in per_cat_fn.items():
        if cats.get(name):
            entries, warns = fn(paths, project)
            all_entries += entries
            warnings += warns
    if _unprotected_enabled(cats):
        entries, warns, _lines = _enumerate_unprotected(paths, [project])
        all_entries += entries
        warnings += warns

    schedules = [dict(s) for s in _load_schedules(paths) if s.get('project_id') == project_id]

    pp = project.get('project_path', '') or ''
    remote, head = _git_info(Path(pp) if pp else Path('.'))
    repo_pointer = {
        'project_path': pp, 'git_remote': remote, 'git_head': head,
        'is_git_repo': bool(pp) and _is_git_repo(Path(pp)),
    }

    dest_dir = Path(dest_dir) if dest_dir else paths['backup_dir']
    dest_dir.mkdir(parents=True, exist_ok=True)
    date = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    fname = f'clayrune-project-{project_id}-{date}.crbackup'
    if vault_status == 'included':
        fname = fname.replace('.crbackup', '-SECRETS.crbackup')
    if label:
        fname = fname.replace('.crbackup', f'-{label}.crbackup')
    final_path = dest_dir / fname
    tmp_path = dest_dir / f'.{fname}.partial'

    files_manifest: dict[str, dict] = {}
    written = 0
    with zipfile.ZipFile(tmp_path, 'w', compression=zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
        for e in all_entries:
            arcname = f"{e.arc_prefix}/{e.relpath}"
            try:
                data = _read_and_maybe_reserialize(e)
            except FileNotFoundError:
                warnings.append(_Warning('vanished', str(e.dest), 'gone before it could be read'))
                continue
            except (OSError, PermissionError) as exc:
                warnings.append(_Warning('unreadable', str(e.dest), f'{exc.__class__.__name__}: {exc}'))
                continue
            except Exception as exc:
                warnings.append(_Warning('unreadable', str(e.dest), f'parse failed: {exc}'))
                continue
            zf.writestr(arcname, data)
            files_manifest[arcname] = {'sha256': _sha256_bytes(data), 'bytes': len(data),
                                       'dest': str(e.dest), 'category': e.category}
            written += 1

        if vault_status == 'included':
            blob = _secrets_store.export_all_for_backup(
                vault_passphrase or '', consumer='backup-export', scope_filter=project_id)
            zf.writestr('secrets/vault.enc', blob)
            files_manifest['secrets/vault.enc'] = {
                'sha256': _sha256_bytes(blob), 'bytes': len(blob), 'category': 'vault'}
            written += 1

        manifest = {
            'format': FORMAT_VERSION,
            'created_at': _now_iso(),
            'clayrune_version': _clayrune_version(),
            'kind': 'project',
            'categories': {
                'records': bool(cats.get('records')), 'artifacts': bool(cats.get('artifacts')),
                'media': bool(cats.get('media')), 'transcripts': bool(cats.get('transcripts')),
                'unprotected': _unprotected_enabled(cats), 'vault': vault_status == 'included',
            },
            'vault_status': vault_status,
            'contains_secrets': vault_status == 'included',
            'projects': [{'id': project_id, 'project_path': pp,
                         'git_remote': remote, 'git_head': head}],
            'repo_pointer': repo_pointer,
            'schedules': schedules,
            'files': files_manifest,
            'warnings': [w.__dict__ for w in warnings],
        }
        zf.writestr('manifest.json', json.dumps(manifest, indent=2, ensure_ascii=False))

    os.replace(tmp_path, final_path)
    return {'path': str(final_path), 'manifest': manifest, 'files_written': written,
           'warnings': manifest['warnings'], 'repo_checklist': repo_checklist_for(repo_pointer)}


# ── Import: dry-run collision report (spec §4.4) ────────────────────────────

def import_dry_run(archive_path: Path) -> dict:
    archive_path = Path(archive_path)
    paths = _paths()
    with zipfile.ZipFile(archive_path) as zf:
        manifest = json.loads(zf.read('manifest.json'))
        names = zf.namelist()

    fmt = manifest.get('format', 0)
    if fmt > FORMAT_VERSION:
        raise BackupFormatError('this backup was made by a newer Clayrune — update first')
    if manifest.get('kind') != 'project':
        raise BackupError(
            "import is for project-kind archives (export_project); a full-install "
            "archive restores via restore_backup instead")

    collisions: dict[str, list[dict]] = {'project_id': [], 'character_name': [], 'schedule_id': []}

    for p in manifest.get('projects', []):
        pid = p['id']
        exists = (paths['data_dir'] / f'{pid}.json').exists()
        collisions['project_id'].append({
            'id': pid, 'exists_locally': exists,
            'options': ['skip', 'replace', 'import-as-copy'], 'default': 'skip',
        })

    char_names = sorted({Path(n).name for n in names
                         if n.startswith('records/characters/') and not n.endswith('/')})
    for cname in char_names:
        exists = (paths['claude_agents_dir'] / cname).exists()
        collisions['character_name'].append({
            'name': cname, 'exists_locally': exists,
            'options': ['skip', 'replace'], 'default': 'skip',
            'note': 'byte-identical files silently skip regardless of resolution',
        })

    local_schedule_ids = {s.get('id') for s in _load_schedules(paths)}
    for s in manifest.get('schedules', []):
        sid = s.get('id')
        collisions['schedule_id'].append({
            'id': sid, 'exists_locally': sid in local_schedule_ids,
            'options': ['skip', 'replace'], 'default': 'skip',
            'note': 'imported schedules always arrive disabled, whichever option is picked',
        })

    project_paths = {p['id']: p.get('project_path', '') for p in manifest.get('projects', [])}
    path_ok = {pid: bool(pp) and Path(pp).is_dir() for pid, pp in project_paths.items()}

    return {
        'archive': str(archive_path),
        'announcement': announcement_for(manifest),
        'manifest': manifest,
        'collisions': collisions,
        'repo_checklist': repo_checklist_for(manifest.get('repo_pointer', {})),
        'path_remap_required': {pid: not ok for pid, ok in path_ok.items()},
        'vault_status': manifest.get('vault_status'),
        'contains_secrets': manifest.get('contains_secrets', False),
    }


# ── Import: apply (spec §4.4/§8) ────────────────────────────────────────────

def _safety_copy_before_replace(paths: dict, project_id: str) -> Path:
    """Minimal stand-in for the full restore-point spec §4.4 calls for on
    'replace' — restore points are Phase 3 and don't exist yet. Copies the
    project's json + sidecars into a timestamped folder so a bad 'replace'
    choice is not silently unrecoverable in the meantime. Does NOT copy the
    memory vault (Phase 3's job) — flagged in the caller's report."""
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    dest = paths['backup_dir'] / '_pre_replace' / f'{project_id}-{stamp}'
    dest.mkdir(parents=True, exist_ok=True)
    for f in paths['data_dir'].glob(f'{project_id}*.json'):
        try:
            (dest / f.name).write_bytes(f.read_bytes())
        except OSError as e:
            _log(f"[backup] pre-replace safety copy skipped {f}: {e}")
    return dest


def _remap_import_dest(arcname: str, paths: dict, *,
                       old_id: str, new_id: str, new_path: str,
                       new_encoded: str) -> Optional[Path]:
    """Recompute a Phase-2 archive member's destination from the NEW id/path
    rather than trusting the manifest's recorded absolute ``dest`` (which is
    only valid for Phase 1's same-machine restore). This is the §8 remap: the
    same relpath, re-rooted under whatever this machine's project_path and
    project id turned out to be."""
    parts = arcname.split('/')
    if arcname.startswith('records/projects/'):
        rel = parts[-1]
        if old_id != new_id and rel.startswith(old_id):
            rel = new_id + rel[len(old_id):]
        return paths['data_dir'] / rel
    if arcname.startswith(f'records/memory/{old_id}/'):
        return paths['claude_projects_dir'] / new_encoded / 'memory' / '/'.join(parts[3:])
    if arcname == 'records/rules/AGENT_RULES.md':
        return Path(new_path) / 'AGENT_RULES.md'
    if arcname == 'records/mcp_project/.mcp.json':
        return Path(new_path) / '.mcp.json'
    if arcname.startswith(f'artifacts/journal/{old_id}/'):
        return Path(new_path) / 'docs' / '_journal' / '/'.join(parts[3:])
    if arcname.startswith(f'artifacts/workspace/{old_id}/'):
        return paths['data_dir'] / new_id / '/'.join(parts[3:])
    if arcname.startswith('media/uploads/'):
        rel = parts[-1]
        if old_id != new_id and rel.startswith(f'{old_id}_'):
            rel = f'{new_id}_' + rel[len(old_id) + 1:]
        return paths['uploads_dir'] / rel
    if arcname.startswith('transcripts/claude/') and len(parts) > 3:
        return paths['claude_projects_dir'] / new_encoded / '/'.join(parts[3:])
    if arcname.startswith(f'unprotected/checkout/{old_id}/') or \
       arcname.startswith(f'unprotected/nongit/{old_id}/'):
        return Path(new_path) / '/'.join(parts[3:])
    return None


def import_project(archive_path: Path, *,
                   project_resolution: str = 'skip',
                   schedule_resolution: str = 'skip',
                   new_project_path: Optional[str] = None,
                   vault_passphrase: Optional[str] = None,
                   unattended: bool = False) -> dict:
    if unattended:
        raise BackupError('import is attended-only — refused for this trigger type (spec §6)')
    if project_resolution not in ('skip', 'replace', 'import-as-copy'):
        raise BackupError(f"unknown project_resolution '{project_resolution}'")
    if schedule_resolution not in ('skip', 'replace'):
        raise BackupError(f"unknown schedule_resolution '{schedule_resolution}'")

    archive_path = Path(archive_path)
    paths = _paths()
    with zipfile.ZipFile(archive_path) as zf:
        manifest = json.loads(zf.read('manifest.json'))
        fmt = manifest.get('format', 0)
        if fmt > FORMAT_VERSION:
            raise BackupFormatError('this backup was made by a newer Clayrune — update first')
        if manifest.get('kind') != 'project':
            raise BackupError('import is for project-kind archives only')

        proj_meta = manifest['projects'][0]
        old_id = proj_meta['id']
        old_path = proj_meta.get('project_path', '')
        existing_local = paths['data_dir'] / f'{old_id}.json'

        final_id = old_id
        pre_replace_copy = None
        if existing_local.exists():
            if project_resolution == 'skip':
                return {'status': 'skipped', 'archive': str(archive_path),
                       'reason': f"project '{old_id}' already exists locally"}
            if project_resolution == 'import-as-copy':
                if not new_project_path:
                    raise BackupError(
                        "import-as-copy needs new_project_path — two projects "
                        "cannot share a project_path (it is the memory-vault key, spec §8)")
                n = 2
                final_id = f'{old_id}-imported'
                while (paths['data_dir'] / f'{final_id}.json').exists():
                    final_id = f'{old_id}-imported-{n}'
                    n += 1
            else:  # replace
                pre_replace_copy = str(_safety_copy_before_replace(paths, old_id))

        new_path = new_project_path or old_path
        if not new_path:
            raise BackupError('archive has no project_path recorded and none was supplied')
        path_changed = (new_path != old_path)
        if not Path(new_path).is_dir() and not path_changed:
            raise BackupError(
                f"project_path '{old_path}' does not exist on this machine — "
                f"pass new_project_path to remap it (spec §8)")

        new_encoded = _encode_project_path(new_path)
        if not new_encoded:
            raise BackupError(f"could not encode new_project_path '{new_path}'")

        report: dict[str, Any] = {
            'archive': str(archive_path), 'status': 'applied',
            'final_project_id': final_id, 'old_project_id': old_id,
            'old_project_path': old_path, 'new_project_path': new_path,
            'path_remapped': path_changed, 'pre_replace_copy': pre_replace_copy,
            'restored': {'records': 0, 'artifacts': 0, 'media': 0, 'transcripts': 0, 'unprotected': 0},
            'schedules_imported': 0, 'schedules_skipped': 0,
            'vault': {'status': manifest.get('vault_status')},
            'warnings': [],
        }

        # Stage + verify EVERY non-vault file's sha256 before any write, same
        # pre-write-abort discipline as Phase 1's restore_backup.
        staged: list[tuple[str, dict, bytes]] = []
        for arcname, meta in manifest.get('files', {}).items():
            if meta.get('category') == 'vault':
                continue
            data = zf.read(arcname)
            if _sha256_bytes(data) != meta.get('sha256'):
                raise BackupIntegrityError(f'sha256 mismatch for {arcname} — aborting before any write')
            staged.append((arcname, meta, data))

        for arcname, meta, data in staged:
            dest = _remap_import_dest(arcname, paths,
                                      old_id=old_id, new_id=final_id,
                                      new_path=new_path, new_encoded=new_encoded)
            if dest is None:
                report['warnings'].append(f'could not map destination for {arcname} — skipped')
                continue
            try:
                if meta['category'] == 'unprotected' and dest.exists():
                    existing = dest.read_bytes()
                    if existing != data:
                        conflict_path = dest.with_name(dest.name + '.crbak-restored')
                        conflict_path.parent.mkdir(parents=True, exist_ok=True)
                        conflict_path.write_bytes(data)
                        continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
                report['restored'][meta['category']] += 1
            except OSError as exc:
                report['warnings'].append(f'{dest}: {exc}')

        # Schedules merge into the shared file — never a whole-file overwrite.
        locals_ = _load_schedules(paths)
        by_id = {s.get('id'): s for s in locals_}
        for sched in manifest.get('schedules', []):
            sid = sched.get('id')
            sched = dict(sched)
            sched['project_id'] = final_id
            sched['enabled'] = False  # imported schedules never arrive live (spec §4.4)
            if sid in by_id:
                if schedule_resolution == 'skip':
                    report['schedules_skipped'] += 1
                    continue
                by_id[sid] = sched
            else:
                by_id[sid] = sched
            report['schedules_imported'] += 1
        _save_schedules(paths, list(by_id.values()))

        # Vault — decrypt under the caller's passphrase, write through
        # secrets_store so entries land under THIS machine's own master key.
        if manifest.get('contains_secrets'):
            if not vault_passphrase:
                report['vault']['imported'] = None
                report['warnings'].append(
                    'archive contains a vault section but no passphrase was given — vault NOT imported')
            else:
                blob = zf.read('secrets/vault.enc')
                vmeta = manifest.get('files', {}).get('secrets/vault.enc', {})
                if vmeta and _sha256_bytes(blob) != vmeta.get('sha256'):
                    raise BackupIntegrityError('sha256 mismatch for secrets/vault.enc — aborting')
                try:
                    result = _secrets_store.import_all_from_backup(
                        blob, vault_passphrase, consumer='backup-import',
                        rescope=(old_id, final_id) if final_id != old_id else None)
                except _secrets_store.SecretsError as e:
                    # Everything else in this import already committed by the
                    # time decryption can fail (vault runs last, deliberately,
                    # since files/schedules are independently useful even if
                    # the passphrase turns out wrong) — surfaced as a
                    # BackupError so callers don't need to know about the
                    # secrets_store exception hierarchy too.
                    raise BackupError(f'vault import failed: {e}') from e
                report['vault']['imported'] = result['imported']
                report['vault']['skipped'] = result['skipped']

        # Finalize the project record's own id/project_path (its bytes were
        # staged/written above under the possibly-renamed filename already).
        rec_path = paths['data_dir'] / f'{final_id}.json'
        if rec_path.is_file():
            try:
                rec = json.loads(rec_path.read_text(encoding='utf-8'))
                rec['id'] = final_id
                rec['project_path'] = new_path
                rec_path.write_text(json.dumps(rec, indent=2, ensure_ascii=False), encoding='utf-8')
            except Exception as e:
                report['warnings'].append(f'could not finalize project record {rec_path}: {e}')
        else:
            report['warnings'].append(
                f'no project record was restored at {rec_path} — the "records" category '
                f'may have been excluded from this export')

        report['repo_checklist'] = repo_checklist_for(manifest.get('repo_pointer', {}), new_path)
        return report
