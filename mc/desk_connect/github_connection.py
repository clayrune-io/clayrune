"""Desk's GitHub staging and approval card, reusing the MCP URL installer.

Only deterministic config extraction and tool-free scanning see repository text.
Staging never installs/runs a repository. The common human/passcode commit owns Save.
"""
from __future__ import annotations
import json
import re
import secrets
import shutil
import threading
import time
from collections import OrderedDict
from pathlib import Path
from mc import mcp_installer as installer
from mc.desk_connect import custom_connection_operation as operation
from mc.desk_connect import custom_connection_store as store
from mc.desk_connect import github_manifest as manifest
from mc.desk_connect import github_activation as activation
from mc.desk_connect import github_install_output as output
from mc.desk_connect import github_install_steps as steps_builder
from mc.desk_connect import parameter_schema as parameters
from mc.desk_connect.mcp_errors import ActivationError

_stages: OrderedDict[str, dict] = OrderedDict()
_lock = threading.Lock()

def stage(body: object) -> dict:
    classified = installer.classify_url(body.get('url', '') if isinstance(body, dict) else '')
    if classified.get('kind') != 'git' or not classified.get('owner') or not classified.get('repo'):
        raise ActivationError('Enter a GitHub repository web address.', 'bad_repository', 400)
    token = secrets.token_hex(16)
    try:
        clone = installer.stage_clone(classified['url'], classified['owner'], classified['repo'],
                                      classified.get('ref'), staging_id=token)
        if not re.fullmatch(r'[0-9a-f]{40}', clone['sha']):
            raise ActivationError('A fixed repository version could not be found.', 'bad_pin', 400)
        files = manifest.inventory(clone['install_dir'])
        extracted = installer.extract_config(clone['install_dir'], allow_claude_fallback=False)
        configs = extracted.get('servers') or {}
        detected = installer.detect_secrets(configs)
        audit = installer.dependency_audit(clone['install_dir'], read_only=True)
        scan = installer.security_scan(clone['install_dir'], clone['sha'], toolless=True)
        if files != manifest.inventory(clone['install_dir']):
            raise ActivationError('The repository changed while it was checked.', 'repository_changed', 409)
        steps = steps_builder.dependencies(clone['install_dir'])
        p = Path(clone['install_dir']) / 'package.json'
        pkg = json.loads(p.read_text(encoding='utf-8')) if p.is_file() else {}
        scripts = pkg.get('scripts', {})
        if not isinstance(scripts, dict):
            raise ActivationError('The declared install steps could not be read.', 'bad_scripts', 400)
        for name, script in scripts.items():
            if not isinstance(script, str) or len(script) > 10000:
                raise ActivationError('An install step cannot be shown completely.', 'bad_scripts', 400)
            steps.append({'id': f'script:{name}', 'argv': [], 'body': script, 'script': name})
        cfg = next(iter(configs.values()), {})
        command = cfg.get('command', '') if isinstance(cfg, dict) else ''
        args = cfg.get('args', []) if isinstance(cfg, dict) else []
        # A registry launcher would ignore the reviewed clone's commit.
        if Path(str(command)).name.lower() in ('npx', 'npx.cmd', 'npm', 'uvx', 'pipx'):
            command, args = '', []
        rec = {**clone, 'url': classified['url'], 'files': files, 'steps': steps,
               'expires': time.monotonic()+1800, 'audit': audit, 'scan': scan}
        with _lock:
            _stages[token] = rec
            while len(_stages)>50:
                _stages.popitem(last=False)
        return {'stage_id': token, 'sha': clone['sha'], 'command': command, 'args': args,
                'credentials': [{'env': c['key']} for c in detected], 'manual_command': not bool(command)}
    except ActivationError:
        raise
    except Exception as e:
        from mc.core import _log
        _log(f'[desk-connect] repository staging failed: {type(e).__name__}', flush=True)
        raise ActivationError('The repository could not be checked; see the server log.', 'stage_failed', 400) from e

