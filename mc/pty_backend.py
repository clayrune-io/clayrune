"""Cross-platform real-PTY backend for the terminal pop-out (MC-928).

Split out of MC-927: the pop-out's existing "TTY shim"
(mc/blueprints/terminal_routes.py) is a PYTHONPATH sitecustomize injection
that makes child PYTHON processes report isatty()=True. It does nothing for
Node CLIs — `gemini`'s account-picker is an Ink TUI that needs a REAL
pseudo-terminal (raw-mode keyboard input, an ioctl-queryable window size) to
draw at all; a piped non-TTY invocation of it produces zero output before
hanging (verified 2026-08-31, see AgentRuntime.auth_login_argv()).

This module gives terminal_routes.py an actual PTY, opt-in per launch:

- Windows: ConPTY via `pywinpty` (the `winpty` package) — an optional native
  dependency, see requirements.txt. `pty_available()` reports False (rather
  than raising) when it isn't installed, so callers can fail with an
  actionable message instead of an ImportError traceback.
- POSIX: stdlib `pty` + `termios`/`fcntl`/`struct` — no extra dependency.
  UNVERIFIED on a real POSIX box as of 2026-08-31 (this was built and tested
  only on Windows); the pattern (openpty + Popen(preexec_fn=os.setsid) with
  the slave fd as stdio) is the standard recipe used by pexpect/ptyprocess,
  but run a live smoke test on Linux/macOS before relying on it there.

Both backends expose the same narrow interface terminal_routes.py drives:
`.pid`, `.read(size)`, `.write(text)`, `.resize(cols, rows)`, `.isalive()`,
`.wait()`, `.close(force)`. Neither backend is a subprocess.Popen — do not
treat the return value of spawn() as one.
"""

import os
import sys
import signal
import subprocess
from typing import List, Optional, Union


class PtyUnavailable(RuntimeError):
    """Raised by spawn() when a real PTY was requested but the platform
    backend isn't installed (Windows without pywinpty)."""


def pty_available() -> bool:
    """Whether spawn() can succeed on this platform right now."""
    if sys.platform == 'win32':
        try:
            import winpty  # noqa: F401
        except ImportError:
            return False
        return True
    return True  # POSIX: `pty` is stdlib, always present


# ── POSIX backend ────────────────────────────────────────────────────────────


class _PosixPty:
    """openpty() + Popen with the slave fd as stdin/stdout/stderr. The child
    is made its own session/process-group leader (preexec_fn=os.setsid) so
    close() can signal the whole group a `shell=True` command may have
    spawned, not just the shell's own PID."""

    def __init__(self, proc, master_fd):
        self._proc = proc
        self._master_fd = master_fd
        self.pid = proc.pid

    @classmethod
    def spawn(cls, command, cwd=None, env=None, cols=120, rows=30):
        import fcntl
        import pty as _pty
        import struct
        import termios

        master_fd, slave_fd = _pty.openpty()
        # Set the slave's window size BEFORE spawn — many TUIs read it once
        # via ioctl(TIOCGWINSZ) at startup, before any resize could reach them.
        winsize = struct.pack('HHHH', rows, cols, 0, 0)
        fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, winsize)

        try:
            proc = subprocess.Popen(
                command,
                stdin=slave_fd, stdout=slave_fd, stderr=slave_fd,
                cwd=cwd, env=env, shell=True,
                preexec_fn=os.setsid,
                close_fds=True,
            )
        finally:
            # Only the child needs the slave end; leaking it in the parent
            # keeps the pty "open" (no EOF) after the child exits.
            os.close(slave_fd)
        return cls(proc, master_fd)

    def read(self, size: int = 4096) -> str:
        try:
            data = os.read(self._master_fd, size)
        except OSError:
            # EIO on Linux once the slave has no more writers — this IS the
            # child-exited signal on that platform, not an error to surface.
            return ''
        return data.decode('utf-8', errors='replace')

    def write(self, text: str) -> None:
        try:
            os.write(self._master_fd, text.encode('utf-8'))
        except OSError:
            pass

    def resize(self, cols: int, rows: int) -> None:
        import fcntl
        import struct
        import termios
        winsize = struct.pack('HHHH', rows, cols, 0, 0)
        try:
            fcntl.ioctl(self._master_fd, termios.TIOCSWINSZ, winsize)
        except OSError:
            return
        try:
            os.killpg(os.getpgid(self.pid), signal.SIGWINCH)
        except (OSError, ProcessLookupError):
            pass

    def isalive(self) -> bool:
        return self._proc.poll() is None

    def poll(self) -> Optional[int]:
        """subprocess.Popen-shaped non-blocking check — the process tracker
        (mc/blueprints/system_routes.py's /api/processes, list+kill) treats
        every tracked 'proc' this way regardless of backend."""
        return self._proc.poll()

    def kill(self) -> None:
        """subprocess.Popen-shaped hard-kill, used by the same tracker."""
        self.close(force=True)

    def wait(self) -> Optional[int]:
        return self._proc.wait()

    def close(self, force: bool = True) -> None:
        if self._proc.poll() is None:
            sig = signal.SIGKILL if force else signal.SIGTERM
            try:
                os.killpg(os.getpgid(self.pid), sig)
            except (OSError, ProcessLookupError):
                try:
                    self._proc.kill()
                except Exception:
                    pass
            try:
                self._proc.wait(timeout=5)
            except Exception:
                pass
        try:
            os.close(self._master_fd)
        except OSError:
            pass


