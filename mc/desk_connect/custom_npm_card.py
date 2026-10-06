"""What the approval card says about a package's dependencies and install scripts (docs/
DESK_SERVICE_PROFILES_SPEC.md, section 6.2, slice U2b). Pure functions of the operation and the resolved
closure (`custom_npm_closure.resolve`); `custom_connection_service` merges the result into the card.

The card must show exactly what the fingerprint covers, so every dependency is listed with its exact name,
version, sha512 and place, and every install script with its exact body and whether it is approved. An
unticked script is OFF: it is listed so the person can see it exists, and it never runs.
"""
from __future__ import annotations

NO_DEPENDENCIES_NOTE = ('Clayrune downloads this one archive itself, checks its sha512 and unpacks it. No npm, npx or '
                        'install script runs, and nothing it contains runs until an agent session starts the server.')
CLOSURE_NOTE = ('Clayrune resolved every dependency itself, to an exact version, from the package registry, and lists each '
                'with the sha512 of its archive on this card. It downloads exactly those archives, checks each against its '
                'digest and unpacks them into a staging folder, moved into place only when all of them verified. No '
                'npm, npx, .npmrc, lockfile or version range is used, and an install script runs only if you tick it '
                'on this card. Nothing a package contains runs until an agent session starts the server.')
SCRIPTS_NOTE = ('An install script runs the system shell with that package\'s folder as the working directory, a fixed '
                'environment with no Clayrune secrets, and the file and network access of this account. It is not a '
                'sandbox. A script you do not tick is not run.')


def install_section(op: dict, artifact: dict, closure: dict | None) -> dict:
    """The card keys for the install: `install_note`, `install_steps` (the approved scripts, exact),
    and for a package with dependencies or scripts `dependencies`, `dependency_totals`, `scripts`, ..."""
    steps = op['install_steps']
    out = {'install_steps': [dict(s) for s in steps],
           'install_note': CLOSURE_NOTE if op.get('dependencies') else NO_DEPENDENCIES_NOTE}
    if not closure or not (closure['dependencies'] or closure['scripts'] or closure['skipped'] or closure['native_build']):
        return out
    approved = {f'{s["path"] or "."}#{s["script"]}' for s in steps}
    details = closure['details']
    out['dependencies'] = [{'name': d['name'], 'version': d['version'], 'integrity': d['integrity'], 'path': d['path'],
                            **{k: details[d['path']][k] for k in ('size_bytes', 'unpacked_bytes', 'licence', 'deprecated')},
                            'registry_stated_digest': details[d['path']]['registry_stated_digest']}
                           for d in closure['dependencies']]
    out['dependency_totals'] = dict(closure['totals'])
    out['scripts'] = [{k: s[k] for k in ('id', 'path', 'package', 'version', 'script', 'body', 'body_escaped',
                                         'approvable', 'reason')} | {'approved': s['id'] in approved}
                      for s in closure['scripts']]
    out['scripts_note'] = SCRIPTS_NOTE if closure['scripts'] else ''
    out['optional_not_installed'] = [dict(s) for s in closure['skipped']]
    out['native_build_not_run'] = list(closure['native_build'])
    return out


def scripts_not_run(op: dict, artifact: dict, closure: dict | None) -> list[str]:
    """Every declared install script that is not approved: the script name for the package itself, `package script` for a dependency's."""
    approved = {f'{s["path"] or "."}#{s["script"]}' for s in op['install_steps']}
    if closure is None:
        return list(artifact['install_scripts'])
    return [s['script'] if not s['path'] else f'{s["package"]} {s["script"]}' for s in closure['scripts']
            if s['id'] not in approved]


def risks(op: dict, closure: dict | None) -> list[dict]:
    out = []
    if op.get('dependencies'):
        out.append({'code': 'dependencies_installed',
                    'label': f'{len(op["dependencies"])} dependency packages are installed with it, each pinned to the '
                             f'digest shown. They are not reviewed by Clayrune and run with the same access.'})
    if op['install_steps']:
        out.append({'code': 'install_scripts_approved',
                    'label': f'{len(op["install_steps"])} install script(s) you approved run now, at Save, with this '
                             f'account\'s file and network access. Clayrune does not sandbox them.'})
    if closure and closure['native_build']:
        out.append({'code': 'native_build_not_run',
                    'label': 'A package here has a native build step (binding.gyp) that Clayrune does not run. It may '
                             'not start without it.'})
    return out
