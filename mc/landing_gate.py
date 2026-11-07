"""MC-1164: compare merged-tree command failures with the target's baseline.

Called under repository landing ownership, before the target is modified.
The agent checkout is borrowed while detached; neither branch ref is moved.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import posixpath
import re
import shlex
import subprocess
import tempfile
import threading
from collections import Counter, OrderedDict
from pathlib import Path
from typing import Any, Callable

from mc.dev_harness_diff import FreezeError, git_bytes, merge_tree, resolve_commit
from mc.landing_serialization import Ownership, assert_held, common_dir

DEFAULT_TIMEOUT_SECONDS = 1200
MAX_OUTPUT_BYTES = 32 * 1024 * 1024
TAIL_CHARS = 12000
_ANSI = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')
_PYTEST = re.compile(r'^(?:FAILED|ERROR)\s+(\S+\.py(?:::.+)?)$')
_SMOKE = re.compile(
    r'^(?:(?P<tag>SCHEDFAIL|CALFAIL)\s+|[✗✘×]\s*|'
    r'❌\s*(?:FAIL\s*[:—-]?\s*)?|FAIL(?:\s*[:—-]\s*|\s+|$))(?P<name>.*?)\s*$')
_FAILURE = re.compile(
    r'fail|error|exception|fatal|\bnot ok\b|\busage:|'
    r'\b(?:not found|could not|unreachable|is required|must be)\b', re.I)
_SUMMARY = re.compile(
    r'^(?:(?:RTL smoke|desk-studio-passcode-replay|human-proof-busy|'
    r'MC-1070 (?:independent|wired) controls):\s*)?(?:\d+(?:/\d+)?(?: [\w-]+)* '
    r'(?:check|assertion|problem|case|failure|behaviour)(?:\(s\)|s)?'
    r'(?: (?:failed|regressed))?\.?|FAILED(?:[: (]+\d+\)?)?|\d+ FAILED|'
    r'RESULT: FAIL|Checks=\d+ failures=\d+|fails=\d+)$', re.I)
# Runner bookkeeping, not a failure: pytest's closing count line and the TAP / node
# test-runner totals. Counts change when unrelated tests are added or fixed.
_PYTEST_COUNT_ITEMS = (
    r'=*\s*(?:\d+ (?:failed|passed|skipped|deselected|xfailed|xpassed|errors?|warnings?|rerun)(?:, )?)+')
_PYTEST_COUNT_DURATION = r' in (?:<duration>|[\d.]+s)(?: \(\d+:\d{2}:\d{2}\))?'
_PYTEST_COUNT_LINE = _PYTEST_COUNT_ITEMS + '(?:' + _PYTEST_COUNT_DURATION + r')?\s*=*'
_RUNNER_SUMMARY = re.compile(
    _PYTEST_COUNT_LINE + r'|=*\s*no tests ran(?: in <duration>)?\s*=*|'
    r'(?:#|ℹ) (?:tests|suites|pass|fail|cancelled|skipped|todo|duration_ms) [\d.]+')
# The runner's own failure totals, used as a count backstop in compare(): pytest's
# closing `N failed` / `N error(s)` and node:test's `ℹ fail N` / `# fail N`
# (`cancelled` is a failed test that never finished, so it counts too). pytest ALWAYS
# ends its closing line with ` in <time>`; a bare `1 failed` is a test's own print().
_PYTEST_COUNTS = re.compile(_PYTEST_COUNT_ITEMS + _PYTEST_COUNT_DURATION + r'\s*=*')
_PYTEST_FAILED_PART = re.compile(r'(\d+) (?:failed|errors?)\b')
_NODE_COUNTS = re.compile(r'(?:#|ℹ) (?:fail|cancelled) (?P<n>\d+)')
# TAP `not ok 3 - name`: the number is the test's position, not its identity.
_TAP = re.compile(r'^not ok\s+\d+\b\s*(?:-\s*)?(?P<name>.*?)\s*$')
_TAP_LOCATION = re.compile(r'''^location:\s*['"]?(?P<file>[^'"]+?)['"]?$''')
_TAP_SUBTEST = re.compile(r'^# Subtest:\s*(?P<name>.*?)\s*$')
_TAP_SUITE_TYPE = re.compile(r"""^type:\s*['"]?suite['"]?$""")
# pytest report heading `____ test_x ____`: the only per-test name when no short
# summary (-r) is printed, now that the closing count line is ignored. It names a
# failure only under a `=== FAILURES ===` / `=== ERRORS ===` banner; `-rP`/`-rA`
# print the same heading for PASSING tests with captured output under `=== PASSES ===`.
_PYTEST_SECTION = re.compile(r'^_{2,} (?P<name>\S.*?) _{2,}$')
_PYTEST_BANNER = re.compile(r'^={2,} (?P<name>\S.*?) ={2,}$')
_PYTEST_FAILURE_BANNERS = frozenset({'FAILURES', 'ERRORS'})
# `---- Captured stdout call ----`: from here to the next `____ name ____` heading the
# text is the TEST's own output, which can print anything, including a line spelled
# like a banner. pytest pads every real banner to one fixed width, so inside captured
# output a banner counts only at the width of the banners seen outside it.
_PYTEST_CAPTURE = re.compile(r'^-{2,} Captured .+ -{2,}$')
# node:test `spec` reporter (the default for a non-TTY stdout): one glyph line per
# test, nested suites indented, each suite opened by `▶ name` and closed by a `✔`/`✖`
# line carrying its own name. Failures are re-listed after the totals in a
# `✖ failing tests:` recap that this parser skips (the live lines already name them).
_SPEC = re.compile(r'^(?P<glyph>[▶✔✖﹣])\s+(?P<name>.*?)(?:\s+\(<duration>\))?(?:\s+# (?:SKIP|TODO).*)?$')
_SPEC_RECAP = re.compile(r'^✖ failing tests:?$')
_SPEC_RECAP_ENTRY = re.compile(r'^test at \S')
_DURATION = re.compile(r'\b\d+(?:\.\d+)?\s*(?:ms|milliseconds?|s|seconds?|secs?|minutes?|mins?)\b', re.I)
_ABSOLUTE_FILE = re.compile(
    r'(?<![\w>])(?:file://)?(?:[A-Za-z]:[\\/]|/)(?:[^\s<>\"\'():]+[\\/])*'
    r'(?P<file>[^\s<>\"\'():/\\]+\.[A-Za-z0-9]+)(?::\d+(?::\d+)?)?')
