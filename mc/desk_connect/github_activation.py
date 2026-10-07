"""Activate an approved repository and check its recorded files before every start."""
from __future__ import annotations
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from mc import mcp, mcp_installer, secrets_store
from mc.core import _log
from mc.desk_connect import custom_connection_operation as operation
from mc.desk_connect import custom_connection_store as store
from mc.desk_connect import custom_connection_activation as npm
from mc.desk_connect import custom_npm_scripts as scripts
from mc.desk_connect import github_manifest as manifest
from mc.desk_connect import github_install_output as output
from mc.desk_connect.mcp_errors import ActivationError

def _manifest_path(op: dict) -> Path:
    return mcp_installer.INSTALLS_ROOT.parent / 'mcp_git_manifests' / (operation.fingerprint(op)[7:]+'.json')

def launch_config(op: dict) -> dict:
    gate = Path(__file__).resolve().parents[2] / 'tools' / 'github-mcp-gate.py'
    if getattr(sys,'frozen',False) or not gate.is_file():
        raise ActivationError('This install cannot start saved repositories.', 'wrapper_missing', 409)
    return {'command':sys.executable,'args':['-I',str(gate),op['scope']['kind'],op['scope']['project_id'] or '-',op['server_name'],operation.fingerprint(op)]}

def describe_argv(op: dict) -> dict:
    return {**launch_config(op),'runnable':True, 'starts':{'command':op['command'],'args':op['args']}}

def matches(cfg, op: dict, strict: bool = True) -> bool:
    try:
        return isinstance(cfg,dict) and {k:v for k,v in cfg.items() if k!='type'}==launch_config(op) and cfg.get('type') in (None,'stdio')
    except ActivationError:
        return False

def conflict(op: dict, project_path: str | None):
    cur = npm._read(op['scope']['kind'],op['scope']['project_id'],op['server_name'],project_path)
    if cur is None or matches(cur,op):
        return None
    previous = store.get(op['scope']['kind'],op['scope']['project_id'],op['server_name'])
    if previous and previous['operation'].get('ecosystem')=='github' and matches(cur,previous['operation']):
        return None
    return npm._clash(op)

def problem(op: dict) -> str:
    try:
        expected=json.loads(_manifest_path(op).read_text(encoding='utf-8'))
        unchanged = (output.source_matches(op, expected) if op.get('file_checks') == output.POLICY
                     else expected == manifest.inventory(op['directory']))
        if not unchanged:
            return 'The saved repository files changed. Review the connection again.'
        return ''
    except (OSError,ValueError,ActivationError) as e:
        _log(f'[desk-connect] repository file check refused: {type(e).__name__}',flush=True)
        return 'The saved repository files cannot be checked. Review the connection again.'

def provision(op: dict, project_path: str | None) -> dict:
    try:
        if manifest.inventory(op['directory']) != op['source_files']:
            raise ActivationError('The repository changed since Review.', 'repository_changed',409)
        cwd=Path(op['directory'])
        with tempfile.TemporaryDirectory(prefix='desk-git-install-') as temporary:
            (Path(temporary)/'tmp').mkdir()
            for step in op['install_steps']:
                step={**step,'package':op['package'],'version':op['version']}
                if step.get('argv'):
                    env=scripts.script_env({'script':step['id'],'package':op['package'],'version':op['version']},Path(temporary),str(Path(shutil.which('node') or sys.executable).parent),cwd)
                    rc,_=scripts.spawn(step['argv'],cwd,env,120)
                    if rc != 0:
                        raise ActivationError('A selected install step failed. See the server log.', 'install_failed',400)
                else:
                    scripts.run(step,cwd,Path(temporary),node_dir=str(Path(shutil.which('node') or sys.executable).parent))
        files=(output.capture(op['directory'], op['source_files']) if op.get('file_checks') == output.POLICY
               else manifest.inventory(op['directory']))
        target=_manifest_path(op);target.parent.mkdir(parents=True,exist_ok=True)
        from mc.atomic_json import write_json_atomic
        write_json_atomic(target,files)
        cfg=mcp.normalize_config('stdio',launch_config(op))
        def mutate(servers):
            cur=servers.get(op['server_name'])
            if cur is not None and not matches(cur,op):
                rec=store.get(op['scope']['kind'],op['scope']['project_id'],op['server_name'])
                previous=rec.get('replaces') if rec else None
                if not previous or previous.get('ecosystem')!='github' or not matches(cur,previous):
                    raise npm._clash(op)
            servers[op['server_name']]=cfg
        if op['scope']['kind']=='global':
            mcp._write_global_servers(mutate)
        else:
            mcp._write_project_servers(project_path or '',mutate)
        return {'state':'registered','code':'','message':'Registered; not checked. It starts when an agent uses it.'}
    except Exception as e:
        _log(f'[desk-connect] repository setup failed: {type(e).__name__}',flush=True)
        return {'state':'setup_failed','code':getattr(e,'code','setup_failed'),'message':str(e) if isinstance(e,ActivationError) else 'Setup failed; see the server log.'}

def derive_state(rec: dict, project_path: str | None) -> dict:
    op=rec['operation']
    cur=npm._read(rec['scope'],rec['project_id'],rec['server_name'],project_path)
    if cur is None:
        return {'state':rec['state'] if rec['state']=='setup_failed' else 'missing','message':'Saved connection is not registered.'}
    why=problem(op)
    if not matches(cur,op) or why:
        return {'state':'changed','message':why or 'The saved start command changed.'}
    names={s['name'] for s in secrets_store.list_secrets(op['scope']['project_id'])}
    if any(c['vault'] not in names for c in op['credentials']):
        return {'state':'credential_missing','message':'A saved credential is missing.'}
    return {'state':'registered','message':'Registered; not checked.'}

def start(scope: str, project_id: str | None, name: str, fingerprint: str) -> None:
    rec=store.get(scope,project_id,name)
    if not rec or rec['fingerprint']!=fingerprint or operation.fingerprint(rec['operation'])!=fingerprint or rec['state']!='registered':
        raise RuntimeError('The saved repository approval no longer matches.')
    op=rec['operation'];why=problem(op)
    if why:
        raise RuntimeError(why)
    from mc.desk_connect import custom_npm_gate, custom_npm_node_paths
    if Path(op['command']).stem.lower()=='node':
        issue=custom_npm_gate.ancestor_problem(op,Path(op['directory']).parent) or custom_npm_node_paths.problem(op,['--',op['command']])
        if issue:
            raise RuntimeError(issue['message'])
    command=op['command'];argv=[command,*op['args']]
    if op['credentials']:
        flags=npm.wrapper_flags(op)
        argv=[sys.executable,str(npm._base.wrapper_path()),*flags,command,*op['args']]
        command=sys.executable
    os.chdir(op['directory'])
    env=custom_npm_node_paths.child_env(op['strip_env'])
    os.execvpe(command,argv,env)
