"""Clayrune backup / restore — Phase 1 of docs/BACKUP_EXPORT_SPEC.md.

Full-install backup + restore, complete by default. Same-machine semantics
only (no path remapping, no vault re-encoding — that is Phase 2, spec §8).
Manifest format 1, including the ``categories`` field (spec §4.5) so it never
needs a format bump to grow this in Phase 2/3.

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