# `file.ext:LINE[:COL]` -> `file.ext` for relative and absolute paths alike, so a
# moved assertion keeps its identity. Needs a path separator or a source-file
# extension, so `example.com:8080` keeps its port.
_LOCATION = re.compile(
    r'(?P<file>[\w.\-~@+\\/]*[\w\-]\.(?P<ext>[A-Za-z][A-Za-z0-9]{0,7}))(?::\d+){1,2}(?!\w)')
_SOURCE_EXTENSIONS = frozenset(
    'py pyi js mjs cjs jsx ts tsx json html htm css scss md txt sh bash ps1 bat cmd rs go java kt c h cc cpp '
    'hpp cs rb php swift yml yaml toml ini cfg vue svelte sql'.split())


class _PytestFrame:
    """Where a line sits in pytest's report: the current `=== NAME ===` banner and whether
    we are inside a `--- Captured ... ---` block. Shared by failure_signatures (headings
    count only under FAILURES / ERRORS) and failure_totals (a count line inside captured
    output is the test's print, not pytest's total)."""

    def __init__(self) -> None:
        self.section = ''  # current pytest `=== NAME ===` banner
        self.in_capture = False  # inside a captured block: the test's own output
        self.banner_width = 0  # width of the first banner seen outside captured output

    def feed(self, shown: str, line: str) -> str | None:
        """Advance past one line; return the banner name if it was accepted as a real one."""
        accepted = None
        if _PYTEST_CAPTURE.match(line):
            self.in_capture = True
        banner = _PYTEST_BANNER.match(line)
        if banner and (not self.in_capture or len(shown) == self.banner_width):
            # A banner inside captured output that is not pytest's own width is the
            # test's print(); it must not move the section.
            self.section, self.in_capture = banner['name'], False
            self.banner_width = self.banner_width or len(shown)
            accepted = banner['name']
        if _PYTEST_SECTION.match(line):
            self.in_capture = False
        return accepted


# A runner total the parser could not attribute. Stored in failure_totals in place of a
# number so it survives into the result; compare() blocks on it ("unprovable").
AMBIGUOUS = 'ambiguous'
# A test identity whose relative file qualifier does not resolve to a file under the
# verification cwd (a wrapper that runs the runner in a child directory prints paths
# relative to THAT directory). It is not proof of which file failed, so compare() does
# not count it as a named failure and blocks while the candidate fails (MC-1164 round 7).
_UNRESOLVED = ' @?'
_NAMED_PREFIXES = ('pytest:', 'pytest-report:', 'tap:', 'spec:')

_cache: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
_cache_lock = threading.Lock()
Reviewer = Callable[[dict[str, Any]], dict[str, Any]]


def settings(project: dict[str, Any]) -> tuple[list[str], float]:
    """A list means ordered shell commands, not an argv array."""
    value = project.get('verify_command')
    if value is None or value == '' or value == []:
        return [], DEFAULT_TIMEOUT_SECONDS
    commands = [value] if isinstance(value, str) else value
    if (not isinstance(commands, list) or not 1 <= len(commands) <= 128
            or any(not isinstance(c, str) or not c.strip() or '\0' in c for c in commands)):
        raise ValueError('invalid_verify_command')
    timeout = project.get('verify_timeout_seconds', DEFAULT_TIMEOUT_SECONDS)
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('invalid_verify_timeout_seconds')
    return list(commands), float(timeout)


def _drop_location(match: re.Match[str]) -> str:
    file = match['file']
    if '/' in file or '\\' in file or match['ext'].lower() in _SOURCE_EXTENSIONS:
        return file
    return match[0]


def _stable_line(line: str) -> str:
    # Keep assertion values and file/check names; remove locations and elapsed time.
    line = _ABSOLUTE_FILE.sub(lambda m: '<path>/' + m['file'], line)
    line = re.sub(r'(<worktree>[/\\][^\s:]+):\d+(?::\d+)?', r'\1', line)
    line = _LOCATION.sub(_drop_location, line)
    return ' '.join(_DURATION.sub('<duration>', line).split())


