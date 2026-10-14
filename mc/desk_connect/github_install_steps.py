"""Dependency steps shown for a staged clone; npm must preserve a reviewed lockfile."""
from pathlib import Path
from mc import mcp_installer as installer
from mc.desk_connect.mcp_errors import ActivationError


def _script_order(name: str) -> tuple[int, str] | None:
    lifecycle = ('preinstall', 'install', 'postinstall', 'prepare')
    if name in lifecycle:
        return lifecycle.index(name), name
    for rank, prefix in ((4, 'build'), (5, 'compile')):
        if name == prefix or name.startswith(prefix + ':'):
            return rank, '' if name == prefix else name
    return None


def declared_scripts(scripts: object) -> list[dict]:
    """Offer only setup scripts, in lifecycle order, without executing any body."""
    if not isinstance(scripts, dict):
        raise ActivationError('The declared install steps could not be read.', 'bad_scripts', 400)
    steps = []
    for name, body in scripts.items():
        if not isinstance(name, str) or _script_order(name) is None:
            continue
        if not isinstance(body, str) or len(body) > 10000:
            raise ActivationError('An install step cannot be shown completely.', 'bad_scripts', 400)
        steps.append({'id': f'script:{name}', 'argv': [], 'body': body, 'script': name})
    return sorted(steps, key=lambda step: _script_order(step['script']) or (6, ''))


def require_selected(op: dict) -> None:
    selected = {step['id'] for step in op['install_steps']}
    if set(op.get('required_steps', [])) - selected:
        raise ActivationError('Select the required build step in Review before saving.',
                              'required_install_step', 400)


def dependencies(directory: str) -> list[dict]:
    commands = installer.install_commands(directory)
    if installer.detect_install_kind(directory) == 'npm':
        locked = any((Path(directory) / name).is_file() for name in ('package-lock.json', 'npm-shrinkwrap.json'))
        commands = [[('ci' if locked and arg == 'install' else arg) for arg in argv] for argv in commands]
        commands = [argv if '--ignore-scripts' in argv else [*argv, '--ignore-scripts'] for argv in commands]
    return [{'id': f'dependencies:{i}', 'argv': argv} for i, argv in enumerate(commands)]
