"""Restricted Aider operations, installed only in the dedicated child process.

This is an adapter for reviewed Aider calls, not an OS/Python sandbox. Native
optional features are refused rather than relying on Python open auditing.
"""
from __future__ import annotations

import importlib
import inspect
import os
from pathlib import Path
import shlex
import subprocess
import sys
import threading
from types import SimpleNamespace
from typing import Any, Callable

FORMATS = frozenset(('whole', 'diff'))


class AiderRefusal(RuntimeError):
    """Named, pre-execution denial visible even if upstream catches it."""


class Guard:
    def __init__(self, cwd: str):
        from steward.fence import classify_action
        self.cwd = str(Path(cwd).resolve())
        self.classify: Callable[..., Any] = classify_action
        self.refusals: list[str] = []
        self.checks: list[tuple[str, dict, str]] = []
        self._local = threading.local()

    def refuse(self, reason: str) -> None:
        message = 'Aider restricted adapter refused: ' + reason
        self.refusals.append(message)
        print(message, file=sys.stderr, flush=True)
        raise AiderRefusal(message)

    def check(self, tool: str, args: dict, cwd: str | None = None) -> None:
        if getattr(self._local, 'checking', False):
            return
        self._local.checking = True
        try:
            directory = str(Path(cwd or self.cwd).resolve())
            self.checks.append((tool, args, directory))
            decision = self.classify(tool, args, directory, self.cwd)
            if decision.blocked:
                self.refuse(decision.reason)
        except AiderRefusal:
            raise
        except Exception as exc:
            self.refuse(f'steward fence could not classify {tool}: {exc}')
        finally:
            self._local.checking = False

    def write(self, filename: Any) -> None:
        if not isinstance(filename, (str, bytes, os.PathLike)):
            self.refuse('unreviewed file-descriptor write')
        # Resolve aliases before the fence; protect installed adapter/vendor
        # sources too, so an accepted edit cannot rewrite the running guard.
        path = Path(os.fsdecode(filename)).resolve()
        adapter_dir = Path(__file__).resolve().parent
        protected_sources = {'aider_guard.py', 'aider_source_pin.py', 'aider_runner.py',
                             'aider_launch.py', '__init__.py', 'vendor_fence.py'}
        if ((path.parent == adapter_dir and path.name in protected_sources)
                or path.is_relative_to(Path(sys.prefix).resolve())):
            self.refuse(f'editing adapter or interpreter supply chain: {path}')
        self.check('Write', {'file_path': str(path)}, os.getcwd())

    def audit(self, event: str, args: tuple) -> None:
        if getattr(self._local, 'checking', False):
            return
        if event == 'open':
            filename, mode, flags = args
            if ((mode and any(c in mode for c in 'wax+')) or
                    (flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC))):
                # subprocess pipe descriptors are not filename writes.
                if isinstance(filename, int):
                    return
                self.write(filename)
        elif event in ('os.remove', 'os.rmdir', 'os.mkdir', 'os.chmod', 'os.utime',
                       'os.truncate', 'os.rename', 'os.link', 'os.symlink'):
            count = 2 if event in ('os.rename', 'os.link', 'os.symlink') else 1
            for path in args[:count]:
                self.write(path)
            # dir_fd can redirect a relative path; this runner never uses it.
            if event in ('os.remove', 'os.rmdir', 'os.mkdir', 'os.rename'):
                offsets = {'os.mkdir': 2, 'os.rename': 2}
                if any(fd not in (-1, None) for fd in args[offsets.get(event, 1):]):
                    self.refuse(f'unreviewed {event} dir_fd')
        elif event == 'subprocess.Popen':
            if not getattr(self._local, 'process_authorized', False):
                self.refuse('subprocess outside guarded Popen')
        elif event in ('os.system', 'os.exec', 'os.posix_spawn', 'os.fork',
                       'os.forkpty', 'pty.spawn'):
            self.refuse(f'unreviewed process primitive {event}')
        elif event == 'sqlite3.connect':
            self.refuse('native SQLite/repository-map cache')

    def deny(self, feature: str) -> Callable[..., Any]:
        def refused(*args: Any, **kwargs: Any) -> Any:
            self.refuse(feature + ' is outside the reviewed allowlist')
        return refused

    def install(self) -> None:
        """Call once after source verification, before constructing any coder."""
        self._install_process_guard()
        sys.addaudithook(self.audit)
        from aider import commands, coders, utils, voice, editor, scrape
        from aider.coders import base_coder
        from aider.io import InputOutput
        from aider import run_cmd as shell_module

        original_create = coders.Coder.create.__func__

        def create(cls: Any, main_model: Any = None, edit_format: str | None = None,
                   io: Any = None, from_coder: Any = None,
                   summarize_from_coder: bool = True, **kwargs: Any) -> Any:
            fmt = edit_format or getattr(from_coder or main_model, 'edit_format', None)
            if fmt not in FORMATS:
                self.refuse(f'edit format {fmt!r}; only whole and diff are supported')
            # Keep accepted API callers on the same surface as the launcher.
            kwargs.update(map_tokens=0, auto_lint=False, auto_test=False,
                          auto_commits=False, dirty_commits=False,
                          detect_urls=False, auto_copy_context=False,
                          num_cache_warming_pings=0,
                          analytics=SimpleNamespace(event=lambda *a, **kw: None))
            return original_create(cls, main_model=main_model, edit_format=fmt,
                                   io=io, from_coder=from_coder,
                                   summarize_from_coder=summarize_from_coder, **kwargs)

        coders.Coder.create = classmethod(create)
        for cls in coders.__all__:
            if cls is not coders.Coder and getattr(cls, 'edit_format', None) not in FORMATS:
                cls.__init__ = self.deny(f'edit format {getattr(cls, "edit_format", None)!r}')
                cls.apply_edits = self.deny(f'edit format {getattr(cls, "edit_format", None)!r}')
        for name in dir(commands.Commands):
            if name.startswith('cmd_') and name != 'cmd_run':
                setattr(commands.Commands, name, self.deny('/' + name[4:].replace('_', '-')))

        # Match command names exactly: upstream accepts slash abbreviations.
        original_preproc = base_coder.Coder.preproc_user_input

        def preproc(coder: Any, text: str) -> Any:
            if not text:
                return original_preproc(coder, text)
            stripped = text.strip()
            if stripped.startswith('/') and stripped.split(maxsplit=1)[0] != '/run':
                self.refuse(f'command {stripped.split(maxsplit=1)[0]} is not /run')
            if stripped.startswith('!'):
                self.refuse('! shell alias; use /run')
            return original_preproc(coder, text)

        base_coder.Coder.preproc_user_input = preproc
        original_commands_run = commands.Commands.run

        def dispatch(command_set: Any, text: str) -> Any:
            if not text.strip() or text.strip().split(maxsplit=1)[0] != '/run':
                self.refuse(f'command {text.split(maxsplit=1)[0]!r} is not /run')
            return original_commands_run(command_set, text.strip())

        commands.Commands.run = dispatch

        def run(command: str, verbose: bool = False, error_print: Any = None,
                cwd: str | None = None) -> tuple[int, str]:
            self.check('Bash', {'command': command}, cwd)
            result = subprocess.run(command, shell=True, cwd=cwd or self.cwd,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, encoding='utf-8', errors='replace')
            print(result.stdout, end='', flush=True)
            return result.returncode, result.stdout

        shell_module.run_cmd = run
        shell_module.run_cmd_subprocess = run
        shell_module.run_cmd_pexpect = self.deny('POSIX pexpect branch')
        base_coder.run_cmd = commands.run_cmd = run
        # Block native/optional entrypoints even when called directly.
        voice.Voice.__init__ = self.deny('/voice native audio')
        voice.Voice.raw_record_and_transcribe = self.deny('/voice native audio')
        scrape.Scraper.__init__ = self.deny('/web browser/document conversion')
        editor.pipe_editor = commands.pipe_editor = self.deny('external editor')
        InputOutput.offer_url = self.deny('browser/GUI URL opening')
        for module, names in (
            (utils, ('check_pip_install_extra', 'run_install')),
            (scrape, ('install_playwright',)),
            (commands, ('install_playwright', 'install_help_extra')),
        ):
            for name in names:
                if not hasattr(module, name):
                    self.refuse(f'audited internal-shape drift: {module.__name__}.{name}')
                setattr(module, name, self.deny('installer ' + name))
        import webbrowser
        webbrowser.open = webbrowser.open_new = webbrowser.open_new_tab = self.deny('browser/GUI')
        # No general CLI, RepoMap, analytics, history, notifications or caches
        # are constructed by the runner. Disable model metadata cache IO too.
        models = importlib.import_module('aider.models')
        models.ModelInfoManager._load_cache = lambda manager: None
        models.ModelInfoManager._update_cache = lambda manager: None
        models.OpenRouterModelManager.get_model_info = self.deny('OpenRouter metadata cache')
        models.ModelInfoManager.fetch_openrouter_model_info = self.deny('OpenRouter web metadata')
        setattr(models, 'check_pip_install_extra', self.deny('installer check_pip_install_extra'))

    def _install_process_guard(self) -> None:
        original_popen = subprocess.Popen
        popen_signature = inspect.signature(original_popen)
        guard = self

        class GuardedPopen(original_popen):
            def __init__(self, *args: Any, **kwargs: Any):
                bound = popen_signature.bind(*args, **kwargs).arguments
                argv = bound['args']
                if bound.get('executable'):
                    guard.refuse('custom subprocess executable')
                child_env = bound.get('env')
                # GitPython's audited safer_popen adds only these hardening /
                # output-locale values. No PATH, shell or git-config override.
                changed = []
                if child_env is not None:
                    parent = {k.upper() if os.name == 'nt' else k: v for k, v in os.environ.items()}
                    child = {k.upper() if os.name == 'nt' else k: v for k, v in child_env.items()}
                    for key in set(parent) | set(child):
                        if parent.get(key) == child.get(key):
                            continue
                        if key in ('LANGUAGE', 'LC_ALL') and child.get(key) == 'C':
                            continue
                        # GitPython temporarily adds this to the parent after
                        # making the child's env copy (shell=False).
                        if key == 'NODEFAULTCURRENTDIRECTORYINEXEPATH' and child.get(key) in (None, '1'):
                            continue
                        changed.append(key)
                if changed or bound.get('preexec_fn') is not None:
                    guard.refuse('custom subprocess environment/preexec_fn: ' + ', '.join(changed))
                if isinstance(argv, str):
                    command = argv
                elif isinstance(argv, (list, tuple)) and all(isinstance(t, str) for t in argv):
                    if bound.get('shell'):
                        guard.refuse('shell=True with argv list')
                    command = (subprocess.list2cmdline(argv) if os.name == 'nt'
                               else shlex.join(argv))
                else:
                    guard.refuse('unreviewed subprocess argv')
                    return
                guard.check('Bash', {'command': command}, bound.get('cwd'))
                guard._local.process_authorized = True
                try:
                    super().__init__(*args, **kwargs)
                finally:
                    guard._local.process_authorized = False

        subprocess.Popen = GuardedPopen
