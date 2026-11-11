"""Offline operator key-file boundaries shared by keygen and the manifest signer."""
from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def key_path_in_checkout(path: Path) -> bool:
    resolved = path.expanduser().resolve()
    if resolved == REPO_ROOT or REPO_ROOT in resolved.parents:
        return True
    # Both normal checkouts (.git directory) and worktrees (.git file).
    return any((parent / '.git').exists() for parent in (resolved, *resolved.parents))


def _windows_private_fd(path: Path) -> int:
    """CREATE_NEW with a protected owner-only DACL, before any bytes exist."""
    import ctypes
    import msvcrt
    from ctypes import wintypes

    class SecurityAttributes(ctypes.Structure):
        _fields_ = [('length', wintypes.DWORD), ('descriptor', ctypes.c_void_p),
                    ('inherit', wintypes.BOOL)]

    advapi = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    convert = advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW
    convert.argtypes = [wintypes.LPCWSTR, wintypes.DWORD,
                        ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
    convert.restype = wintypes.BOOL
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                       ctypes.POINTER(SecurityAttributes), wintypes.DWORD,
                       wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    descriptor = ctypes.c_void_p()
    # OW = OWNER RIGHTS, not a guessed username; P blocks inherited permissions.
    if not convert('D:P(A;;FA;;;OW)', 1, ctypes.byref(descriptor), None):
        raise OSError('could not construct private key permissions')
    try:
        attrs = SecurityAttributes(ctypes.sizeof(SecurityAttributes), descriptor, False)
        handle = create(str(path), 0x40000000, 0, ctypes.byref(attrs), 1, 0x80, None)
        if handle == ctypes.c_void_p(-1).value:
            code = ctypes.get_last_error()
            if code in (80, 183):
                raise FileExistsError('key file already exists')
            raise OSError('could not create private key file')
        try:
            return msvcrt.open_osfhandle(handle, os.O_WRONLY | os.O_BINARY)
        except BaseException:
            kernel.CloseHandle(handle)
            raise
    finally:
        kernel.LocalFree(descriptor)


def write_new_private_file(path: Path, data: bytes) -> None:
    """Exclusive creation; private from birth on POSIX and Windows. Never replaces."""
    fd = (_windows_private_fd(path) if os.name == 'nt' else
          os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
    with os.fdopen(fd, 'wb') as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
