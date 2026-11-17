"""Kill-command analysis for ``mc/process_guard.py`` (MC-1190, review pass 10 AP1).

``process_guard`` is a tripwire for two things SHARED_RULES forbids: killing a
process by image name, and killing the PID that listens on the Clayrune port.
The regexes it started with only matched the documented spelling of each.
This module finds the kill command word first, takes that command's own
segment, and then looks at the WHOLE segment: every integer in it, every
name-selecting parameter, and (for forms whose PIDs cannot be read from the
text) whatever produced the PIDs.

Stdlib only, and imported by bare name as a fallback, because the installed
hook runs ``python <repo>/mc/process_guard.py`` as a script (``sys.path[0]``
is ``mc/``, not the repo root).

Still a tripwire, not a sandbox.  It only ever adds refusals: the original
patterns in ``process_guard`` keep running first.

What is refused when the PIDs cannot be read from the text (all of it only
when the thing that yields the PIDs is a lookup by port or by name):

* a command substitution, backtick or parenthesised group inside the kill
  command that contains a lookup: ``kill $(lsof -ti :5199)``,
  ``Stop-Process -Id (Get-NetTCPConnection ...).OwningProcess``;
* a variable, loop variable or ``while read`` variable that was assigned from
  a lookup earlier in the same command, then used in a kill;
* a pipeline whose earlier stage is a lookup and whose later stage is a kill:
  ``lsof -ti :5199 | xargs kill``, ``Get-NetTCPConnection ... | % { kill }``.

Lookups: ``lsof netstat fuser pidof pgrep ss ps -e/-a/aux tasklist wmic
Get-Process gps Get-NetTCPConnection Get-NetUDPConnection Get-CimInstance
Get-WmiObject gwmi gcim OwningProcess GetProcessesByName``.

Let through (the PID is the agent's own, or not a lookup by port/name):
``kill $!``, ``kill $pid``, ``Stop-Process -Id $p.Id``, ``$p.Kill()`` on a
``Start-Process -PassThru`` handle, ``kill $(cat pidfile)``, ``kill $(jobs
-p)``, ``kill $(pgrep -P $$)`` (children of the caller), and a lookup that
sits in a different statement from the kill.
"""

from __future__ import annotations

import re
from typing import Callable, Iterable, Iterator, List, Optional, Set, Tuple, Union

ListenerSource = Union[Iterable[str], Callable[[], Iterable[str]]]


class LazyPids:
    """The listener PIDs, resolved at most once and only when first asked.

    ``source`` is either the PIDs themselves or a zero-argument callable that
    produces them.  The lookup behind the callable is a ``netstat`` subprocess
    (53.5 ms measured); most shell commands contain no kill, so it must not
    run until a kill with an integer in it has been found.
    """

    def __init__(self, source: ListenerSource):
        self._source = source
        self._pids: Optional[Set[str]] = None

    def get(self) -> Set[str]:
        if self._pids is None:
            raw = self._source() if callable(self._source) else self._source
            self._pids = {str(pid).strip() for pid in raw if str(pid).strip()}
        return self._pids


_KILL_WORD = re.compile(r"(?<![\w.-])(taskkill|stop-process|spps|kill)(?:\.exe)?(?=\s)", re.I)
_PS_KILLERS = {"stop-process", "spps"}

# Name-selecting parameters.  Stop-Process takes unambiguous prefixes
# (-Na, -Pr ...); `kill` is also bash's, where `-n` is a signal switch, so for
# that word only the full spellings count.
_NAME_PARAM_PS = re.compile(
    r"(?<![\w-])-(?:n(?:a(?:m(?:e)?)?)?|pr(?:o(?:c(?:e(?:s(?:s(?:n(?:a(?:m(?:e)?)?)?)?)?)?)?)?)?)(?![\w-])",
    re.I)
_NAME_PARAM_FULL = re.compile(r"(?<![\w-])-(?:name|processname)(?![\w-])", re.I)
_TASKKILL_IMAGE = re.compile(r"""(?<![\w-])[/-](?:im\s+\S|fi\s+["']?imagename\b)""", re.I)

_INT = re.compile(r"(?<![\w$.])\d+(?![\w.])")