# ── Windows backend ──────────────────────────────────────────────────────────


class _WinPty:
    """ConPTY via pywinpty's winpty.PtyProcess. `command` is wrapped in
    `cmd.exe /c` so shell operators (&&, |, >) and npm's `.cmd` shims resolve
    exactly as they do under the existing shell=True pipe path — spawn()
    would otherwise exec argv[0] directly with no shell interpreting it."""

    def __init__(self, proc):
        self._proc = proc
        self.pid = proc.pid

    @classmethod
    def spawn(cls, command, cwd=None, env=None, cols=120, rows=30):
        import winpty
        if isinstance(command, (list, tuple)):
            argv = ['cmd.exe', '/c', subprocess.list2cmdline(list(command))]
        else:
            argv = ['cmd.exe', '/c', command]
        proc = winpty.PtyProcess.spawn(
            argv, cwd=cwd, env=env, dimensions=(rows, cols),
        )
        return cls(proc)

    def read(self, size: int = 4096) -> str:
        try:
            return self._proc.read(size)
        except (EOFError, OSError):
            return ''

    def write(self, text: str) -> None:
        try:
            self._proc.write(text)
        except (OSError, ValueError):
            pass

    def resize(self, cols: int, rows: int) -> None:
        try:
            self._proc.setwinsize(rows, cols)
        except Exception:
            pass

    def isalive(self) -> bool:
        return self._proc.isalive()

    def poll(self) -> Optional[int]:
        """subprocess.Popen-shaped non-blocking check (see _PosixPty.poll's
        docstring — same tracker, same contract). Non-blocking: isalive() is
        documented as such, and the exitstatus property just reads a cached
        value from the PTY handle rather than waiting on it."""
        if self._proc.isalive():
            return None
        return self._proc.exitstatus

    def kill(self) -> None:
        self.close(force=True)

    def wait(self) -> Optional[int]:
        self._proc.wait()
        return self._proc.exitstatus

    def close(self, force: bool = True) -> None:
        try:
            self._proc.terminate(force=force)
        except Exception:
            pass
        # Defense in depth: ConPTY groups the session in a job object with
        # kill-on-close semantics, so terminate() above empirically reaps
        # the whole tree (verified 2026-08-31 against a real `gemini` login
        # — cmd.exe -> 2x node.exe -> an npx-spawned MCP server subprocess
        # chain, 7 processes, all gone within ~2s of close()). That job-
        # object cascade is a platform behavior this module doesn't control,
        # so back it with an explicit tree-kill by the PID we started rather
        # than trust it silently — same `taskkill /F /T /PID` shape
        # agent_routes._kill_pid(tree=True) already uses. A harmless no-op
        # once the tree is already gone.
        try:
            subprocess.run(['taskkill', '/F', '/T', '/PID', str(self.pid)],
                            capture_output=True, timeout=10)
        except Exception:
            pass


def spawn(command: Union[str, List[str]], cwd=None, env=None, cols: int = 120,
          rows: int = 30):
    """Spawn `command` (a shell string, same shape terminal_routes.py already
    accepts) behind a real PTY. Raises PtyUnavailable rather than returning
    None so callers can't silently fall through to a half-working session."""
    if sys.platform == 'win32':
        if not pty_available():
            raise PtyUnavailable(
                "pywinpty is not installed — run 'pip install pywinpty' to "
                "enable real-PTY terminal sessions on Windows."
            )
        return _WinPty.spawn(command, cwd=cwd, env=env, cols=cols, rows=rows)
    return _PosixPty.spawn(command, cwd=cwd, env=env, cols=cols, rows=rows)