def _qualifier(path: str, root: Path | None) -> str:
    """A test file as the identity qualifier: the full path relative to the run cwd,
    forward slashes, no :LINE:COL. NOT the basename: a/same.test.mjs and
    b/same.test.mjs are different files. Input is _clean() output, where the cwd is
    already <worktree>; a path outside the cwd stays absolute (fails closed)."""
    path = re.sub(r':\d+(?::\d+)?$', '', path.strip().strip('\'"'))
    if path.startswith('file://'):
        path = re.sub(r'^/(?=[A-Za-z]:)', '', path[len('file://'):])
    path = path.replace('\\', '/')
    if path.startswith('<worktree>/'):
        path = path[len('<worktree>/'):]
    elif root is not None and os.path.isabs(path):
        try:
            path = Path(path).resolve().relative_to(root.resolve()).as_posix()
        except (ValueError, OSError):
            pass
    return posixpath.normpath(path)


def _identity_file(path: str, root: Path | None, relative_ok: bool = True) -> str:
    """_qualifier() plus provenance. A RELATIVE qualifier is the runner's own cwd-relative
    spelling, so it names a file under the verification cwd only if the configured command
    IS that runner (relative_ok: a direct `node --test`, see _direct_node). Under any
    wrapper the runner may have run in a child directory, and a same-named file at the
    outer cwd proves nothing (MC-1164 round 8), so it is marked `?` (unproven). Even when
    relative_ok it must also exist there. An absolute path outside the cwd stays absolute.
    With no cwd there is nothing to resolve against (direct parser calls); run_command
    always passes one and always passes relative_ok."""
    qualified = _qualifier(path, root)
    if root is None or os.path.isabs(qualified) or re.match(r'^[A-Za-z]:/', qualified):
        return qualified
    # A path the runner printed absolute (a TAP `location:`, already <worktree>/ after
    # _clean) is anchored to the cwd whatever wrapper ran it; only a bare relative one is not.
    spelled = path.strip().strip("'\"").replace(chr(92), '/')
    anchored = (spelled.startswith(('<worktree>/', 'file://')) or os.path.isabs(spelled)
                or bool(re.match(r'^[A-Za-z]:/', spelled)))
    if anchored:
        return qualified if (root / qualified).is_file() else '?' + qualified
    return qualified if relative_ok and (root / qualified).is_file() else '?' + qualified


def _clean(output: str, root: Path | None) -> str:
    clean = _ANSI.sub('', output).replace('\r', '\n')
    if root is not None:
        clean = clean.replace(str(root), '<worktree>').replace(root.as_posix(), '<worktree>')
    return clean


def failure_totals(output: str, exit_code: int, root: Path | None = None) -> list[int | str]:
    """The runner's own failed/error total lines, IN OUTPUT ORDER, one entry each.

    Never summed: a sum lets a total a test printed offset one pytest never printed
    (MC-1164 round 6). A zero exit is []. A total the parser had to drop because it
    could not tell the runner's line from a test's print is the explicit AMBIGUOUS
    marker, not a silent gap: compare() cannot prove anything from it and blocks.
    [] with a non-zero exit means no recognised total (a smoke script, a crash before
    the summary): compare() then relies on signatures alone.
    """
    if exit_code == 0:
        return []
    totals: list[int | str] = []
    frame = _PytestFrame()
    # A count line inside a captured block is almost always the test's print(). The one
    # exception is pytest's own bare closing line when no short summary (`-rN`) separates
    # it from the last captured block: that line is the LAST thing in the output. So it
    # is held back, and kept only if nothing follows it. Anything that does follow
    # (a wrapper's footer, more test output, another count line) leaves it unprovable.
    held: int | None = None
    for raw in _clean(output, root).splitlines():
        shown = raw.strip()
        if not shown:
            continue
        line = _stable_line(shown)
        frame.feed(shown, line)
        node = _NODE_COUNTS.fullmatch(line)
        pytest_count = None if node else _PYTEST_COUNTS.fullmatch(line)
        if held is not None:
            totals.append(AMBIGUOUS)  # the held line was not provably the last thing
            held = None
        if pytest_count and frame.in_capture:
            held = sum(int(n) for n in _PYTEST_FAILED_PART.findall(line))
        elif node:
            totals.append(int(node['n']))
        elif pytest_count:
            totals.append(sum(int(n) for n in _PYTEST_FAILED_PART.findall(line)))
    if held is not None:
        totals.append(held)
    return totals


class _Scan:
    """One pass over a failing command's output: the sorted signatures plus the parts of the
    pass that prove() reuses (which `counted` entries were node suite/describe containers)."""

    def __init__(self, signatures: list[str], counted: list[str], containers: set[int]) -> None:
        self.signatures = signatures
        self.counted = counted
        self.containers = containers  # indexes into counted: spec `✖ suite` / TAP `type: 'suite'`


def failure_signatures(output: str, exit_code: int, root: Path | None = None,
                       relative_ok: bool = True) -> list[str]:
    """Sorted failure identities. Test identities (pytest, TAP, spec) are a multiset:
    the same one printed twice is listed twice. Smoke and fallback lines are a set.
    relative_ok is _direct_node(command) when the command is known (run_command always
    passes it): a relative spec/TAP file path is trusted only then (see _identity_file)."""
    return _scan(output, exit_code, root, relative_ok).signatures


