"""Studio host-path policy; inspect links themselves before resolving anything."""
from __future__ import annotations

import stat
from pathlib import Path

from mc import desk_pieces as pieces

PROTECTED_DIRS = {
    '.clayrune', '.ssh', '.aws', '.gnupg', '.azure', '.git',
    'browser_profiles', 'browser_profiles_named', 'user data',
    'chrome', 'chromium', 'edge', 'firefox', 'profiles', 'safari',
    'brave-browser', 'bravesoftware', 'opera', 'opera software', 'vivaldi',
    'node_modules', '__pycache__', 'venv', '.venv', 'appdata',
    'application data', 'local settings', 'programdata', 'nethood',
    'printhood', 'recent', 'sendto', 'start menu', 'templates', 'cookies',
}
PROFILE_MARKERS = ('Local State', 'Cookies', 'Login Data',
                   'cookies.sqlite', 'key4.db', 'logins.json')


def secret(name: str) -> bool:
    name = name.casefold()
    return (name.startswith(('.env', 'id_rsa', 'id_ed25519', 'id_dsa'))
            or 'credential' in name or name.endswith(('.key', '.pem', '.p12', '.pfx', '.kdbx'))
            or name in {'secrets.json', 'secrets.toml', 'cookies', 'login data', 'local state'})


def check_parts(path: Path, home: Path | None) -> None:
    for part in path.parts[1:]:
        if secret(part) or part.casefold() in PROTECTED_DIRS or part.startswith('.') or ':' in part:
            raise pieces.PieceError('That location is private and cannot be browsed.', 403)
    if home is not None:
        try:
            relative = path.relative_to(home)
        except ValueError:
            return
        if relative.parts and relative.parts[0].casefold() == 'library':
            raise pieces.PieceError('That location is private and cannot be browsed.', 403)


def check_node(path: Path):
    info = path.lstat()  # stat() follows Windows junctions, hiding their attributes.
    if (stat.S_ISLNK(info.st_mode)
            or getattr(info, 'st_file_attributes', 0) & 0x400
            or (path.parent != path and (getattr(info, 'st_file_attributes', 0) & 6
                or getattr(info, 'st_flags', 0) & getattr(stat, 'UF_HIDDEN', 0x8000)))):
        raise pieces.PieceError('Links and hidden/system locations cannot be browsed.', 403)
    return info


def check_profile(folder: Path) -> None:
    if any((folder / marker).is_file() for marker in PROFILE_MARKERS):
        raise pieces.PieceError('Browser profiles are private and cannot be browsed.', 403)


def check_chain(path: Path, home: Path | None) -> None:
    check_parts(path, home)
    # Walk from the anchor down: never inspect a child through an unchecked link,
    # including a link above a registered root or a mounted/reparse root itself.
    for node in reversed((path, *path.parents)):
        info = check_node(node)
        if stat.S_ISDIR(info.st_mode):
            check_profile(node)