# A lookup of a process or listener by port or by name.
_LOOKUP = re.compile(
    r"""(?<![\w.-])(?:
          lsof | netstat | fuser | pidof | tasklist | wmic
        | pgrep(?!\s+(?:-P|--parent)\b)
        | get-nettcpconnection | get-netudpconnection
        | get-process | gps | get-ciminstance | get-wmiobject | gwmi | gcim
        | ss(?=\s+-)
        | ps(?=\s+(?:-[A-Za-z]*[aAeC][A-Za-z]*|aux|ax)(?![\w-]))
        )(?![\w-])
        | owningprocess | getprocessesbyname
        | \[system\.diagnostics\.process\]::getprocesses
    """, re.I | re.X)

_KILL_AFTER_PIPE = re.compile(
    r"\|[\s\S]*?(?<![\w-])(?:kill|stop-process|spps|taskkill)(?![\w-])", re.I)

# Statement-level image-name spellings (a statement is one `;`/`&&`/newline
# separated command, braces and parentheses kept whole).
_STATEMENT_IMAGE = (
    re.compile(r"(?<![\w-])(?:get-process|gps)(?![\w-])[\s\S]*\|\s*(?:stop-process|spps|kill)(?![\w-])", re.I),
    re.compile(r"(?<![\w-])ps\s+[\w*.'\"-]+\s*\|\s*(?:stop-process|spps|kill)(?![\w-])", re.I),
    re.compile(r"(?<![\w-])(?:get-process|gps)(?![\w-])[\s\S]*?\.\s*kill\s*\(", re.I),
    re.compile(r"getprocesses(?:byname)?\s*\([\s\S]*?(?:\.\s*kill\s*\(|\|\s*(?:stop-process|spps|kill)(?![\w-]))", re.I),
    re.compile(r"(?<![\w-])(?:get-ciminstance|get-wmiobject|gwmi|gcim)(?![\w-])[\s\S]*\bterminate\b", re.I),
    re.compile(r"(?<![\w-])invoke-(?:cim|wmi)method(?![\w-])[\s\S]*\bterminate\b", re.I),
)

_ASSIGN = re.compile(
    r"(?:^|[\s({])(?:(?:export|local|declare|readonly|set)\s+(?:-\w+\s+)*)?\$?(\w+)\s*=(?!=)\s*([\s\S]*)")
_FOR_IN = re.compile(r"(?<![\w-])for\s+(\w+)\s+in\b([\s\S]*)", re.I)
_FOREACH_PS = re.compile(r"(?<![\w-])foreach\s*\(\s*\$(\w+)\s+in\b([\s\S]*)", re.I)
_WHILE_READ = re.compile(
    r"\|\s*while\s+(?:IFS=\S*\s+)?read\s+(?:-\w+\s+)*(\w+)", re.I)

_QUOTES = "\"'"


def _statements(text: str) -> List[str]:
    """Split on ``;`` ``&&`` ``||`` newline and a lone ``&``, at nesting depth 0
    and outside quotes.  ``$(...)``, ``(...)`` and ``{...}`` stay whole."""
    out: List[str] = []
    buf: List[str] = []
    depth = 0
    quote = ""
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if quote:
            buf.append(c)
            if c == quote:
                quote = ""
        elif c == "\\" and i + 1 < n and (text[i + 1] in _QUOTES or text[i + 1] == "\n"):
            buf.append(text[i:i + 2])
            i += 1
        elif c in _QUOTES:
            quote = c
            buf.append(c)
        elif c in "({[":
            depth += 1
            buf.append(c)
        elif c in ")}]":
            depth = max(0, depth - 1)
            buf.append(c)
        elif depth == 0 and (c in ";\n" or (c in "&|" and text[i + 1:i + 2] == c)
                             or (c == "&" and not _is_redirect_amp(text, i))):
            out.append("".join(buf))
            buf = []
            if c in "&|" and text[i + 1:i + 2] == c:
                i += 1
        else:
            buf.append(c)
        i += 1
    out.append("".join(buf))
    return [s for s in out if s.strip()]


def _is_redirect_amp(text: str, i: int) -> bool:
    """``2>&1`` / ``&>file`` / ``>&2``: an ampersand that is not a separator."""
    return text[i - 1:i] in (">", "<") or text[i + 1:i + 2] == ">"


