"""Pinned Addy guidance plans and guarded project imports (DH-M2).

No tool installation, activation or trust override. Network staging remains the
existing skills Git import; the reviewed plan contains only its selected files.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import tempfile
from typing import Any
from urllib.parse import unquote, urlsplit

from mc import skills
from mc.atomic_json import write_json_atomic
from mc.dev_harness_catalogue import digest
from mc.dev_harness_store import HarnessStore, StoreError

SOURCE = 'addyosmani/agent-skills'
ORIGIN = 'https://github.com/addyosmani/agent-skills'
PIN = 'aeac41d4d005999432bb7312f7ff06383a91d59d'
NAMES = frozenset(('constraint-driven-development', 'test-driven-development',
    'security-and-hardening', 'ci-cd-and-automation', 'shipping-and-launch',
    'code-review-and-quality', 'documentation-and-adrs', 'performance-optimization',
    'observability-and-instrumentation', 'deprecation-and-migration',
    'api-and-interface-design', 'frontend-ui-engineering', 'code-simplification'))
_LINK = re.compile(r'!?\[[^\]\n]*\]\(\s*<?([^\s)>]+)>?(?:\s+[^)]*)?\)|^\s*\[[^]]+\]:\s*<?([^\s>]+)>?', re.M)
_CODE_PATH = re.compile(r'`((?:\.{1,2}/|references/|resources/|scripts/|assets/)[^`\s]+)`')
MAX_FILES = 2000
MAX_BYTES = 25_000_000


def selected_sources(policy: dict[str, Any], catalogue: dict[str, Any]) -> list[dict[str, str]]:
    """Only the immutable default, deduplicated across enabled practices."""
    selected: dict[str, dict[str, str]] = {}
    for key, choice in sorted(policy['practices'].items()):
        if not choice['enabled']:
            continue
        for source in catalogue['practices'][key]['guidance']:
            name = source['subpath'].removeprefix('skills/')
            if (source['source'] != SOURCE or source['origin'] != ORIGIN
                    or source['pin'] != PIN or source['licence'] != 'MIT'
                    or name not in NAMES or source['subpath'] != f'skills/{name}'):
                raise ValueError('Only the pinned Addy default guidance is supported')
            selected[name] = {k: source[k] for k in ('source', 'origin', 'pin', 'licence', 'subpath')}
    return [selected[name] for name in sorted(selected)]


def _safe_path(root: Path, rel: str) -> Path:
    if ('\\' in rel or ':' in rel or not rel or PurePosixPath(rel).is_absolute()
            or '..' in PurePosixPath(rel).parts):
        raise ValueError('Invalid resource path')
    path = root / rel
    for part in (path, *path.parents):
        if part == root.parent:
            break
        if part.is_symlink() or getattr(part, 'is_junction', lambda: False)():
            raise ValueError('Linked guidance resources are not supported')
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('Resource escapes guidance root')
    return path


def _reference(root: Path, parent: Path, target: str) -> Path | None:
    parsed = urlsplit(target)
    if parsed.scheme and parsed.scheme not in {'https', 'http', 'mailto'}:
        raise ValueError('Unsupported guidance link scheme')
    if parsed.scheme or parsed.netloc or not parsed.path:
        return None
    rel = unquote(parsed.path)
    if '\\' in rel or ':' in rel or rel.startswith('/'):
        raise ValueError('Invalid local guidance reference')
    path = Path(os.path.abspath(parent / rel))
    if not path.is_relative_to(root):
        raise ValueError('Local reference escapes staged repository')
    return _safe_path(root, path.relative_to(root).as_posix())


def resource_manifest(root: Path, sources: list[dict[str, str]]) -> dict[str, str]:
    """Close local Markdown links/backtick resource paths, preserving layout.

    Selected subtrees include all their files; directory references include
    their full subtree. Missing references and links fail closed.
    """
    root = root.resolve()
    pending = [_safe_path(root, 'LICENSE')]
    for source in sources:
        folder = _safe_path(root, source['subpath'])
        if not (folder / 'SKILL.md').is_file():
            raise ValueError('Selected guidance is missing SKILL.md')
        pending.append(folder)
    files: dict[str, str] = {}
    selected_paths = {source['subpath'] for source in sources}
    size = 0
    visited: set[str] = set()
    while pending:
        path = pending.pop()
        rel = path.relative_to(root).as_posix()
        parts = PurePosixPath(rel).parts
        if (parts[0] in {'.git', '.claude-plugin', 'hooks', 'agents', 'commands'}
                or (parts[0] == 'skills' and len(parts) > 1
                    and '/'.join(parts[:2]) not in selected_paths)):
            raise ValueError('Reference requires an unselected skill or plugin component')
        path = _safe_path(root, rel)
        if rel in visited:
            continue
        visited.add(rel)
        if len(visited) > MAX_FILES * 4:
            raise ValueError('Guidance resource limit exceeded')
        if path.is_dir():
            pending.extend(path.iterdir())
            continue
        if not path.is_file():
            raise ValueError(f'Missing guidance resource: {rel}')
        if path.stat().st_size > 2_000_000:
            raise ValueError('Guidance resource limit exceeded')
        data = path.read_bytes()
        size += len(data)
        files[rel] = hashlib.sha256(data).hexdigest()
        if len(files) > MAX_FILES or size > MAX_BYTES:
            raise ValueError('Guidance resource limit exceeded')
        if path.suffix.lower() in ('.md', '.markdown'):
            text = data.decode('utf-8')
            refs = [a or b for a, b in _LINK.findall(text)] + _CODE_PATH.findall(text)
            for target in refs:
                ref = _reference(root, path.parent, target)
                if ref is not None:
                    pending.append(ref)
    if not files.get('LICENSE') or not (root / 'LICENSE').read_text(encoding='utf-8').strip():
        raise ValueError('Upstream licence notice is missing')
    return dict(sorted(files.items()))


def build_install_plan(*, store: HarnessStore, project: dict[str, Any],
                       policy: dict[str, Any], catalogue: dict[str, Any],
                       staging_id: str) -> dict[str, Any]:
    """Inspect an already staged full pinned checkout; no download or install."""
    if not isinstance(staging_id, str) or not project.get('project_path'):
        raise ValueError('Existing staging identity and project directory are required')
    root = skills.staging_dir(staging_id)
    provenance = json.loads((root / '.mc_git_source.json').read_text(encoding='utf-8'))
    if (not isinstance(provenance, dict) or provenance.get('clone_url') not in (ORIGIN, ORIGIN + '.git')
            or provenance.get('commit') != PIN or provenance.get('subpath')):
        raise ValueError('Stage the full pinned Addy repository through skills Git import')
    project_path = Path(project['project_path']).resolve()
    if not project_path.is_dir():
        raise ValueError('Project directory is unavailable')
    sources = selected_sources(policy, catalogue)
    if not sources:
        raise ValueError('No guidance selected')
    files = resource_manifest(root, sources)
    checkout_files = provenance.get('files')
    if not isinstance(checkout_files, dict) or any(checkout_files.get(p) != value for p, value in files.items()):
        raise ValueError('Staged files differ from the pinned checkout; stage the source again')
    lock = {'version': 1, 'sources': sources, 'files': files,
            'install_location': '.clayrune/dev-harness/guidance'}
    source_digest = digest(lock)
    target = project_path / lock['install_location'] / source_digest
    lock_path = project_path / '.clayrune/dev-harness/sources.lock.json'
    plan = {'version': 1, 'project_id': project['id'], 'project_path': str(project_path),
            'staging_id': staging_id, 'policy_digest': digest(policy),
            'catalogue_digest': catalogue['digest'], 'sources_digest': source_digest,
            'lock': lock, 'destination': str(target),
            'network_destinations': [], 'cost': 0,
            'disk_bytes': sum((root / p).stat().st_size for p in files),
            'platforms': {name: 'requires real delivery verification' for name in ('Windows', 'macOS', 'Linux')},
            'already_exists': target.exists(),
            'previous_lock_digest': hashlib.sha256(lock_path.read_bytes()).hexdigest() if lock_path.exists() else None,
            'job_entry': [sys.executable, '-m', 'mc.dev_harness_sources', '--data-root', str(store.root.parent),
                          '--project-id', project['id'], '--plan', '<plan_digest shown below>'],
            'operations': ['scan selected resource closure', 'copy original bytes',
                           'verify hashes', 'write source lock and installation record'],
            'activation': False}
    plan['plan_digest'] = digest(plan)
    folder = store.project_dir(project['id']) / 'plans'
    folder.mkdir(parents=True, exist_ok=True)
    write_json_atomic(folder / (plan['plan_digest'] + '.json'), plan, indent=2)
    return plan


def read_plan(store: HarnessStore, project_id: str, plan_digest: str) -> dict[str, Any]:
    from mc.dev_harness_policy import require_digest
    require_digest(plan_digest)
    folder = store.project_dir(project_id)
    plan = store._read_json(_safe_path(folder, f'plans/{plan_digest}.json'))
    if (plan.get('project_id') != project_id or plan.get('plan_digest') != plan_digest
            or digest({k: v for k, v in plan.items() if k != 'plan_digest'}) != plan_digest):
        raise StoreError('Install plan integrity mismatch')
    return plan


def verify_installation(store: HarnessStore, project: dict[str, Any],
                        source_digest: str) -> dict[str, Any]:
    from mc.dev_harness_policy import require_digest
    require_digest(source_digest)
    folder = store.project_dir(project['id'])
    receipt = store._read_json(_safe_path(folder, f'installations/{source_digest}.json'))
    lock = receipt.get('lock')
    if (receipt.get('status') != 'verified' or receipt.get('project_id') != project['id']
            or not isinstance(lock, dict) or digest(lock) != source_digest
            or not isinstance(lock.get('sources'), list) or not isinstance(lock.get('files'), dict)):
        raise StoreError('Guidance installation is incomplete or changed')
    target = _safe_path(Path(project['project_path']).resolve(),
                        f'.clayrune/dev-harness/guidance/{source_digest}')
    if resource_manifest(target, lock['sources']) != lock['files']:
        raise StoreError('Installed guidance resources have changed')
    return {'lock': lock, 'destination': str(target)}


def validate_activation(store: HarnessStore, project: dict[str, Any],
                        policy: dict[str, Any], catalogue: dict[str, Any]) -> None:
    """Enforce cannot activate missing, partial, changed or quarantined sources."""
    installed = verify_installation(store, project, policy['digests'].get('sources', ''))
    if installed['lock']['sources'] != selected_sources(policy, catalogue):
        raise ValueError('Selected guidance differs from the verified installation')


def _check_prior_lock(lock_path: Path, plan: dict[str, Any]) -> None:
    prior = hashlib.sha256(lock_path.read_bytes()).hexdigest() if lock_path.exists() else None
    if prior != plan['previous_lock_digest']:
        # Only an identical completed plan may be retried without new inspection.
        if not lock_path.is_file() or json.loads(lock_path.read_text(encoding='utf-8')) != plan['lock']:
            raise StoreError('Source lock changed after inspection; inspect a new plan')


def execute_install(store: HarnessStore, project_id: str, plan_digest: str) -> dict[str, Any]:
    """Run only a human-approved plan. Write delivery evidence last.

    Partial trees never become evidence. Never overwrite user-edited resources.
    """
    plan = read_plan(store, project_id, plan_digest)
    approval = store._read_json(_safe_path(store.project_dir(project_id), f'approved/{plan_digest}.json'))
    if approval != {'plan_digest': plan_digest, 'project_id': project_id}:
        raise StoreError('Exact human-approved install plan is required')
    project = {'id': project_id, 'project_path': plan['project_path']}
    lock_path = _safe_path(Path(plan['project_path']), '.clayrune/dev-harness/sources.lock.json')
    _check_prior_lock(lock_path, plan)
    sources = plan['lock']['sources']
    stage = skills.staging_dir(plan['staging_id'])
    if resource_manifest(stage, sources) != plan['lock']['files']:
        raise StoreError('Staged resources changed after inspection; make a new plan')
    if digest(plan['lock']) != plan['sources_digest']:
        raise StoreError('Invalid source lock')
    target = _safe_path(Path(plan['project_path']),
                        f'.clayrune/dev-harness/guidance/{plan["sources_digest"]}')
    with tempfile.TemporaryDirectory(prefix='clayrune-guidance-') as tmp:
        closure = Path(tmp) / 'closure'
        closure.mkdir()
        for rel in plan['lock']['files']:
            dest = closure / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(_safe_path(stage, rel), dest)
        if resource_manifest(closure, sources) != plan['lock']['files']:
            raise StoreError('Guidance changed during staging')
        skills.import_preserved_project_tree(closure, target, Path(plan['project_path']))
    if resource_manifest(target, sources) != plan['lock']['files']:
        raise StoreError('Guidance copy verification failed')
    _check_prior_lock(lock_path, plan)
    write_json_atomic(lock_path, plan['lock'], indent=2)
    record = {'status': 'verified', 'project_id': project_id, 'lock': plan['lock']}
    receipt = store.project_dir(project_id) / 'installations' / (plan['sources_digest'] + '.json')
    receipt.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(receipt, record, indent=2)
    verify_installation(store, project, plan['sources_digest'])
    return {'status': 'verified', 'sources_digest': plan['sources_digest'], 'activation': False}


def install_plan_request(project_id: str, store: HarnessStore, project: dict[str, Any]):
    from flask import jsonify, request
    from mc.dev_harness_catalogue import load_catalogue
    from mc.dev_harness_policy import resolve_policy
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or set(data) != {'draft', 'staging_id'}:
        raise ValueError('Expected draft and existing staging_id')
    catalogue = load_catalogue()
    return jsonify(build_install_plan(store=store, project=project,
        policy=resolve_policy(data['draft'], catalogue), catalogue=catalogue,
        staging_id=data['staging_id']))


def install_request(project_id: str, store: HarnessStore, project: dict[str, Any]):
    """Reuse caller/passcode gates and the existing registered session job path."""
    from flask import jsonify, request
    from mc.blueprints.secrets_routes import _require_human_passcode
    from mc.project_safety_settings import is_agent_caller
    if is_agent_caller():
        return jsonify({'error': 'Guidance installation is human-only'}), 403
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or set(data) != {'passcode', 'plan_digest', 'session_id'}:
        raise ValueError('Expected passcode, plan_digest and receiving session_id')
    refusal = _require_human_passcode(data)
    if refusal is not None:
        return refusal
    plan = read_plan(store, project_id, data['plan_digest'])
    if plan['project_path'] != str(Path(project['project_path']).resolve()):
        raise ValueError('Project location changed; inspect a new plan')
    folder = store.project_dir(project_id) / 'approved'
    folder.mkdir(parents=True, exist_ok=True)
    write_json_atomic(folder / (data['plan_digest'] + '.json'),
                      {'plan_digest': data['plan_digest'], 'project_id': project_id}, indent=2)
    argv = [sys.executable, '-m', 'mc.dev_harness_sources', '--data-root', str(store.root.parent),
            '--project-id', project_id, '--plan', data['plan_digest']]
    if os.name == 'nt':
        command = '& ' + ' '.join("'" + s.replace("'", "''") + "'" for s in argv)
        shell = 'powershell'
    else:
        import shlex
        command, shell = shlex.join(argv), 'sh'
    from mc.blueprints import agent_routes
    return agent_routes.agent_job_start(project_id, data['session_id'], job_data={
        'command': command, 'shell': shell, 'cwd': str(Path(__file__).resolve().parent.parent)})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root', required=True)
    parser.add_argument('--project-id', required=True)
    parser.add_argument('--plan', required=True)
    args = parser.parse_args()
    try:
        result = execute_install(HarnessStore(Path(args.data_root)), args.project_id, args.plan)
    except Exception as exc:
        # No upstream content/scanner findings in managed job output.
        from mc.skill_import_guard import SkillQuarantined
        code, repair = 'install_error', 'Inspect the approved plan and retry after repairing the installation.'
        if isinstance(exc, SkillQuarantined):
            if exc.verdict.get('scan_crashed'):
                code, repair = 'scanner_error', 'Repair the import scanner and retry the same plan; no override is available.'
            else:
                code, repair = 'quarantined', 'Review the selected closure in the existing quarantine surface; guidance remains unavailable.'
        elif isinstance(exc, FileExistsError):
            code, repair = 'destination_conflict', 'Preserve the partial or user-edited destination and inspect a new plan.'
        elif isinstance(exc, StoreError):
            code, repair = 'state_or_hash_mismatch', 'Re-inspect the plan, approval, source hashes and installation record; preserve existing artifacts.'
        elif isinstance(exc, ValueError):
            code, repair = 'invalid_resource_or_plan', 'Compare the approved resource manifest with the staged tree; fix missing/escaping resources and inspect a new plan.'
        elif isinstance(exc, OSError):
            code, repair = 'io_error', 'Restore access to the approved staging/project/runtime paths, then retry; partial artifacts remain unverified.'
        print(json.dumps({'status': 'error', 'code': code, 'repair': repair, 'activation': False}))
        raise SystemExit(2)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
