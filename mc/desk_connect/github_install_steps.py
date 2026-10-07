"""Dependency steps shown for a staged clone; npm must preserve a reviewed lockfile."""
from pathlib import Path
from mc import mcp_installer as installer


def dependencies(directory: str) -> list[dict]:
    commands = installer.install_commands(directory)
    if installer.detect_install_kind(directory) == 'npm':
        locked = any((Path(directory) / name).is_file() for name in ('package-lock.json', 'npm-shrinkwrap.json'))
        commands = [[('ci' if locked and arg == 'install' else arg) for arg in argv] for argv in commands]
        commands = [argv if '--ignore-scripts' in argv else [*argv, '--ignore-scripts'] for argv in commands]
    return [{'id': f'dependencies:{i}', 'argv': argv} for i, argv in enumerate(commands)]
