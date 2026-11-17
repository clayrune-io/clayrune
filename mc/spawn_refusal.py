"""Which spawn errors prove that no child process was created (MC-1179 AP37).

An exception out of `subprocess.Popen(...)` itself is a refusal by the OS
before any child exists, but only for the errors below. Anything else (a bare
OSError with another code, a handle-cleanup failure) is left unproven, and the
launch transaction keeps treating it as an unknown outcome that is never
retried. Nothing here clears an unknown outcome after the fact.

Measured on Windows (scratch Popen probe, no provider CLI): a missing
executable raises FileNotFoundError, a missing cwd NotADirectoryError, a
directory as the target PermissionError, a non-executable file OSError with
winerror 193. A CLI binary that is half replaced or locked while it updates is
the winerror 193 / PermissionError case.
"""
from __future__ import annotations

import errno

# ERROR_BAD_EXE_FORMAT: CreateProcess refused a file that is not an executable.
_WINERROR_BAD_EXE_FORMAT = 193


def is_prechild_refusal(exc: BaseException) -> bool:
    """True when `exc`, raised by the Popen constructor, proves no child exists."""
    if isinstance(exc, (FileNotFoundError, NotADirectoryError, PermissionError)):
        return True
    if isinstance(exc, OSError):
        return (getattr(exc, 'winerror', None) == _WINERROR_BAD_EXE_FORMAT
                or exc.errno == errno.ENOEXEC)
    return False


class PromptCaptureIncomplete(OSError):
    """The prompt stream was not closed in time, so the prompt may be truncated.

    Raised by the spawn factory before any shim or CLI is launched (MC-1179
    AP32), which is why it carries the same no-child proof as a refused Popen:
    nothing exists to be unsure about, and the caller is told delivery failed.
    """
    _mc_prechild_refusal = True
