"""Allowlisted setup draft from a saved approval; never credential values or approval."""
from mc.desk_connect import custom_connection_store as store
from mc.desk_connect.mcp_errors import ActivationError


def read(raw) -> dict:
    if not isinstance(raw, dict) or set(raw) - {'scope', 'project_id', 'server_name'}:
        raise ActivationError('name the saved server', 'invalid', 400)
    scope, pid, name = raw.get('scope'), raw.get('project_id'), raw.get('server_name')
    if scope not in ('project', 'global') or not isinstance(name, str) or (pid is not None and not isinstance(pid, str)):
        raise ActivationError('name the saved server and its reach', 'invalid', 400)
    rec = store.get(scope, pid if scope == 'project' else None, name)
    if not rec:
        raise ActivationError('saved server not found', 'not_found', 404)
    op = rec['operation']
    kind = op['ecosystem']
    if kind not in ('npm', 'github', 'remote'):
        raise ActivationError('this saved setup cannot be edited yet', 'unsupported', 400)
    out = {'ecosystem': kind, 'server_name': name, 'scope': scope, 'project_id': rec.get('project_id'),
           'credentials': [{k: c[k] for k in ('env', 'vault', 'header', 'prefix') if k in c} for c in op['credentials']]}
    if kind == 'remote':
        out.update(url=op['url'], protocol=op['protocol'], auth=op['auth']['type'],
                   issuer=op['auth'].get('issuer') or '', scopes=op['auth'].get('scopes') or [])
    else:
        out.update(package=op['package'], version=op['version'], entry=op.get('entry') or '', args=list(op['args']))
        if kind == 'github':
            out['command'] = op['command']
    return out
