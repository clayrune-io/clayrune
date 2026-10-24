"""Open a media file and verify the opened handle, closing resolve/open races."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import BinaryIO

from mc import desk_file_explorer as explorer, desk_pieces as pieces


def _opened_path(stream: BinaryIO) -> Path:
    if os.name == 'nt':
        import ctypes
        import msvcrt
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        fn = kernel.GetFinalPathNameByHandleW
        fn.argtypes = (wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD)
        fn.restype = wintypes.DWORD
        buffer = ctypes.create_unicode_buffer(32768)
        count = fn(msvcrt.get_osfhandle(stream.fileno()), buffer, len(buffer), 0)
        if not count or count >= len(buffer):
            raise OSError('Could not verify the opened media location.')
        value = buffer.value
        if value.startswith('\\\\?\\UNC\\'):
            value = '\\\\' + value[8:]
        elif value.startswith('\\\\?\\'):
            value = value[4:]
        return Path(value)
    if sys.platform == 'darwin':
        import fcntl
        value = fcntl.fcntl(stream.fileno(), 50, bytes(1024))  # macOS F_GETPATH
        return Path(os.fsdecode(value.split(b'\0', 1)[0]))
    return Path(os.readlink(f'/proc/self/fd/{stream.fileno()}'))


def open_media(root_id: str, relative: str) -> tuple[dict, Path, BinaryIO]:
    root, path = explorer.authorize(root_id, relative)
    stream = path.open('rb')
    try:
        opened = _opened_path(stream)
        # Reauthorize against current registrations AND validate the actual file
        # handle's final name. A swapped junction cannot redirect the read.
        _, current = explorer.authorize(root_id, relative)
        if opened != current or not explorer.project_media._under(opened, root['root']):
            raise pieces.PieceError('That file moved while opening it. Try again.', 409)
        explorer.guard.check_chain(opened, explorer._home)
        a, b = os.fstat(stream.fileno()), current.stat()
        if (a.st_dev, a.st_ino) != (b.st_dev, b.st_ino):
            raise pieces.PieceError('That file changed while opening it. Try again.', 409)
        return root, path, stream
    except BaseException:
        stream.close()
        raise