def _scan(output: str, exit_code: int, root: Path | None, relative_ok: bool) -> _Scan:
    if exit_code == 0:
        return _Scan([], [], set())
    clean = _clean(output, root)
    names: set[str] = set()
    counted: list[str] = []  # test identities, one entry per failure the runner printed
    containers: set[int] = set()  # indexes in counted that are a node suite, not a test
    summaries: set[str] = set()
    heading = ''
    subtests: list[tuple[int, str]] = []  # enclosing TAP `# Subtest:` lines, by indent
    suites: list[tuple[int, str]] = []  # enclosing spec `▶ name` suites, by indent
    frame = _PytestFrame()
    recap = False  # inside the spec reporter's trailing `✖ failing tests:` recap
    recap_entry = False  # the previous recap line was `test at file:line:col`
    recap_file = ''  # the file that line named
    spec_live: list[tuple[int, str]] = []  # (index into counted, test name) per live `✖`
    recap_files: list[tuple[str, str]] = []  # (test name, file) per recap entry
    tap_open = -1  # index into counted of the `not ok` whose YAML block is still open
    for raw in clean.splitlines():
        line = raw.strip()
        if not line:
            continue
        indent = len(raw) - len(raw.lstrip())
        match = _PYTEST.match(line.split(' - ', 1)[0])
        if match:
            counted.append('pytest:' + match[1])
            heading = ''
            continue
        shown = line
        line = _stable_line(line)
        subtest = _TAP_SUBTEST.match(line)
        if subtest:
            while subtests and subtests[-1][0] >= indent:
                subtests.pop()
            subtests.append((indent, subtest['name']))
            heading = ''
            continue
        frame.feed(shown, line)  # a banner/heading line then keeps its old treatment below
        section = _PYTEST_SECTION.match(line)
        if section and frame.section in _PYTEST_FAILURE_BANNERS:
            counted.append('pytest-report:' + section['name'])
            heading = ''
            continue
        if _SPEC_RECAP.match(line):
            recap, recap_entry = True, False
            suites.clear()
            heading = ''
            continue
        spec = _SPEC.match(line)
        if spec:
            glyph, name = spec['glyph'], spec['name']
            if recap and recap_entry and glyph == '✖':
                recap_entry = False  # the recap's copy of a failure already named above
                recap_files.append((name or '<unspecified>', recap_file))
                continue
            recap = False  # a glyph line that is not a recap entry: a new run's live output
            if glyph == '▶':
                while suites and suites[-1][0] >= indent:
                    suites.pop()
                suites.append((indent, name))
            else:
                while suites and suites[-1][0] > indent:
                    suites.pop()
                closing = bool(suites and suites[-1] == (indent, name))
                if closing:
                    suites.pop()  # a suite's own closing line
                if glyph == '✖':
                    # Qualified by enclosing suites, like TAP. A suite that fails only
                    # because a child failed is listed too: it is stable and harmless as a
                    # signature, but it is a container, not a test (prove() skips it).
                    path = [n for depth, n in suites if depth < indent]
                    spec_live.append((len(counted), name or '<unspecified>'))
                    counted.append('spec:' + ' > '.join([*path, name or '<unspecified>']))
                    if closing:
                        containers.add(len(counted) - 1)
            heading = ''
            continue
        if recap:
            recap_entry = bool(_SPEC_RECAP_ENTRY.match(line))
            recap_file = _identity_file(shown[len('test at '):], root, relative_ok) if recap_entry else ''
        tap = _TAP.match(line)
        if tap:
            # Qualified by enclosing suites, so the same test name failing in a
            # second suite is still a new failure.
            path = [name for depth, name in subtests if depth < indent]
            counted.append('tap:' + ' > '.join([*path, tap['name'] or '<unspecified>']))
            tap_open = len(counted) - 1
            heading = ''
            continue
        # node's TAP block names the file in `location: '<file>:LINE:COL'`; the identity
        # includes it (full path relative to the run cwd, as the spec reporter's does), so
        # a failure that moves to another file (same test name, same count) is a new one.
        # Other TAP producers print no location and stay unqualified.
        if tap_open >= 0:
            if _TAP_SUITE_TYPE.match(line):
                containers.add(tap_open)  # `type: 'suite'`: a describe(), not a test
            location = _TAP_LOCATION.match(shown)
            if location:
                counted[tap_open] += ' @' + _identity_file(location['file'], root, relative_ok)
            if location or line == '...' or line.startswith(('ok ', 'not ok ')):
                tap_open = -1
        match = _SMOKE.match(line)
        detail = match['name'].strip() if match else line
        if _SUMMARY.fullmatch(detail) or _RUNNER_SUMMARY.fullmatch(detail):
            summaries.add(line)
            heading = ''
            continue
        if match:
            tag = (match['tag'] + ':') if match['tag'] else ''
            name = tag + (detail or '<unspecified>')
            names.add('smoke:' + name)
            heading = name if detail.endswith(':') else ''
            continue
        # boot-smoke prints multiple findings under one guard heading. Also keep
        # indented inline-handler/stack diagnostics in such a failure block.
        if heading and raw[:1].isspace():
            names.add('smoke:' + heading + ' ' + re.sub(r'^[•*\-]\s+', '', line))
            continue
        heading = ''
        # Additive: a known pytest/smoke failure must not hide another format.
        if _FAILURE.search(line):
            names.add(f'exit:{exit_code}:' + line)
    # The live `✖` lines carry no file; the recap's `test at FILE` does. Pair them up
    # by test name and order, so two files that both name a test `works` stay apart.
    # Only when the counts agree: a mismatch leaves the name unqualified (stable).
    live_by_name: dict[str, list[int]] = {}
    for index, name in spec_live:
        live_by_name.setdefault(name, []).append(index)
    for name, indexes in live_by_name.items():
        files = [file for recap_name, file in recap_files if recap_name == name]
        if len(files) == len(indexes) and all(files):
            for index, file in zip(indexes, files):
                counted[index] += ' @' + file
    if not names and not counted:
        lines = [_stable_line(line) for line in clean.splitlines() if line.strip()]
        names.add(f'exit:{exit_code}:' + (sorted(summaries) or lines or ['<no output>'])[-1])
    return _Scan(sorted([*names, *counted]), counted, containers)


