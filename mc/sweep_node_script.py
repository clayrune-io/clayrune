"""Is a process a node image running a script out of a given directory?

MC-1179 AP9: orphan-sweep criterion 1 used to be met by any command line that
merely contained a CLI package directory, so an editor or pager opened on a file
inside it matched. The only command line that proves "this process runs that
CLI" is a node image whose script argument is a file under the package dir.

This only narrows. The caller keeps its textual check and ANDs this one in, so
nothing this module says can make a process match that did not match before. Any
command line it cannot read with confidence (a relative script, `-e`/`-p`, no
script at all) is "no match".
"""
from __future__ import annotations

import os
import re
from typing import Any, List, Mapping, Optional

_NODE_IMAGES = frozenset({'node', 'node.exe'})
# node options whose value is the NEXT token (the `--opt=value` form is one token).
_VALUE_FLAGS = frozenset({
    '-r', '--require', '--import', '--loader', '--experimental-loader',
    '-C', '--conditions', '--input-type', '--title', '--stack-trace-limit',
    '--max-old-space-size', '--max-semi-space-size', '--inspect-port',
})
# options after which the rest is code or a REPL, not a script path.
_CODE_FLAGS = frozenset({'-e', '--eval', '-p', '--print', '-pe', '-i', '--interactive', '-c', '--check'})
_TOKEN = re.compile(r'"([^"]*)"|(\S+)')


def _tokens(cmdline: str) -> List[str]:
    return [(m.group(1) if m.group(1) is not None else m.group(2))
            for m in _TOKEN.finditer(cmdline)]


def _is_node(proc: Mapping[str, Any]) -> bool:
    for key in ('name', 'exe'):
        value = proc.get(key)
        if value and os.path.basename(str(value).replace('\\', '/')).lower() in _NODE_IMAGES:
            return True
    return False


def script_argument(cmdline: Optional[str]) -> Optional[str]:
    """The script a `node ...` command line runs, or None when there is none to read."""
    tokens = _tokens(cmdline or '')
    rest = iter(tokens[1:])
    for tok in rest:
        if tok == '--':
            return next(rest, None)
        if tok in _CODE_FLAGS:
            return None
        if tok in _VALUE_FLAGS:
            next(rest, None)
            continue
        if tok.startswith('-'):
            continue
        return tok
    return None


def node_script_under(proc: Mapping[str, Any], dir_path: str) -> bool:
    """True when `proc` is a node image whose absolute script argument is
    `dir_path` itself or a path under it."""
    if not dir_path or not _is_node(proc):
        return False
    script = script_argument(proc.get('cmdline'))
    if not script or not os.path.isabs(script):
        return False
    base = os.path.normcase(os.path.abspath(dir_path))
    path = os.path.normcase(os.path.abspath(script))
    return path == base or path.startswith(base + os.sep)
