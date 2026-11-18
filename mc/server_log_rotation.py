"""Size-bounded `data/logs/clayrune.log` (MC-1213).

THE PROBLEM. The server's stdout/stderr is handed to it as an open file
(`_redirect_restart_child_output` opens `clayrune.log` 'ab' and passes it to the
restarted child; `installer/start.bat` does the same with `>>`). Nothing ever
bounded it: 526 MB after 3.7 months, 92 percent of it Werkzeug access lines.

WHY A FILE HANDED IN FROM OUTSIDE CANNOT BE RENAMED. On Windows a file cannot be
renamed while any handle to it is open without FILE_SHARE_DELETE, and neither the
inherited handle nor Python's `open()` has it. So the server takes the file over
at boot: it points fd 1 and fd 2 at NUL (which releases the inherited handle),
opens the file itself, and points fd 1/2 at that.

THAT IS NOT ENOUGH ON A VENV (measured 2026-11-18, live server). A Windows venv's
`.venv/Scripts/python.exe` is a redirector: it starts the base interpreter as a
child, hands it its own std handles, and waits for it. Every launch path goes
through it (start.bat's `python` after activate, the relaunch route's
`sys.executable`), so the redirector, a process this one does not own, holds the
log open for the server's whole life. psutil on the live box: holders were
52720 (server, base pythoncore-3.14) and 61808 (its parent, the venv
redirector); every rename failed with WinError 32 and the file reached 530 MB.

So when the rename fails, the file is COPIED to its target and then truncated
to zero through this process's own handle (copy-truncate). That needs only
write access, which the share mode already grants, so a foreign holder cannot
block it. A foreign holder that WRITES would keep its own file position and
could leave a hole of zeros after a truncate; the redirector never writes.

WHAT IS CAPTURED. `sys.stdout` / `sys.stderr` are replaced by line-buffered
streams that write through one lock, and fd 1/2 are left on the same file so a
child process that inherits them, or a C-level write, still lands in the log.

THE BOUND. Before a write, an active file at `max_bytes` is renamed to `<name>.1`,
older generations shift up (`.1` -> `.2` ...), and the generation past `generations`
is dropped. Disk use is at most `(generations + 1) * max_bytes`.

A FILE FROM BEFORE THE BOUND IS NOT ROTATED AWAY. At boot a file already at
`max_bytes` was written before this module existed (rotation keeps the active
file under the bound). It is renamed once to `<stem>.pre-cap<suffix>` (then
`.pre-cap-2`, ...), a name rotation never writes to, and the rename is announced
in the new file. Removing it is the operator's call. Same rule as
`mc/memory_push_log.py`.

Never raises. A rotation that fails both ways (a read-only directory, a full
disk) is announced once, the process keeps appending past the
bound, and the rotation is retried after `RETRY_SECONDS`. Losing the bound for a
while is cheaper than losing the log.

WHICH FILES. `install_std_logs(log_dir)` is what `boot()` calls. It finds the
file behind fd 1 and the file behind fd 2 by identity (device + inode against
the regular files directly under `log_dir`, never by name) and bounds each one.
That covers `clayrune.log` (restart child, start.bat) and the two files
`tools/clayrune-autostart.ps1` redirects to, `clayrune-autostart-server.log` and
`clayrune-autostart-server.err.log`, which are cleared only at each launch and
used to grow for the weeks a server stays up. Two different files get two
independent `RotatingLog`s; one file behind both fds gets one. Nothing outside
`log_dir` is looked at, opened or renamed.

`install()` / `install_std_logs()` do nothing unless fd 1 or fd 2 already IS a
file under the log dir, so a foreground console run and the test suite are
untouched.

Leaf module: imports nothing from `mc`.
"""
from __future__ import annotations

import io as _io
import os as _os
import shutil as _shutil
import stat as _stat
import sys as _sys
import threading as _threading
import time as _time
from pathlib import Path as _Path

# Active file at 25 MB; the active file plus 4 older generations is at most
# 125 MB. The reviewed install wrote about 4.5 MB a day, so one generation is
# about 5 days and the set is about 25 days of history, and every file is small
# enough to grep in a second or two.
MAX_BYTES = 25 * 1024 * 1024
GENERATIONS = 4

# After a failed rotation, do not try again for this long.
RETRY_SECONDS = 30.0

