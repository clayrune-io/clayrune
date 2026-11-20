"""MC-1171: say so when a test run's pass or fail is hidden from Clayrune.

An agent ran `pytest ... | tail -15` inside a job script. The job reported
exit 0, which was tail's exit, and nothing told the agent or the user that
pytest's own verdict never arrived. This module answers one question about a
shell command, `classify()`, and turns a "masked" answer into one chat status
line. It only reports: it never rewrites, blocks or reruns a command and never
changes an exit code.

`classify(command, shell)` returns a `Verdict` whose `state` is one of:

  not_test  no simple command in it is a recognised test runner
  clean     it starts a recognised runner and the command's exit status is
            the runner's
  masked    it starts a recognised runner and the exit status is not the
            runner's
  unknown   the shape is not recognised, or the shells that could be running
            it disagree. Unknown is never reported as clean and never gets a
            status line.

A runner is whatever `mc.test_run_detection.candidate()` recognises (pytest,
`python -m pytest`, `node --test`, a node test/smoke script, `npm test`,
`npm run test`, `npx playwright test`, the pnpm/yarn forms), looked for at
the head of every simple command, after these prefixes: `VAR=value`, `uv run`
and `poetry run` in any shell; `env`, `time`, `nohup`, `command`, `exec`,
`timeout N` in POSIX; `call` and a leading `@` in cmd; `&` and `.` in
PowerShell.

POSIX rules (bash, sh). Words are split with `shlex`; the operator split is a
quote-aware scan in front of it, because `shlex` returns `"|"` and `|` as the
same token. Each rule with its example:

  M1 piped       `pytest tests | tail -15`. The runner's output goes into
                 another command and no pipefail is in effect, so the exit
                 status is the last command's. In effect means `set -o
                 pipefail` (or `set -euo pipefail`) earlier in the same
                 command, `bash -o pipefail -c "..."`, or `pipefail=True`
                 from the caller. Then `pytest | tail` is clean.
  M2 followed    `pytest; echo done`, `pytest; true`, or a later line of a
                 script. Another command runs after the runner, so the exit
                 status is that command's.
  M3 or_fallback `pytest || true`, `pytest || echo failed`. The fallback's
                 status replaces a failing one.
  M4 script_unreadable  `bash run_tests.sh` when the script cannot be read
                 and its file name says it runs tests (the same name rule
                 `candidate()` uses for node scripts). Any other unreadable
                 script is unknown.
  C1 `a && pytest`, `cd x && pytest`, `pytest && echo ok`: a failing status
     passes through `&&`.
  C2 `pytest > out.log 2>&1` as the last command: redirection keeps the
     status.
  C3 the launcher, `python tools/test-run.py ...`, in any position, piped or
     not: it reports the real exit code itself. Its `--command` string is
     not inspected.
  C4 `pytest || exit 1`, `pytest; exit $?`, and `set -e; pytest; echo done`
     when the runner is the last command of its `&&`/`||` list: the failing
     status still ends the command.
  C5 `pytest -k "a|b"`: a quoted `|` is an argument, not a pipe.
  U1 `pytest; rc=$?; tail log; exit $rc`: a later `$?`, or `PIPESTATUS`
     after a pipe, means the status is handled by hand. Not judged.
  U2 `if pytest; then ...`, `( pytest )`, `! pytest`, a function body, a
     loop: inside control flow only M1 is decided (`for f in a b; do pytest
     $f | tail -1; done` is masked); everything else there is unknown.
  U3 `pytest &`, `echo "$(pytest)"`, an unterminated quote, a command over
     32768 characters, nesting deeper than 3.
  Inline scripts are read through: `bash -c "..."`, `sh -c '...'`, `eval`,
  a here-document fed to `bash`/`sh`, `$(...)` and backticks. A here-document
  fed to anything else (`python - <<'PY'`, `cat <<EOF`) is data. A script
  file (`bash x.sh`, `./x.sh`, `source x.sh`, `x.ps1`, `x.bat`) is read
  through `read_script` when the caller supplies one.

PowerShell. Recognised: statements split on `;` and newlines, `|`, `&&` and
`||` (PowerShell 7; 5.1 refuses them outright, which hides nothing), single
and double quotes, backtick escapes, `#` comments, the call operator `&`.
  masked: `pytest | Select-Object -Last 15` (PowerShell has no pipefail);
          `pytest; Write-Host done`.
  clean:  one runner statement, also after `cd x;`, with `>`/`*>`
          redirection; `pytest; exit $LASTEXITCODE`.
  unknown: any later `$LASTEXITCODE` or `$?`, a pipe when `$LASTEXITCODE`
          is mentioned, and every command that contains `( ) { }`, a
          here-string, a background `&`, or a statement that starts with
          if/for/foreach/while/do/switch/try/function/filter/param/begin/trap.
          Inside those only a piped runner is decided.

cmd. Recognised: `|`, `&&`, `||`, `&` and newlines as sequence, double
quotes, `^` escapes, `rem` and `::` lines.
  masked: `pytest | more`; `pytest & echo done`; `pytest || echo failed`.
  clean:  `cd tests && pytest`; `pytest > out.txt 2>&1`;
          `pytest || exit /b 1`; `pytest & exit /b %errorlevel%`.
  unknown: any later `errorlevel`, parentheses, `if`, `for`, `goto`, a
          `:label`. Inside those only a piped runner is decided.

Shell not known (`shell=None`): the command is judged as all three shells
and the answer is used only when they agree (`pytest | tail -15` is masked
in each). Otherwise it is unknown, reason `shell_unknown`.

Not covered: a runner started by a program the detector does not read (a
Python or Node script that spawns pytest, a Makefile target, an alias, a
shell function, `$PY -m pytest`). Those are not_test.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
import json
import os
from pathlib import Path
import re
import shlex
from typing import Any, Callable

from mc import state
from mc.core import _log
from mc.test_run_detection import MAX_COMMAND, SHELL_TOOLS, candidate

SETTING = 'masked_test_verdict_enabled'
LABEL = '[test verdict]'
MAX_SCRIPT = 256 * 1024
MAX_DEPTH = 3
MAX_SEEN = 64
_SEEN_KEY = '_masked_verdict_seen'

NOT_TEST, CLEAN, MASKED, UNKNOWN = 'not_test', 'clean', 'masked', 'unknown'

ScriptReader = Callable[[str], 'str | None']


@dataclass(frozen=True)
class Verdict:
    state: str
    reason: str = ''
    detail: str = ''   # a bare command or file name, never arguments
    script: str = ''   # file name of the script the finding is in, if any

    @property
    def masked(self) -> bool:
        return self.state == MASKED

    @property
    def is_test_run(self) -> bool | None:
        return None if self.state == UNKNOWN else self.state != NOT_TEST


@dataclass(frozen=True)
class _Dialect:
    name: str
    escape: str
    quotes: str
    redirect: re.Pattern[str]
    failing_exit: re.Pattern[str]     # after `||`: still ends nonzero
    passthrough: re.Pattern[str]      # next command hands the status on
    status_words: tuple[str, ...]     # the status is handled by hand
    pipe_status_words: tuple[str, ...]
    keywords: frozenset[str]          # statements that are not looked into
    opaque: bool                      # grouping is not looked into at all


_DIALECTS = {
    'posix': _Dialect(
        'posix', '\\', '\'"', re.compile(r'<<<|<>|>>|>&|<&|>\||>|<'),
        re.compile(r'(?:exit|return)(?: (?:[1-9]\d*|"?\$\?"?))?|false'),
        re.compile(r'exit(?: "?\$\?"?)?'),
        ('$?', 'pipestatus'), ('pipestatus',), frozenset(), False),
    'powershell': _Dialect(
        'powershell', '`', '\'"', re.compile(r'>>|>&|<&|>|<'),
        re.compile(r'exit (?:[1-9]\d*|\$lastexitcode)'),
        re.compile(r'exit \$lastexitcode'),
        ('$lastexitcode', '$?'), ('$lastexitcode',),
        frozenset({'if', 'for', 'foreach', 'while', 'do', 'switch', 'try',
                   'function', 'filter', 'param', 'begin', 'trap'}), True),
    'cmd': _Dialect(
        'cmd', '^', '"', re.compile(r'>>|>&|<&|>|<'),
        re.compile(r'exit(?: /b)? (?:[1-9]\d*|%errorlevel%)'),
        re.compile(r'exit(?: /b)? %errorlevel%'),
        ('errorlevel',), (), frozenset({'if', 'for', 'goto'}), True),
}
_SHELL_KINDS = {'posix': 'posix', 'bash': 'posix', 'sh': 'posix', 'zsh': 'posix',
                'powershell': 'powershell', 'pwsh': 'powershell', 'cmd': 'cmd'}
_SHELL_EXES = {**_SHELL_KINDS, 'dash': 'posix', 'ksh': 'posix'}
_SCRIPT_EXTS = {'.sh': 'posix', '.bash': 'posix', '.ps1': 'powershell',
                '.bat': 'cmd', '.cmd': 'cmd'}
_PS_VALUE_OPTS = ('-executionpolicy', '-ep', '-windowstyle', '-workingdirectory',
                  '-wd', '-outputformat', '-inputformat', '-configurationname',
                  '-settingsfile', '-version')
_POSIX_OPEN = frozenset({'if', 'then', 'else', 'elif', 'do', 'while', 'until', '!', '{'})
_POSIX_BLOCK = frozenset({'for', 'case', 'esac', 'fi', 'done', 'function',
                          'select', '}', '[[', '(('})
_POSIX_WRAPPERS = frozenset({'time', 'nohup', 'command', 'exec', 'winpty'})
_RUN_WRAPPERS = frozenset({'uv', 'poetry', 'pipenv', 'hatch', 'pdm'})
_ASSIGN_RE = re.compile(r'[A-Za-z_]\w*=.*', re.S)
_PYTHON_RE = re.compile(r'python[\d.]*w?|py')
_HEREDOC_RE = re.compile(
    r'''<<(-?)[ \t]*(?:'([^'\n]*)'|"([^"\n]*)"|\\?([^\s;&|<>()'"]+))''')
_SAFE_NAME_RE = re.compile(r'[\w.+-]{1,40}')


@dataclass
class _Cmd:
    text: str
    substs: tuple[str, ...] = ()
    heredoc: str | None = None


@dataclass
class _List:
    """Pipelines joined by `&&`/`||`, ended by `;`, a newline or `&`."""
    pipes: list[list[_Cmd]] = field(default_factory=lambda: [[]])
    conns: list[str] = field(default_factory=list)
    end: str = ''


@dataclass
class _Ctx:
    reader: ScriptReader | None
    depth: int = 0
    base: str = ''

    def child(self) -> _Ctx:
        return _Ctx(self.reader, self.depth + 1, self.base)

    def read(self, path: str) -> str | None:
        if self.reader is None:
            return None
        absolute = path.startswith(('/', '\\', '~')) or path[1:2] == ':'
        if self.base and not absolute:
            path = self.base.rstrip('/\\') + '/' + path
        try:
            text = self.reader(path)
        except Exception:
            return None
        return text if isinstance(text, str) and len(text) <= MAX_SCRIPT else None


@dataclass
class _Info:
    verdict: Verdict
    exempt: bool = False      # the launcher: no rule applies to it
    complex: bool = False
    pipefail: bool | None = None
    errexit: bool | None = None
    cd: str = ''


def _exe(word: str) -> str:
    return word.replace('\\', '/').rsplit('/', 1)[-1].lower().removesuffix('.exe')


def _safe(name: str) -> str:
    return name if _SAFE_NAME_RE.fullmatch(name) else ''


def _norm(text: str) -> str:
    return ' '.join(text.lower().split())


def _paren_end(text: str, i: int) -> int | None:
    """Index just past the `)` that closes the `(` at `text[i]`."""
    depth, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == '\\':
            i += 2
            continue
        if c == "'":
            j = text.find("'", i + 1)
            if j < 0:
                return None
            i = j + 1
            continue
        if c == '"':
            i += 1
            while i < n and text[i] != '"':
                i += 2 if text[i] == '\\' else 1
            i += 1
            continue
        if c == '(':
            depth += 1
        elif c == ')':
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return None


def _quoted(text: str, i: int, d: _Dialect, substs: list[str]) -> tuple[int, str] | None:
    """(index past the closing quote, text to keep) for the quote at `i`."""
    q, n = text[i], len(text)
    if d.name == 'cmd':
        j = text.find('"', i + 1)
        return (n, text[i:] + '"') if j < 0 else (j + 1, text[i:j + 1])
    if q == "'":
        j = i + 1
        while True:
            j = text.find("'", j)
            if j < 0:
                return None
            if d.name == 'powershell' and text[j + 1:j + 2] == "'":
                j += 2   # '' is a literal quote
                continue
            return j + 1, text[i:j + 1]
    out, j = ['"'], i + 1
    while j < n:
        c = text[j]
        if c == d.escape:
            out.append(text[j:j + 2])
            j += 2
        elif c == '"':
            if d.name == 'powershell' and text[j + 1:j + 2] == '"':
                out.append('""')
                j += 2
                continue
            return j + 1, ''.join(out) + '"'
        elif d.name == 'posix' and text.startswith('$(', j):
            end = _paren_end(text, j + 1)
            if end is None:
                return None
            substs.append(text[j + 2:end - 1])
            out.append('$SUBST')
            j = end
        elif d.name == 'posix' and c == '`':
            end = text.find('`', j + 1)
            if end < 0:
                return None
            substs.append(text[j + 1:end])
            out.append('$SUBST')
            j = end + 1
        else:
            out.append(c)
            j += 1
    return None


def _read_heredocs(text: str, pos: int, heredocs: list[tuple[str, bool, int]],
                   cmds: list[_Cmd]) -> int:
    n = len(text)
    for delimiter, strip_tabs, ordinal in heredocs:
        body = []
        while pos < n:
            end = text.find('\n', pos)
            line = text[pos:n if end < 0 else end]
            pos = n if end < 0 else end + 1
            probe = line.rstrip('\r')
            if (probe.lstrip('\t') if strip_tabs else probe) == delimiter:
                break
            body.append(line)
        if ordinal < len(cmds):
            cmds[ordinal].heredoc = '\n'.join(body)
    return pos


def _scan(text: str, d: _Dialect) -> tuple[list[_Cmd | str], bool] | None:
    """Split into simple commands and the operators `| && || ; &`.

    Returns (items, complex) or None when the text cannot be scanned. Quotes
    and escapes stay in each command's text for the word splitter.
    """
    posix = d.name == 'posix'
    items: list[_Cmd | str] = []
    cmds: list[_Cmd] = []
    buf: list[str] = []
    substs: list[str] = []
    heredocs: list[tuple[str, bool, int]] = []
    complex_ = False
    n, i = len(text), 0

    def flush() -> None:
        raw = ''.join(buf).strip()
        buf.clear()
        if raw:
            cmd = _Cmd(raw, tuple(substs))
            items.append(cmd)
            cmds.append(cmd)
        substs.clear()

    def op(token: str) -> bool:
        flush()
        dangling = not items or isinstance(items[-1], str)
        if token in (';', '&'):
            if not dangling:
                items.append(token)
            return True
        if dangling:
            return False
        items.append(token)
        return True

    while i < n:
        c = text[i]
        if c == '\r':
            i += 1
        elif c == d.escape:
            nxt = text[i + 1:i + 2]
            if nxt == '\r' and text[i + 2:i + 3] == '\n':
                i += 3   # line continuation
            elif nxt == '\n':
                i += 2
            else:
                buf.append(text[i:i + 2] if posix else nxt)
                i += 2
        elif c in d.quotes:
            found = _quoted(text, i, d, substs)
            if found is None:
                return None
            i = found[0]
            buf.append(found[1])
        elif c == '#' and d.name != 'cmd' and (not buf or buf[-1][-1:].isspace()):
            end = text.find('\n', i)
            i = n if end < 0 else end
        elif c == '\n':
            op(';')
            i += 1
            if heredocs:
                i = _read_heredocs(text, i, heredocs, cmds)
                heredocs.clear()
        elif c == ';' and d.name != 'cmd':
            if text.startswith(';;', i):
                complex_ = True
                i += 1
            op(';')
            i += 1
        elif c == '|':
            width = 2 if text.startswith('||', i) or (posix and text.startswith('|&', i)) else 1
            if not op('||' if text.startswith('||', i) else '|'):
                return None
            i += width
        elif c == '&':
            if text.startswith('&&', i):
                if not op('&&'):
                    return None
                i += 2
                continue
            if posix and text.startswith('&>', i):
                buf.append(' &> ')
                i += 2
                continue
            if d.name == 'cmd':
                op(';')
            elif posix:
                op('&')
            elif ''.join(buf).strip():
                complex_ = True   # PowerShell background operator
                buf.append(' ')
            i += 1                # else: PowerShell call operator, dropped
        elif c in '<>':
            if posix and text.startswith('<<', i) and not text.startswith('<<<', i):
                m = _HEREDOC_RE.match(text, i)
                if m is None:
                    return None
                heredocs.append((m.group(2) or m.group(3) or m.group(4) or '',
                                 bool(m.group(1)), len(cmds)))
                buf.append(' ')
                i = m.end()
            elif posix and text[i + 1:i + 2] == '(':
                end = _paren_end(text, i + 1)
                if end is None:
                    return None
                substs.append(text[i + 2:end - 1])
                buf.append(' $SUBST ')
                i = end
            else:
                m = d.redirect.match(text, i)
                token = m.group() if m else c
                buf.append(f' {token} ')
                i += len(token)
        elif posix and text.startswith('$(', i):
            end = _paren_end(text, i + 1)
            if end is None:
                return None
            substs.append(text[i + 2:end - 1])
            buf.append('$SUBST')
            i = end
        elif posix and c == '`':
            end = text.find('`', i + 1)
            if end < 0:
                return None
            substs.append(text[i + 1:end])
            buf.append('$SUBST')
            i = end + 1
        elif c in '()' or (c in '{}' and d.name == 'powershell'):
            complex_ = True
            buf.append(' ')
            i += 1
        elif d.name == 'powershell' and c == '@' and text[i + 1:i + 2] in ('"', "'"):
            return None   # here-string
        else:
            buf.append(c)
            i += 1
    flush()
    while items and items[-1] == ';':
        items.pop()
    if items and items[-1] in ('|', '&&', '||'):
        return None
    return items, complex_


def _group(items: list[_Cmd | str]) -> list[_List]:
    lists: list[_List] = []
    cur = _List()
    for item in items:
        if isinstance(item, _Cmd):
            cur.pipes[-1].append(item)
        elif item in ('&&', '||'):
            cur.conns.append(item)
            cur.pipes.append([])
        elif item != '|':
            cur.end = item
            lists.append(cur)
            cur = _List()
    if cur.pipes != [[]]:
        lists.append(cur)
    return lists


def _split(text: str, d: _Dialect) -> list[str] | None:
    try:
        if d.name == 'posix':
            return shlex.split(text, posix=True)
        lex = shlex.shlex(text, posix=False)
        lex.whitespace_split = True
        lex.commenters = ''
        lex.quotes = d.quotes
        return [part.strip(d.quotes) for part in lex]
    except ValueError:
        return None


def _head(argv: list[str], d: _Dialect) -> tuple[list[str], bool, bool]:
    """Drop prefixes in front of the command word: (argv, complex, opaque)."""
    complex_ = False
    while argv:
        word = argv[0]
        exe = _exe(word)
        if d.name == 'posix':
            if word in _POSIX_OPEN:
                complex_, argv = True, argv[1:]
                continue
            if word in _POSIX_BLOCK:
                return [], True, False
            if exe in _POSIX_WRAPPERS:
                argv = argv[1:]
                while argv and argv[0].startswith('-'):
                    argv = argv[1:]
                continue
            if exe == 'env':
                argv = argv[1:]
                while argv and (argv[0].startswith('-') or _ASSIGN_RE.fullmatch(argv[0])):
                    argv = argv[2:] if argv[0] in ('-u', '--unset', '-C', '--chdir') else argv[1:]
                continue
            if exe == 'timeout':
                argv = argv[1:]
                while argv and argv[0].startswith('-'):
                    argv = argv[2:] if argv[0] in ('-s', '-k', '--signal', '--kill-after') else argv[1:]
                argv = argv[1:]   # the duration
                continue
        elif d.name == 'cmd':
            if word.startswith('::') or exe == 'rem':
                return [], False, False
            if word.startswith(':'):
                return [], True, False   # a label: goto flow
            if word.startswith('@'):
                argv = [word[1:], *argv[1:]] if len(word) > 1 else argv[1:]
                continue
            if exe == 'call' and len(argv) > 1 and not argv[1].startswith(':'):
                argv = argv[1:]
                continue
        elif word == '.':
            argv = argv[1:]   # PowerShell dot-source
            continue
        if _ASSIGN_RE.fullmatch(word) and d.name == 'posix':
            argv = argv[1:]
            continue
        if exe in d.keywords:
            return [], True, True
        if exe in _RUN_WRAPPERS and argv[1:2] == ['run']:
            argv = argv[2:]
            continue
        break
    return argv, complex_, False


def _name(cmd: _Cmd, d: _Dialect) -> str:
    argv = _split(cmd.text, d)
    return _safe(_exe(argv[0])) if argv else ''


def _is_launcher(argv: list[str]) -> bool:
    args = argv[1:] if _PYTHON_RE.fullmatch(_exe(argv[0])) else argv
    for arg in args:
        if arg.startswith('-'):
            continue
        norm = arg.replace('\\', '/').lower()
        return norm.endswith('/tools/test-run.py') or norm == 'tools/test-run.py'
    return False


def _named_for_tests(path: str) -> bool:
    # candidate()'s own name rule for node scripts (test/tests/smoke as a
    # name part), asked about this script's base name instead of copied.
    stem = os.path.splitext(path.replace('\\', '/').rsplit('/', 1)[-1])[0]
    return candidate('bash', ['node', stem + '.js'])


def _flags(args: list[str]) -> tuple[bool | None, bool | None, int, bool]:
    """sh option words: (pipefail, errexit, index past them, saw -c)."""
    pipefail = errexit = None
    k = 0
    while k < len(args):
        arg = args[k]
        if arg.startswith('--'):
            k += 1
            if arg == '--':
                break
            continue
        if len(arg) < 2 or arg[0] not in '-+':
            break
        on, letters = arg[0] == '-', arg[1:]
        if 'e' in letters:
            errexit = on
        if 'o' in letters and k + 1 < len(args):
            k += 1
            if args[k] == 'pipefail':
                pipefail = on
            elif args[k] == 'errexit':
                errexit = on
        k += 1
        if on and 'c' in letters:
            return pipefail, errexit, k, True
    return pipefail, errexit, k, False


def _shell_call(argv: list[str], cmd: _Cmd, d: _Dialect, ctx: _Ctx,
                pipefail: bool, errexit: bool) -> Verdict | None:
    """Verdict of an inline script or script file this command runs, or None."""
    exe, rest = _exe(argv[0]), argv[1:]
    kind = _SHELL_EXES.get(exe)
    inline: str | None = None
    script: str | None = None
    pf = ee = False
    if d.name == 'posix' and exe in ('source', '.', 'eval') and rest:
        kind, pf, ee = 'posix', pipefail, errexit   # same shell, same options
        if exe == 'eval':
            inline = ' '.join(rest)
        else:
            script = rest[0]
    elif kind == 'posix':
        f_pf, f_ee, k, is_inline = _flags(rest)
        pf, ee = bool(f_pf), bool(f_ee)
        if is_inline:
            inline = rest[k] if k < len(rest) else ''
        elif k < len(rest):
            script = rest[k]
        elif cmd.heredoc is not None:
            inline = cmd.heredoc
        else:
            return Verdict(UNKNOWN, 'opaque_script')   # reads a script from stdin
    elif kind == 'powershell':
        k = 0
        while k < len(rest) and inline is None and script is None:
            arg = rest[k].lower()
            if not arg.startswith('-'):
                if arg.endswith('.ps1'):
                    script = rest[k]
                else:
                    inline = ' '.join(rest[k:])
            elif len(arg) > 1 and '-command'.startswith(arg):
                inline = ' '.join(rest[k + 1:])
            elif len(arg) > 1 and '-file'.startswith(arg):
                script = rest[k + 1] if k + 1 < len(rest) else ''
            elif len(arg) > 1 and ('-encodedcommand'.startswith(arg) or arg == '-ec'):
                return Verdict(UNKNOWN, 'opaque_script')
            elif len(arg) > 1 and any(opt.startswith(arg) for opt in _PS_VALUE_OPTS):
                k += 1
            k += 1
        if inline is None and script is None:
            return Verdict(UNKNOWN, 'opaque_script')
    elif kind == 'cmd':
        for k, arg in enumerate(rest):
            if arg.lower().lstrip('/') in ('c', 'k') and arg.startswith('/'):
                inline = ' '.join(rest[k + 1:])
                break
            if not arg.startswith('/'):
                break
        if inline is None:
            return Verdict(UNKNOWN, 'opaque_script')
    else:
        kind = _SCRIPT_EXTS.get(os.path.splitext(argv[0])[1].lower())
        if kind is None:
            return None
        script = argv[0]
    if ctx.depth >= MAX_DEPTH:
        return Verdict(UNKNOWN, 'nested_too_deep')
    label = ''
    if script is not None:
        label = _safe(script.replace('\\', '/').rsplit('/', 1)[-1])
        text = ctx.read(script)
        if text is None:
            if _named_for_tests(script):
                return Verdict(MASKED, 'script_unreadable', label)
            return Verdict(UNKNOWN, 'opaque_script', label)
        kind = _SCRIPT_EXTS.get(os.path.splitext(script)[1].lower(), kind)
    else:
        text = inline or ''
    verdict = _walk(text, _DIALECTS[kind], ctx.child(), pf, ee)
    if label and verdict.state in (MASKED, UNKNOWN) and not verdict.script:
        return replace(verdict, script=label)
    return verdict


def _inspect(cmd: _Cmd, d: _Dialect, ctx: _Ctx, pipefail: bool, errexit: bool) -> _Info:
    argv = _split(cmd.text, d)
    if argv is None:
        return _Info(Verdict(UNKNOWN, 'unparsed'))
    argv, complex_, opaque = _head(argv, d)
    if opaque:
        return _Info(Verdict(UNKNOWN, 'complex'), complex=True)
    inside = Verdict(NOT_TEST)
    for inner in cmd.substs:
        if ctx.depth >= MAX_DEPTH:
            found = Verdict(UNKNOWN, 'nested_too_deep')
        else:
            found = _walk(inner, _DIALECTS['posix'], ctx.child(), pipefail, False)
        if found.masked:
            return _Info(found, complex=complex_)
        if found.state != NOT_TEST and inside.state == NOT_TEST:
            # A runner's status inside $(...) is not the command's.
            inside = found if found.state == UNKNOWN else Verdict(UNKNOWN, 'substitution')
    if not argv:
        return _Info(inside, complex=complex_)
    if _is_launcher(argv):
        return _Info(Verdict(CLEAN, 'launcher'), exempt=True, complex=complex_)
    if candidate('bash', argv):
        return _Info(Verdict(CLEAN), complex=complex_)
    nested = _shell_call(argv, cmd, d, ctx, pipefail, errexit)
    if nested is not None:
        return _Info(nested, complex=complex_)
    info = _Info(inside, complex=complex_)
    exe = _exe(argv[0])
    if d.name == 'posix' and exe == 'set':
        info.pipefail, info.errexit, _, _ = _flags(argv[1:])
    elif exe in ('cd', 'pushd', 'set-location') and len(argv) == 2:
        info.cd = argv[1]
    return info


def _after(lists: list[_List], li: int, pi: int, d: _Dialect, errexit: bool) -> Verdict:
    """Does a failing status of pipeline `pi` of list `li` reach the end?"""
    lst = lists[li]
    for j in range(pi, len(lst.conns)):
        if lst.conns[j] == '&&':
            continue
        fallback = lst.pipes[j + 1]
        if len(fallback) == 1 and d.failing_exit.fullmatch(_norm(fallback[0].text)):
            return Verdict(CLEAN)
        return Verdict(MASKED, 'or_fallback', _name(fallback[0], d) if fallback else '')
    if lst.end == '&':
        return Verdict(UNKNOWN, 'backgrounded')
    rest = [cmd for later in lists[li + 1:] for pipe in later.pipes for cmd in pipe]
    if not rest:
        return Verdict(CLEAN)
    if errexit and pi == len(lst.pipes) - 1:
        return Verdict(CLEAN)
    if d.passthrough.fullmatch(_norm(rest[0].text)):
        return Verdict(CLEAN)
    if any(word in _norm(cmd.text) for cmd in rest for word in d.status_words):
        return Verdict(UNKNOWN, 'exit_status_handled')
    return Verdict(MASKED, 'followed', _name(rest[0], d))


def _walk(text: str, d: _Dialect, ctx: _Ctx, pipefail: bool, errexit: bool) -> Verdict:
    scanned = _scan(text, d)
    if scanned is None:
        return Verdict(UNKNOWN, 'unparsed')
    lists = _group(scanned[0])
    complex_ = scanned[1]
    rows: list[tuple[int, int, list[_Cmd], list[_Info], bool, bool]] = []
    for li, lst in enumerate(lists):
        for pi, pipe in enumerate(lst.pipes):
            infos = [_inspect(cmd, d, ctx, pipefail, errexit) for cmd in pipe]
            rows.append((li, pi, pipe, infos, pipefail, errexit))
            complex_ = complex_ or any(info.complex for info in infos)
            if len(infos) == 1:   # options and directory apply to what follows
                only = infos[0]
                pipefail = pipefail if only.pipefail is None else only.pipefail
                errexit = errexit if only.errexit is None else only.errexit
                if only.cd and '$' not in only.cd:
                    absolute = only.cd.startswith(('/', '\\', '~')) or only.cd[1:2] == ':'
                    ctx.base = only.cd if absolute or not ctx.base else (
                        ctx.base.rstrip('/\\') + '/' + only.cd)
    low = text.lower()
    tests = False
    pending: Verdict | None = None
    for li, pi, pipe, infos, row_pipefail, row_errexit in rows:
        for ci, info in enumerate(infos):
            verdict = info.verdict
            if verdict.masked:
                return verdict
            if verdict.state == UNKNOWN:
                pending = pending or verdict
                continue
            if verdict.state == NOT_TEST:
                continue
            tests = True
            if info.exempt:
                continue
            if ci < len(pipe) - 1 and not row_pipefail:
                if any(word in low for word in d.pipe_status_words):
                    pending = pending or Verdict(UNKNOWN, 'exit_status_handled')
                    continue
                return Verdict(MASKED, 'piped', _name(pipe[ci + 1], d))
            if complex_:
                pending = pending or Verdict(UNKNOWN, 'complex')
                continue
            reach = _after(lists, li, pi, d, row_errexit)
            if reach.masked:
                return reach
            if reach.state == UNKNOWN:
                pending = pending or reach
    if pending is not None:
        return pending
    if complex_ and d.opaque:
        return Verdict(UNKNOWN, 'complex')
    return Verdict(CLEAN if tests else NOT_TEST)


def classify(command: Any, shell: str | None = None, *,
             read_script: ScriptReader | None = None,
             pipefail: bool = False) -> Verdict:
    """Is `command` a recognised test run, and is its exit status masked?

    Pure when `read_script` is None or pure: no file, process or network
    access of its own. `shell` is 'bash'/'sh'/'posix', 'powershell'/'pwsh' or
    'cmd'; anything else means not known. `read_script(path)` returns the
    text of a script file the command runs, or None. `pipefail` says the
    caller already runs the command under `bash -o pipefail`. The rules are
    in the module docstring.
    """
    if not isinstance(command, str) or not command.strip():
        return Verdict(NOT_TEST)
    if len(command) > MAX_COMMAND:
        return Verdict(UNKNOWN, 'too_long')
    known = _SHELL_KINDS.get(shell.strip().lower()) if isinstance(shell, str) else None
    kinds = (known,) if known else ('posix', 'powershell', 'cmd')
    verdicts = [_walk(command, _DIALECTS[kind], _Ctx(read_script),
                      pipefail and kind == 'posix', False) for kind in kinds]
    if all(verdict.state == verdicts[0].state for verdict in verdicts):
        return verdicts[0]
    return Verdict(UNKNOWN, 'shell_unknown')


# ── Reporting ────────────────────────────────────────────────────────────

def enabled() -> bool:
    return state.CONFIG.get(SETTING, True) is not False


def status_line(verdict: Verdict) -> str:
    """The one chat line for a masked run; '' for every other verdict.

    Starts with `LABEL`, which `mc.reply_line_kind` registers, so the line is
    never collected as reply text.
    """
    if not verdict.masked:
        return ''
    other = verdict.detail or 'another command'
    where = f' in {verdict.script}' if verdict.script else ''
    if verdict.reason == 'piped':
        why = (f"the test command{where} is piped into {other}, so the exit "
               f"code is {other}'s, not the test run's")
    elif verdict.reason == 'or_fallback':
        why = (f'the test command{where} is followed by || {other}, which '
               'replaces a failing exit code')
    elif verdict.reason == 'script_unreadable':
        why = (f'the tests start inside a script Clayrune could not read'
               f'{" (" + verdict.detail + ")" if verdict.detail else ""}, so '
               'their exit code is not visible')
    else:
        why = (f"{other} runs after the test command{where}, so the exit "
               f"code is {other}'s, not the test run's")
    return (f'{LABEL} UNVERIFIED: {why}. For the real pass or fail, run the '
            'tests through tools/test-run.py.')


def file_reader(cwd: str | None) -> ScriptReader:
    """A bounded local script reader for `classify(read_script=...)`."""
    def read(path: str) -> str | None:
        if not path or any(ch in path for ch in '\x00$`*?'):
            return None
        if os.name == 'nt':
            drive = re.fullmatch(r'/([A-Za-z])/(.*)', path)   # Git Bash form
            if drive:
                path = f'{drive.group(1)}:/{drive.group(2)}'
        if path.startswith(('//', '\\\\')):
            return None   # never a network path from a reader thread
        try:
            target = Path(path).expanduser()
            if not target.is_absolute():
                if not cwd:
                    return None
                target = Path(cwd) / target
            if not target.is_file() or target.stat().st_size > MAX_SCRIPT:
                return None
            return target.read_text(encoding='utf-8', errors='replace')
        except (OSError, RuntimeError, ValueError):
            return None
    return read


def shell_for_tool(name: str) -> str | None:
    """Shell a vendor tool runs its command string in, where that is known.

    Claude's `Bash` is bash and its `PowerShell` is PowerShell. Every other
    shell tool (Codex `shell`, Gemini and Qwen `run_shell_command`) is `sh`
    off Windows; on Windows the vendor picks the shell and does not say
    which, so it is not known.
    """
    low = name.lower()
    if low == 'powershell':
        return 'powershell'
    return 'posix' if low == 'bash' or os.name != 'nt' else None


def tool_call_line(name: Any, tool_input: Any, cwd: str | None = None) -> str:
    """Status line for one agent shell tool call, or ''."""
    if not isinstance(name, str) or name.lower() not in SHELL_TOOLS:
        return ''
    if isinstance(tool_input, str):   # Codex function_call: arguments as JSON text
        try:
            tool_input = json.loads(tool_input)
        except ValueError:
            return ''
    args = tool_input if isinstance(tool_input, dict) else {}
    command = args.get('command', args.get('cmd'))
    shell = shell_for_tool(name)
    if isinstance(command, list) and all(isinstance(arg, str) for arg in command):
        command, shell = shlex.join(command), 'posix'   # an argv, no shell of its own
    return status_line(classify(command, shell, read_script=file_reader(cwd)))


def note_shell_call(session: dict[str, Any], name: Any, tool_input: Any,
                    call_id: Any = None) -> None:
    """Append the status line after a shell tool call's `[tool: ...]` line.

    Called by every engine's stream reader right after it surfaces the call.
    One line per call id (Codex surfaces a call when it starts and again when
    it completes). Never raises.
    """
    try:
        if not enabled():
            return
        seen = session.setdefault(_SEEN_KEY, [])
        if isinstance(call_id, str) and call_id:
            if call_id in seen:
                return
            seen.append(call_id)
            del seen[:-MAX_SEEN]
        line = tool_call_line(name, tool_input, session.get('_agent_cwd') or None)
        if line:
            session['log_lines'].append(line)
    except Exception as exc:
        _log(f'[test-run-masking] shell call check failed: {type(exc).__name__}', flush=True)


def job_line(result: dict[str, Any]) -> str:
    """Status line for a finished background job, or ''. Never raises.

    The job route runs bash jobs under `-o pipefail` (`mc.agent_jobs.
    build_argv`), so a pipe at the top level of a bash job is not masked; a
    pipe inside a script the job runs still is.
    """
    try:
        shell = result.get('shell')
        if not enabled() or shell not in _SHELL_KINDS:
            return ''
        return status_line(classify(
            result.get('command'), shell,
            read_script=file_reader(result.get('cwd') or None),
            pipefail=shell == 'bash'))
    except Exception as exc:
        _log(f'[test-run-masking] job check failed: {type(exc).__name__}', flush=True)
        return ''