def named_identities(signatures: list[str]) -> int:
    """How many distinct failures the signatures NAME (unproven file qualifiers excluded).

    pytest lists a failure up to twice (the `-r` short summary and the report heading), so
    the larger of the two lists is the count, not their sum. Generic lines (`exit:...`,
    including a `=== FAILURES ===` banner) and smoke lines name no test."""
    resolved = [s for s in signatures if _UNRESOLVED not in s]
    by_prefix = {p: sum(1 for s in resolved if s.startswith(p)) for p in _NAMED_PREFIXES}
    return max(by_prefix['pytest:'], by_prefix['pytest-report:']) + by_prefix['tap:'] + by_prefix['spec:']


# ── Identity proof (MC-1164 round 8) ───────────────────────────────────────────
# Free text from an arbitrary wrapper cannot be counted: every counting rule so far was fed
# surplus text (suite containers, a name a passing test printed, a second runner). So a red
# candidate is let through only when each command's output is ONE runner's own report: one
# runner family, one total line, names taken from that runner's report section, deduplicated,
# and exactly as many as the total. Anything else is `unprovable` and blocks.
_PYTEST_BANNER_NAMES = frozenset({'test session starts', 'FAILURES', 'ERRORS', 'warnings summary',
                                  'PASSES', 'short test summary info'})
_PYTEST_SUMMARY_BANNER = 'short test summary info'
# Every line pytest writes in its short summary section (-r chars f E p P s x X a A).
_PYTEST_SUMMARY_LINE = re.compile(r'^(?:FAILED|ERROR|PASSED|SKIPPED|XFAIL|XPASS)\b')
# `.F.  [100%]` / `test_x.py F  [ 50%]`: pytest's per-session progress; never inside a report.
_PYTEST_PROGRESS = re.compile(r'\[\s*\d{1,3}%\]\s*$')
_PYTEST_PROGRESS_DONE = re.compile(r'^(?:\S+\s+)?[.FEsxXQ]+\s+\[\s*100%\]$')
_PYTEST_NO_TESTS = re.compile(r'=*\s*no tests ran(?: in <duration>)?\s*=*')
_NODE_TOTAL = re.compile(r'(?:#|ℹ) tests \d+')
_NODE_SIGNAL = re.compile(
    r'TAP version \d+|(?:#|ℹ) (?:suites|pass|fail|cancelled|skipped|todo|duration_ms) [\d.]+|'
    r'✖ failing tests:?')
# Shell syntax that can change the directory or run a second thing: `cd a && node ...`,
# `a; b`, pipes, redirects, substitution, `%VAR%` / `^` (cmd), newlines.
_SHELL_SYNTAX = re.compile(r'[;&|<>`$()%^\r\n]')
_Row = tuple[str, str, 'str | None', str, bool]  # shown, stable, accepted banner, section, in_capture


def _direct_node(command: str) -> bool:
    """True only for a bare `node [flags] --test ...` invocation: no shell syntax, and
    `--test` among the leading flags. Node then runs in the verification cwd, so the
    relative paths it prints are relative to it. A wrapper (npm, python run.py, cd x && node,
    VAR=1 node) may run it anywhere, and its relative paths cannot be placed."""
    if _SHELL_SYNTAX.search(command):
        return False
    try:
        tokens = shlex.split(command)
    except ValueError:
        return False
    if not tokens or re.split(r'[\\/]', tokens[0])[-1].lower() not in ('node', 'node.exe'):
        return False
    for token in tokens[1:]:
        if not token.startswith('-'):
            return False
        if token == '--test':
            return True
    return False