_installed: list = []  # the RotatingLogs `install*()` created, kept alive for the process


def _fmt(size: int) -> str:
    return f'{size // (1024 * 1024)} MB' if size >= 1024 * 1024 else f'{size // 1024} KB'


def _pre_cap_target(path: _Path) -> _Path:
    base = path.with_name(f'{path.stem}.pre-cap{path.suffix}')
    n = 2
    while base.exists():
        base = path.with_name(f'{path.stem}.pre-cap-{n}{path.suffix}')
        n += 1
    return base


class RotatingLog:
    """Owns the log file and (optionally) fd 1/2 pointed at it. Thread-safe."""

    def __init__(self, path, max_bytes: int = MAX_BYTES,
                 generations: int = GENERATIONS, clock=_time.monotonic):
        self.path = _Path(path)
        self.max_bytes = int(max_bytes)
        self.generations = max(1, int(generations))
        self._clock = clock
        self._lock = _threading.RLock()
        self._fd = -1
        self._std_fds: list = []      # fds (1, 2) kept pointed at the file
        self._nul_fd = -1
        self._next_try = 0.0
        self._warned = False
        self._legacy_pending = False  # a file from before the bound still to be kept
        self._notes: list = []        # announcements waiting for a file to land in

    # -- open / close -------------------------------------------------------
    def _open_active(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fd = _os.open(str(self.path),
                            _os.O_WRONLY | _os.O_APPEND | _os.O_CREAT
                            | getattr(_os, 'O_BINARY', 0), 0o644)
        for std in self._std_fds:
            _os.dup2(self._fd, std)

    def _release_active(self) -> None:
        """Drop every handle this process holds on the file (fd 1/2 go to NUL)."""
        if self._std_fds and self._nul_fd >= 0:
            for std in self._std_fds:
                _os.dup2(self._nul_fd, std)
        if self._fd >= 0:
            try:
                _os.close(self._fd)
            finally:
                self._fd = -1

    def open(self, std_fds=()) -> None:
        """Open the file for appending, first moving a file written before the
        bound out of the way. `std_fds` are repointed at the file."""
        with self._lock:
            self._std_fds = list(std_fds)
            if self._std_fds and self._nul_fd < 0:
                self._nul_fd = _os.open(_os.devnull, _os.O_RDWR
                                        | getattr(_os, 'O_BINARY', 0))
            if self._std_fds:
                # Release the inherited handle so the file can be renamed.
                for std in self._std_fds:
                    _os.dup2(self._nul_fd, std)
            try:
                size = _os.path.getsize(self.path)
            except OSError:
                size = 0
            if size >= self.max_bytes:
                try:
                    self._note(self._shift_files(size, pre_cap=True))
                except OSError as e:
                    self._legacy_pending = True
                    self._next_try = self._clock() + RETRY_SECONDS
                    self._announce_failure(size, e)
            if self._fd < 0:
                self._open_active()
            self._flush_notes()

    def close(self) -> None:
        with self._lock:
            try:
                self._release_active()
            except OSError:
                pass

    # -- rotation -----------------------------------------------------------
    def _note(self, text: str) -> None:
        self._notes.append(text)

    def _flush_notes(self) -> None:
        notes, self._notes = self._notes, []
        for t in notes:
            self._write_raw((t + '\n').encode('utf-8', 'backslashreplace'))

    def _shift_files(self, size: int, pre_cap: bool) -> str:
        """Move the active file out of the way (no handle of ours is open).
        Returns the announcement. Raises OSError, leaving the active file in
        place, when neither a rename nor a copy-truncate works. A copy-truncate
        leaves the active file open; a rename leaves it closed."""
        if pre_cap:
            target = _pre_cap_target(self.path)
            how = self._move_active(target)
            return (f"[log-rotate] {self.path.name} was {_fmt(size)}, written "
                    f"before the size bound; kept as {target.name} (not deleted)"
                    f"{how} and a new log started")
        oldest = self.path.with_name(f'{self.path.name}.{self.generations}')
        if oldest.exists():
            _os.remove(oldest)
        for n in range(self.generations - 1, 0, -1):
            src = self.path.with_name(f'{self.path.name}.{n}')
            if src.exists():
                _os.replace(src, self.path.with_name(f'{self.path.name}.{n + 1}'))
        how = self._move_active(self.path.with_name(f'{self.path.name}.1'))
        return (f"[log-rotate] {self.path.name} reached {_fmt(size)}; previous "
                f"kept as {self.path.name}.1 ({self.generations} generations "
                f"kept){how}")

    def _move_active(self, target: _Path) -> str:
        """Move the active file's contents to `target`, leaving an empty active
        file open (or none: the caller opens it). Called with the lock held and
        none of our handles open. Returns '' for a rename, else a note saying
        it was a copy-truncate. Raises OSError,
        with the active file in place and `target` absent, when both ways fail.
        """
        try:
            _os.replace(self.path, target)
            return ''
        except OSError as e:
            rename_err = e
        # A process we do not own holds the file (the venv redirector, see the
        # module docstring). Copy, then truncate through our own handle.
        self._open_active()
        try:
            self._copy_truncate(target)
        except OSError as e:
            self._release_active()
            raise OSError(f"rename: {rename_err}; copy-truncate: {e}") from e
        return ('; copied then truncated, because another process holds the '
                'file open (rename failed)')

    def _copy_truncate(self, target: _Path) -> None:
        """Copy the active file to `target`, then empty it. `self._fd` is open.

        Reading runs to EOF, so bytes a child or a C-level write appended
        during the copy are taken too; only a write landing between the last
        read and the truncate is lost. A partial `target` is removed."""
        try:
            with open(self.path, 'rb') as src, open(target, 'xb') as dst:
                _shutil.copyfileobj(src, dst, 1024 * 1024)
                dst.flush()
                _os.fsync(dst.fileno())
            _os.ftruncate(self._fd, 0)
            # fd 1/2 are duplicates sharing this file position: reset it, or a
            # raw write through them lands at the old offset behind a hole.
            _os.lseek(self._fd, 0, _os.SEEK_SET)
        except OSError:
            try:
                _os.remove(target)
            except OSError:
                pass
            raise

    def _announce_failure(self, size: int, err: Exception) -> None:
        # Once per process. Printing on every retry would itself flood the log.
        if self._warned:
            return
        self._warned = True
        self._note(f"[log-rotate] could not rotate {self.path.name} at "
                   f"{_fmt(size)} ({err}); appending past the bound, "
                   f"retrying every {int(RETRY_SECONDS)}s")

    def _rotate(self, size: int) -> None:
        """Close, shift generations, reopen. Called with the lock held.

        A file more than twice the bound cannot have been written under it (a
        rotation that kept failing is the only other way), and neither can the
        file whose boot-time move failed. Both are kept, not shifted into the
        generation chain where they would eventually be dropped."""
        self._release_active()
        err = None
        try:
            self._note(self._shift_files(
                size, pre_cap=self._legacy_pending or size > 2 * self.max_bytes))
            self._legacy_pending = False
        except OSError as e:
            err = e
        # Reopen whether or not the move worked: the log must keep landing.
        # (A copy-truncate already left it open.)
        if self._fd < 0:
            self._open_active()
        if err is not None:
            self._next_try = self._clock() + RETRY_SECONDS
            self._announce_failure(size, err)
        self._flush_notes()

    def _rotate_if_full(self) -> None:
        if self._clock() < self._next_try:
            return
        try:
            size = _os.fstat(self._fd).st_size
        except OSError:
            return
        if size >= self.max_bytes:
            self._rotate(size)

    # -- writing ------------------------------------------------------------
    def _write_raw(self, data: bytes) -> None:
        view = memoryview(data)
        while view:
            n = _os.write(self._fd, view)
            view = view[n:]

    def write(self, data: bytes) -> None:
        """Append `data`, rotating first when the file is at the bound.
        Never raises."""
        if not data:
            return
        try:
            with self._lock:
                if self._fd < 0:
                    return
                self._rotate_if_full()
                self._write_raw(data)
        except Exception:
            pass


class _BinaryFacade:
    """`sys.stdout.buffer` for the rare library that writes bytes to it."""

    def __init__(self, log: RotatingLog):
        self._log = log

    def write(self, b) -> int:
        self._log.write(bytes(b))
        return len(b)

    def flush(self) -> None:
        pass


class LogStream(_io.TextIOBase):
    """A `sys.stdout` / `sys.stderr` stand-in that writes whole lines.

    `print(text)` is two `write` calls (text, then '\\n'). Text is held until a
    newline so a rotation can fall between lines but never inside one;
    `flush()` releases a held partial line."""

    encoding = 'utf-8'
    errors = 'backslashreplace'

    def __init__(self, log: RotatingLog, fd: int):
        super().__init__()
        self._log = log
        self._fd_number = fd
        self._pending = ''
        self._plock = _threading.Lock()
        self.buffer = _BinaryFacade(log)

    def writable(self) -> bool:
        return True

    def isatty(self) -> bool:
        return False

    def fileno(self) -> int:
        return self._fd_number

    def write(self, s) -> int:  # type: ignore[override]
        if not isinstance(s, str):
            s = str(s)
        with self._plock:
            self._pending += s
            cut = self._pending.rfind('\n')
            if cut < 0:
                return len(s)
            out, self._pending = self._pending[:cut + 1], self._pending[cut + 1:]
        self._log.write(out.encode('utf-8', 'backslashreplace'))
        return len(s)

    def flush(self) -> None:
        with self._plock:
            out, self._pending = self._pending, ''
        if out:
            self._log.write(out.encode('utf-8', 'backslashreplace'))


def _is_same_file(fd: int, path: _Path) -> bool:
    try:
        a = _os.fstat(fd)
        b = _os.stat(path)
    except OSError:
        return False
    return a.st_ino != 0 and (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino)


def _file_behind(fd: int, log_dir: _Path):
    """The regular file directly under `log_dir` that `fd` is open on, or None.

    Matched by identity, not by name: the launcher chose the name, we only know
    the directory. Only `log_dir` itself is listed."""
    try:
        if not _stat.S_ISREG(_os.fstat(fd).st_mode):
            return None
        with _os.scandir(log_dir) as it:
            names = sorted(e.name for e in it)
    except OSError:
        return None
    for name in names:
        cand = log_dir / name
        if _is_same_file(fd, cand):
            return cand
    return None


def _flush_std() -> None:
    for stream in (_sys.stdout, _sys.stderr):
        try:
            stream.flush()
        except Exception:
            pass


def _take_over(path: _Path, std: list, max_bytes: int, generations: int):
    """Point `std` (fd numbers that are already `path`) at a RotatingLog for it
    and swap the matching `sys.stdout`/`sys.stderr`. Returns the RotatingLog, or
    None on failure (output is left on the file, never on NUL)."""
    rl = RotatingLog(path, max_bytes, generations)
    try:
        rl.open(std_fds=std)
    except Exception:
        # Output must not stay on NUL because the takeover failed halfway.
        try:
            rl._open_active()
        except Exception:
            pass
        return None
    if 1 in std:
        _sys.stdout = LogStream(rl, 1)
    if 2 in std:
        _sys.stderr = LogStream(rl, 2)
    _installed.append(rl)
    return rl


def install_std_logs(log_dir, max_bytes: int = MAX_BYTES,
                     generations: int = GENERATIONS) -> list:
    """Bound whichever file(s) under `log_dir` stdout/stderr are redirected to.

    Returns the `RotatingLog`s created (empty when neither fd is a file directly
    under `log_dir`). Never raises."""
    try:
        directory = _Path(log_dir)
        groups: dict = {}                       # file path -> [fd numbers]
        for fd in (1, 2):
            found = _file_behind(fd, directory)
            if found is not None:
                groups.setdefault(found, []).append(fd)
        if not groups:
            return []
        _flush_std()
        made = []
        for path, std in groups.items():
            try:
                rl = _take_over(path, std, max_bytes, generations)
            except Exception:
                rl = None
            if rl is not None:
                made.append(rl)
        return made
    except Exception:
        return []


def install(log_path, max_bytes: int = MAX_BYTES, generations: int = GENERATIONS):
    """Take over stdout/stderr IF they are the one file `log_path`; else do nothing.

    Returns the `RotatingLog`, or None when nothing was taken over. Never raises.
    """
    try:
        path = _Path(log_path)
        std = [fd for fd in (1, 2) if _is_same_file(fd, path)]
        if not std:
            return None
        _flush_std()
        return _take_over(path, std, max_bytes, generations)
    except Exception:
        return None