def _segment_end(text: str, start: int) -> int:
    """End of the kill command that begins before ``start``: the next
    separator at depth 0 (``;`` newline ``|`` ``&``), or an unbalanced closer.
    Quotes opened inside the segment are skipped over, so a quoted PID list is
    seen; a closing quote of an OUTER string (``bash -c "kill 1"``) reads as an
    opening one, which only ever lengthens the segment (more refusals, never
    fewer)."""
    depth = 0
    quote = ""
    backtick = False
    i = start
    n = len(text)
    while i < n:
        c = text[i]
        if quote:
            if c == quote:
                quote = ""
        elif c == "\\" and i + 1 < n and text[i + 1] in "\n\r":
            i += 1
        elif c in _QUOTES:
            quote = c
        elif c == "`":
            backtick = not backtick
        elif c in "({[":
            depth += 1
        elif c in ")}]":
            if depth == 0:
                return i
            depth -= 1
        elif depth == 0 and not backtick and (c in ";\n|" or (c == "&" and not _is_redirect_amp(text, i))):
            return i
        i += 1
    return n


def _kill_segments(text: str) -> Iterator[Tuple[str, str]]:
    for match in _KILL_WORD.finditer(text):
        yield match.group(1).lower(), text[match.start():_segment_end(text, match.end())]


def _tainted_variables(statements: List[str]) -> Set[str]:
    """Names assigned (or loop-bound) from a lookup by port or by name."""
    names: Set[str] = set()
    for stmt in statements:
        for m in _ASSIGN.finditer(stmt):
            if _LOOKUP.search(m.group(2)):
                names.add(m.group(1))
        for pattern in (_FOR_IN, _FOREACH_PS):
            for m in pattern.finditer(stmt):
                if _LOOKUP.search(m.group(2)):
                    names.add(m.group(1))
        for m in _WHILE_READ.finditer(stmt):
            if _LOOKUP.search(stmt[:m.start()]):
                names.add(m.group(1))
    return names


def _image_message(fragment: str) -> str:
    return ("process guard: image-name termination is blocked "
            f"({_clip(fragment)!r}); only terminate a PID you spawned")


def _lookup_message(fragment: str) -> str:
    return ("process guard: refusing to terminate a PID that was looked up by port "
            f"or by name ({_clip(fragment)!r}); kill only the PID you captured from "
            "your own spawn ($!, $pid, $p.Id)")


def _clip(fragment: str, limit: int = 120) -> str:
    fragment = " ".join(fragment.split())
    return fragment if len(fragment) <= limit else fragment[:limit] + "..."


def find_violation(command: str, listeners: LazyPids) -> Optional[str]:
    """Return a block reason for ``command``, or ``None``.

    ``listeners`` is only read when a kill command carrying an integer has
    been found."""
    statements = _statements(command)

    for stmt in statements:
        for pattern in _STATEMENT_IMAGE:
            m = pattern.search(stmt)
            if m:
                return _image_message(m.group(0))

    tainted = _tainted_variables(statements)
    taint_ref = (re.compile(r"\$\{?(?:%s)(?![\w])" % "|".join(map(re.escape, sorted(tainted))))
                 if tainted else None)

    for word, segment in _kill_segments(command):
        if word == "taskkill":
            m = _TASKKILL_IMAGE.search(segment)
            if m:
                return _image_message(segment)
        else:
            name = _NAME_PARAM_PS if word in _PS_KILLERS else _NAME_PARAM_FULL
            if name.search(segment):
                return _image_message(segment)
        if ("(" in segment or "`" in segment) and _LOOKUP.search(segment):
            return _lookup_message(segment)
        if taint_ref is not None and taint_ref.search(segment):
            return _lookup_message(segment)

    for stmt in statements:
        source = _LOOKUP.search(stmt)
        if taint_ref is not None:
            ref = taint_ref.search(stmt)
            if ref and (source is None or ref.start() < source.start()):
                source = ref
        if source is not None:
            if _KILL_AFTER_PIPE.search(stmt, source.end()):
                return _lookup_message(stmt)
        if taint_ref is not None and re.search(
                taint_ref.pattern + r"\s*\.\s*kill\s*\(", stmt, re.I):
            return _lookup_message(stmt)

    for _, segment in _kill_segments(command):
        for m in _INT.finditer(segment):
            if m.group(0) in listeners.get():
                return ("process guard: refusing to terminate Clayrune listener "
                        f"PID {m.group(0)}")
    return None