def prove(output: str, exit_code: int, root: Path | None = None, command: str | None = None,
          scan: _Scan | None = None) -> dict[str, Any]:
    """Can this command's failures be told apart from another run's? Returns
    {'families', 'totals_lines', 'total', 'identities', 'reasons'}; `reasons` empty means
    provable. All of these must hold, else a reason is added and compare() blocks:

    1. exactly one recognised runner family (pytest or node) and exactly one total line;
    2. pytest names come from the LAST `short test summary info` section that sits
       immediately before the total line, else from the `____ name ____` headings of its
       FAILURES / ERRORS report. FAILED / ERROR text anywhere else (captured stdout, `-s`
       output, a traceback) is never a name;
    3. node suite / describe containers are not tests; only leaf tests (plus, in TAP, a
       parent test that has subtests) count, each with a resolved file qualifier;
    4. names are deduplicated and their number EQUALS the runner's failure total.

    Output with no recognised runner family at all (a smoke script, a crash before any
    report) has nothing to prove here: compare() judges it on its signatures alone, as before.
    """
    proof: dict[str, Any] = {'families': [], 'totals_lines': 0, 'total': 0, 'identities': [], 'reasons': []}
    if exit_code == 0:
        return proof
    reasons: list[str] = proof['reasons']
    frame = _PytestFrame()
    rows: list[_Row] = []
    for raw in _clean(output, root).splitlines():
        shown = raw.strip()
        if not shown:
            continue
        line = _stable_line(shown)
        banner = frame.feed(shown, line)
        rows.append((shown, line, banner, frame.section, frame.in_capture))
    pytest_totals = [i for i, row in enumerate(rows)
                     if _PYTEST_COUNTS.fullmatch(row[1]) or _PYTEST_NO_TESTS.fullmatch(row[1])]
    node_totals = [i for i, row in enumerate(rows) if _NODE_TOTAL.fullmatch(row[1])]
    banners = [(i, row[2]) for i, row in enumerate(rows) if row[2] and row[2] in _PYTEST_BANNER_NAMES]
    sessions = sum(1 for row in rows if _PYTEST_PROGRESS_DONE.fullmatch(row[1]))
    families = []
    if pytest_totals or banners or sessions:
        families.append('pytest')
    if node_totals or any(_NODE_SIGNAL.fullmatch(row[1]) for row in rows):
        families.append('node')
    proof['families'], proof['totals_lines'] = families, len(pytest_totals) + len(node_totals)
    if not families:
        return proof
    if len(families) > 1:
        reasons.append(f'ran more than one runner family ({", ".join(families)}) in one command')
    if proof['totals_lines'] != 1:
        reasons.append('printed no runner total line' if not proof['totals_lines']
                       else f'printed {proof["totals_lines"]} runner total lines, only exactly 1 is provable')
    if sessions > 1:
        reasons.append(f'ran pytest {sessions} times in one command')
    if len(families) != 1 or proof['totals_lines'] != 1:
        return proof
    if families == ['pytest']:
        total_at = pytest_totals[0]
        proof['total'] = sum(int(n) for n in _PYTEST_FAILED_PART.findall(rows[total_at][1]))
        identities = _pytest_identities(rows, total_at, banners, reasons)
    else:
        proof['total'] = sum(int(m['n']) for m in (_NODE_COUNTS.fullmatch(row[1]) for row in rows) if m)
        scan = scan or _scan(output, exit_code, root, command is None or _direct_node(command))
        identities = {
            entry for index, entry in enumerate(scan.counted)
            if index not in scan.containers and _UNRESOLVED not in entry and ' @' in entry}
    proof['identities'] = sorted(identities)
    if len(identities) < proof['total']:
        reasons.append(f'names {len(identities)} of {proof["total"]} failures the runner counted')
    elif len(identities) > proof['total']:
        reasons.append(f'names {len(identities)} failures but the runner counted only {proof["total"]}')
    return proof


def _pytest_identities(rows: list[_Row], total_at: int, banners: list[tuple[int, str]],
                       reasons: list[str]) -> set[str]:
    if any(index > total_at for index, _ in banners):
        reasons.append('pytest report sections follow the runner total line')
    summaries = [index for index, name in banners if name == _PYTEST_SUMMARY_BANNER and index < total_at]
    if summaries:
        found: set[str] = set()
        for row in rows[summaries[-1] + 1:total_at]:
            if not _PYTEST_SUMMARY_LINE.match(row[0]):
                reasons.append('the pytest short summary is not immediately followed by the runner total line')
                return set()
            match = _PYTEST.match(row[0].split(' - ', 1)[0])
            if match:
                found.add('pytest:' + match[1])
        if found:
            return found
    # No (usable) short summary, e.g. `-rN`: the report headings, only under a real
    # FAILURES / ERRORS banner. A pytest report never contains a progress line outside a
    # captured block, so one there means the "banner" was printed live by a test.
    headings: set[str] = set()
    for row in rows[:total_at]:
        if row[3] not in _PYTEST_FAILURE_BANNERS:
            continue
        if _PYTEST_PROGRESS.search(row[1]) and not row[4]:
            reasons.append('a progress line sits inside the pytest failure report (a test printed the banner)')
            return set()
        heading = _PYTEST_SECTION.match(row[1])
        if heading:
            headings.add('pytest-report:' + heading['name'])
    return headings