def prepare(body: dict, resolve_project) -> dict:
    from mc.desk_connect import custom_connection_service as service
    with _lock:
        stage_id = body.get('stage_id')
        staged = _stages.get(stage_id) if isinstance(stage_id, str) else None
    if not staged or staged['expires'] < time.monotonic():
        raise ActivationError('These details expired. Enter the address again.', 'stage_expired', 409)
    if manifest.inventory(staged['install_dir']) != staged['files']:
        raise ActivationError('The repository changed. Enter the address again.', 'repository_changed', 409)
    raw = {k:v for k,v in body.items() if k not in ('stage_id','command')}
    fields = operation.clean_fields(raw, service.visible_vault_names(body.get('scope','project'),body.get('project_id')), default_name=Path(staged['install_dir']).name[:60])
    command = body.get('command')
    if not isinstance(command,str) or not command.strip() or len(command)>300 or parameters.has_hidden_chars(command) or parameters.credential_like(command):
        raise ActivationError('Enter the start command.', 'command_required', 400)
    if Path(command).name.lower() in ('npx','npx.cmd','npm','npm.cmd','uvx','pipx'):
        raise ActivationError('Use a command that runs the saved repository, rather than downloading other code.', 'unpinned_command', 400)
    chosen = set(fields['approve_scripts'])
    if chosen - {s['id'] for s in staged['steps']}:
        raise ActivationError('An install step changed. Review it again.', 'bad_script_approval', 400)
    op = {'schema':'desk-github-connection/2','ecosystem':'github','protocol':'stdio',
          'file_checks':dict(output.POLICY),
          'package':staged['url'], 'version':staged['sha'], 'integrity':operation.fingerprint(staged['files']),
          'directory':staged['install_dir'], 'source_files':staged['files'], 'command':shutil.which(command.strip()) or command.strip(),
          'args':fields['args'], 'credentials':fields['credentials'], 'server_name':fields['server_name'],
          'strip_env':['NODE_OPTIONS','NODE_PATH','PYTHONPATH','PYTHONHOME'],
          'scope':{'kind':fields['scope'],'project_id':fields['project_id']},
          'install_steps':[s for s in staged['steps'] if s['id'] in chosen]}
    project = resolve_project(fields['project_id']) if fields['scope']=='project' else None
    clash = activation.conflict(op, project['path'] if project else None)
    if clash:
        raise clash
    fp = operation.fingerprint(op)
    previous = store.get(fields['scope'], fields['project_id'], fields['server_name'])
    request_id = secrets.token_urlsafe(24)
    service._remember(request_id, {'op':op,'fingerprint':fp,'project':project,'expires':time.monotonic()+1800})
    return {'request_id':request_id,'fingerprint':fp,'title':Path(staged['url']).stem,'server_name':op['server_name'],
            'protocol':'stdio','origin':{'code':'user_supplied','label':'User supplied; not reviewed by Clayrune'},
            'command':activation.describe_argv(op), 'package':{'version':staged['sha'],'integrity':op['integrity'],'source':staged['url'],'registry':'GitHub','pinned':True},
            'reach':{'scope':fields['scope'],'who':'Agents in this project only.' if project else 'Agents in every project.','local_code':'Runs with your file and network access; no sandbox.'},
            'credentials':op['credentials'], 'changes':operation.changes(previous['operation'] if previous else None,op),
            'reask':bool(previous and previous['fingerprint'] != fp),
            'replaces':previous['fingerprint'] if previous and previous['fingerprint'] != fp else None,
            'dependencies':[], 'limitations':[],
            'scripts':[{'id':s['id'],'path':'','package':op['package'],'version':op['version'],'script':s.get('script',s['id']),
                        'body':s.get('body') or json.dumps(s['argv']), 'body_escaped':s.get('body') or json.dumps(s['argv']),
                        'approved':s['id'] in chosen,'approvable':True,'reason':''} for s in staged['steps']],
            'scripts_note':'Every install step starts off. Selected steps run only after passcode Save; dependencies may be unpinned. '+output.NOTICE,
            'install_note':'Only selected install steps run. Nothing starts until an agent uses this connection.',
            'working_directory':staged['install_dir'],'first_start':'Deferred until an agent session uses it.',
            'risks':[{'code':'unreviewed','label':'User supplied code; no safety guarantee. Dependencies and selected install steps can fetch or run other code.'},
                     {'code':'install_output_not_rechecked','label':output.NOTICE}],
            'repository_checks':{'audit':staged['audit'],'scan':staged['scan']}}