def compare(baseline: list[dict[str, Any]], candidate: list[dict[str, Any]]) -> dict[str, Any]:
    # Scope signatures by command position: one command cannot hide another.
    # A multiset: a failure the baseline has once and the candidate has twice is new.
    old = Counter(f'{i + 1}:{s}' for i, run in enumerate(baseline) for s in run['signatures'])
    current = Counter(f'{i + 1}:{s}' for i, run in enumerate(candidate) for s in run['signatures'])
    errors = [f'{phase}:{i + 1}:{run["error"]}' for phase, runs in
              (('baseline', baseline), ('candidate', candidate))
              for i, run in enumerate(runs) if run.get('error')]
    new = sorted((current - old).elements())
    # Backstop, and the "cannot prove it -> block" rule. The runner's own totals are an
    # ORDERED LIST per command position, never summed. When the candidate fails and the
    # totals cannot be compared one for one, the gate cannot prove the candidate has no
    # new failure, so it blocks and a human merges by hand: an AMBIGUOUS total on either
    # side, a different number of total lines, or any element that went up. A drop, or
    # passing-count noise, is fine. A candidate that exits 0 has nothing to prove.
    # The same holds for identity (round 7): a failure is "pre-existing" only if it is
    # NAMED. Either run must name at least as many failures as its runner counted, and
    # every file qualifier must resolve from the verification cwd; otherwise an equal
    # count could be a different failure (a swapped test under `--tb=no`, or the same
    # relative path in two subprojects) and the gate blocks.
    count_increases: list[str] = []
    unprovable: list[str] = []
    for i, (before, after) in enumerate(zip(baseline, candidate)):
        if after.get('exit_code') == 0:
            continue
        was, now = list(before.get('failure_totals') or []), list(after.get('failure_totals') or [])
        if AMBIGUOUS in was or AMBIGUOUS in now:
            side = ' and '.join(n for n, t in (('baseline', was), ('candidate', now)) if AMBIGUOUS in t)
            unprovable.append(f'{i + 1}:runner total ambiguous on {side} '
                              f'(baseline {len(was)} total lines, candidate {len(now)})')
        elif len(was) != len(now):
            unprovable.append(f'{i + 1}:runner total lines differ '
                              f'(baseline {len(was)}, candidate {len(now)})')
        else:
            for j, (w, n) in enumerate(zip(was, now)):
                if n > w:
                    where = '' if len(was) == 1 else f' #{j + 1}'
                    count_increases.append(f'{i + 1}:runner failure count{where} {w} -> {n}')
        for side, run_, totals in (('baseline', before, was), ('candidate', after, now)):
            signatures = list(run_.get('signatures') or [])
            unresolved = [s for s in signatures if _UNRESOLVED in s]
            if unresolved:
                unprovable.append(f'{i + 1}:test file path does not resolve from the verification '
                                  f'cwd on {side}: {unresolved[0]}')
            proof = run_.get('proof')
            if proof is None:
                # A hand-built run (no output to analyse): the weaker signature-count check.
                counted_by_runner = sum(t for t in totals if isinstance(t, int))
                named = named_identities(signatures)
                if named < counted_by_runner:
                    unprovable.append(f'{i + 1}:{side} names {named} of {counted_by_runner} failures '
                                      f'the runner counted')
            else:
                unprovable.extend(f'{i + 1}:{side} {reason}' for reason in proof['reasons'])
        # Round 8: a name only counts if it came from the runner's own report, so compare
        # those names directly; a name that also appears in the baseline's free text
        # (a passing test's print, a duplicate) must not hide a different failure.
        if before.get('proof') is not None and after.get('proof') is not None:
            known = set(before['proof']['identities'])
            new.extend(label for label in (f'{i + 1}:{name}' for name in after['proof']['identities']
                                           if name not in known) if label not in new)
    new.sort()
    return {'status': 'blocked' if new or errors or count_increases or unprovable else 'pass',
            'new_failures': new, 'count_increases': count_increases, 'unprovable': unprovable,
            'baseline_failures': sorted(old.elements()), 'errors': errors,
            'baseline': baseline, 'candidate': candidate}


def run_command(command: str, cwd: Path, timeout: float, project_id: str) -> dict[str, Any]:
    """Run one exact platform-shell command with the existing owned-tree cleanup."""
    from mc.blueprints.agent_routes import _register_process, _unregister_process
    from mc.blueprints.secrets_routes import _kill_process_tree, _win_job_object_for
    result: dict[str, Any] = {'exit_code': None, 'signatures': [], 'failure_totals': [],
                              'proof': None, 'output_tail': '', 'error': None}
    proc = None
    job = None
    with tempfile.TemporaryFile() as output:
        try:
            kwargs: dict[str, Any] = {'start_new_session': True} if os.name != 'nt' else {
                'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW}
            proc = subprocess.Popen(command, shell=True, cwd=cwd, stdin=subprocess.DEVNULL,
                                    stdout=output, stderr=subprocess.STDOUT, **kwargs)
            _register_process(proc, 'landing verification', 'agent_job', '', project_id)
            if os.name == 'nt':
                job = _win_job_object_for(proc.pid)
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                result['error'] = 'timeout'
                _kill_process_tree(proc, job)
                proc.wait(timeout=10)
            result['exit_code'] = proc.returncode
        except Exception as exc:
            result['error'] = f'command_unavailable:{type(exc).__name__}'
        finally:
            if proc is not None:
                # Also reap descendants of a shell that exits before its children.
                if proc.poll() is None:
                    _kill_process_tree(proc, job)
                    proc.wait(timeout=10)
                if os.name == 'nt' and job:
                    import ctypes
                    from ctypes import wintypes
                    ctypes.windll.kernel32.TerminateJobObject(job, 1)
                    ctypes.windll.kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
                    ctypes.windll.kernel32.CloseHandle(job)
                _unregister_process(proc.pid)
        size = output.tell()
        output.seek(max(0, size - TAIL_CHARS * 4))
        result['output_tail'] = output.read().decode('utf-8', 'replace')[-TAIL_CHARS:]
        if size > MAX_OUTPUT_BYTES:
            result['error'] = 'output_limit'
        elif result['exit_code'] is not None:
            output.seek(0)
            text = output.read().decode('utf-8', 'replace')
            scan = _scan(text, result['exit_code'], cwd, _direct_node(command))
            result['signatures'] = scan.signatures
            result['failure_totals'] = failure_totals(text, result['exit_code'], cwd)
            result['proof'] = prove(text, result['exit_code'], cwd, command, scan)
    return result


def _checkout(repo: Path, commit: str) -> None:
    git_bytes(repo, ['checkout', '--detach', commit])


def verify(project: dict[str, Any], worktree: Path, target: str, incoming: str,
           owner: Ownership, *, reviewer: Reviewer | None = None) -> dict[str, Any] | None:
    """Return None for the historical path. Reviewer is a future trusted callback.

    Its only accepted verdict is {'status': 'pass', ...}; absence is no extra
    policy. Existing human review approval remains owned by merge_review_gate.
    """
    if not project.get('verify_command') and reviewer is None:
        # False/0 are malformed settings, not an opt-out.
        if project.get('verify_command') is None or project.get('verify_command') in ('', []):
            return None
    result: dict[str, Any] = {'status': 'blocked', 'new_failures': [], 'count_increases': [],
        'unprovable': [],
        'baseline_failures': [], 'errors': [], 'baseline': [], 'candidate': []}
    restored = False
    original_ref = ''
    original_tip = ''
    borrowed = False
    try:
        assert_held(owner)
        commands, timeout = settings(project)
        target_tip = resolve_commit(worktree, target)
        incoming_tip = resolve_commit(worktree, incoming)
        original_tip = resolve_commit(worktree, 'HEAD')
        original_ref = git_bytes(worktree, ['symbolic-ref', 'HEAD']).decode().strip()
        if original_tip != incoming_tip or git_bytes(worktree, ['status', '--porcelain', '--untracked-files=normal']):
            raise ValueError('agent_checkout_changed')
        tree = merge_tree(worktree, target_tip, incoming_tip)
        result.update(target_sha=target_tip, incoming_sha=incoming_tip, merged_tree=tree)
        if commands:
            # Synthetic commit, no ref: make exactly the eventual merged tree checkoutable.
            merged = git_bytes(worktree, ['-c', 'user.name=Landing verification',
                '-c', 'user.email=landing@localhost', 'commit-tree', tree,
                '-p', target_tip, '-p', incoming_tip, '-m', 'Landing verification candidate']).decode().strip()
            key = hashlib.sha256(json.dumps([str(common_dir(worktree)), target_tip, commands, timeout,
                sorted(os.environ.items())], ensure_ascii=True).encode()).hexdigest()
            with _cache_lock:
                baseline = _cache.get(key)
                # Runtime dependencies/config are not in the key. Only a green
                # baseline is safe to reuse: it cannot exempt any new failure.
                if baseline is not None and not all(
                        run.get('exit_code') == 0 and not run.get('signatures') and not run.get('error')
                        for run in baseline):
                    del _cache[key]
                    baseline = None
                elif baseline is not None:
                    _cache.move_to_end(key)
            result['baseline_cached'] = baseline is not None
            borrowed = True
            if baseline is None:
                _checkout(worktree, target_tip)
                baseline = [run_command(c, worktree, timeout, project['id']) for c in commands]
                result['baseline'] = baseline
                if resolve_commit(worktree, 'HEAD') != target_tip:
                    raise ValueError('verification_checkout_changed')
            result['baseline'] = baseline
            # Never throw away tracked output written by a verifier. Such commands
            # are unsuitable for this borrowed checkout and block for inspection.
            if git_bytes(worktree, ['status', '--porcelain', '--untracked-files=no']):
                raise ValueError('verification_modified_tracked_files')
            if all(run.get('exit_code') == 0 and not run.get('signatures') and not run.get('error')
                   for run in baseline):
                with _cache_lock:
                    _cache[key] = baseline
                    while len(_cache) > 64:
                        _cache.popitem(last=False)
            _checkout(worktree, merged)
            candidate = [run_command(c, worktree, timeout, project['id']) for c in commands]
            result.update(compare(baseline, candidate))
            if resolve_commit(worktree, 'HEAD') != merged:
                raise ValueError('verification_checkout_changed')
            if git_bytes(worktree, ['status', '--porcelain', '--untracked-files=no']):
                raise ValueError('verification_modified_tracked_files')
        else:
            result['status'] = 'pass'
        if reviewer is not None:
            try:
                verdict = reviewer(dict(result))
                if not isinstance(verdict, dict):
                    raise ValueError('invalid_reviewer_verdict')
            except Exception as exc:
                verdict = {'status': 'blocked', 'error': type(exc).__name__}
            result['reviewer'] = verdict
            if verdict.get('status') != 'pass':
                result['status'] = 'blocked'
                result['errors'].append('reviewer_hold')
        if (resolve_commit(worktree, target) != target_tip
                or resolve_commit(worktree, incoming) != incoming_tip):
            raise ValueError('landing_inputs_changed')
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        result['status'] = 'blocked'
        result['errors'].append(str(exc) if isinstance(exc, ValueError) else type(exc).__name__)
    finally:
        if borrowed:
            try:
                # Ordinary checkout refuses verifier edits/collisions. No reset,
                # clean, deletion or force checkout may erase agent/test output.
                _checkout(worktree, original_tip)
                if resolve_commit(worktree, original_ref) != original_tip:
                    raise ValueError('agent_branch_changed')
                git_bytes(worktree, ['symbolic-ref', 'HEAD', original_ref])
                restored = True
            except (FreezeError, OSError, ValueError) as exc:
                result['status'] = 'blocked'
                result['errors'].append('checkout_restore_failed:' + str(exc))
        else:
            restored = True
    result['checkout_restored'] = restored
    return result
