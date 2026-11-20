"""Fail the test that leaves the application's modules shadowed (MC-1212, MC-1223).

WHY THIS EXISTS
---------------
`server.py` is imported by bare name (`import server`) in about a hundred test
modules, and dozens of fixtures do `importlib.reload(server)` or pop it from
`sys.modules` and import it again to rebind its module-level paths to a
throwaway data dir. All three resolve the name through `sys.path` at that
moment, not through the module object they already hold.

`tools/mail-mcp/` holds another `server.py`, and both it and its sibling
`read_digest.py` prepend their own directory to `sys.path` when executed. Two
test loaders exec'd them and left that entry at `sys.path[0]`
(tests/test_mail_launder.py, tests/test_mail_header_charset.py). From then on,
in the same pytest process:

  * `importlib.reload(server)` ran the mail MCP server's code INSIDE the
    application's server module: nothing was rebound to the test's tmp dir,
    `server.CONFIG_PATH` became the mail tool's credential path, and tests saw
    each other's files (18 failures in four memory test files);
  * `sys.modules.pop('server'); import server` returned the mail MCP module
    for the rest of the session ("module 'server' has no attribute 'app'",
    126 errors in one 455-file order).

Nothing failed in the polluting test, so the reds landed on whichever files
happened to run later, and changed with the order.

WHAT IT CHECKS
--------------
After every test (and after every test module is imported at collection):

  1. `sys.path` gained no entry that now resolves a name AHEAD of the directory
     that owns it, for (a) every top-level module or package in the repo root
     and (b) every top-level module already imported. Only shadows that were
     not there before the test count, and the comparison runs only when
     `sys.path` changed at all.
  2. `sys.modules['server']`, when present, is still the repo root's
     `server.py`. A reload through a shadowed path flips `__file__` in place;
     a swap replaces the object. A stub with no `__file__` is not flagged.

A new shadow is removed from `sys.path` again after it is reported, so one
polluter costs one named red instead of a different set of victims per order.
A swapped or corrupted `server` module cannot be repaired from here.

A test that needs a directory on `sys.path` uses `monkeypatch.syspath_prepend`
or restores `sys.path` itself before it returns.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _norm(entry) -> str:
    try:
        return os.path.normcase(os.path.abspath(os.fspath(entry) or "."))
    except (TypeError, ValueError):
        return ""


_ROOT = _norm(_REPO_ROOT)
_APP_SERVER = os.path.join(_ROOT, "server.py")


class ImportShadowLeak(AssertionError):
    """A test left `sys.path` or `sys.modules['server']` pointing somewhere else."""


def _listing(directory: str) -> frozenset:
    try:
        return frozenset(os.listdir(directory))
    except OSError:
        return frozenset()


def _provides(directory: str, names: frozenset, name: str) -> bool:
    if name + ".py" in names:
        return True
    return name in names and os.path.isfile(os.path.join(directory, name, "__init__.py"))


def _root_names() -> frozenset:
    names = _listing(_ROOT)
    out = set()
    for n in names:
        if n.endswith(".py"):
            out.add(n[:-3])
        elif os.path.isfile(os.path.join(_ROOT, n, "__init__.py")):
            out.add(n)
    return frozenset(out)


_ROOT_NAMES = _root_names()


def _owners() -> dict:
    """{top-level name: normalised directory that is supposed to provide it}."""
    owners = {}
    for name, mod in list(sys.modules.items()):
        if "." in name:
            continue
        f = getattr(mod, "__file__", None)
        if not isinstance(f, str) or not f:
            continue
        d = os.path.dirname(_norm(f))
        if getattr(mod, "__path__", None) is not None:
            d = os.path.dirname(d)
        owners[name] = d
    # The repo root owns its own names whatever sys.modules currently holds:
    # a corrupted `server` must not make the shadowing directory the "owner".
    for name in _ROOT_NAMES:
        owners[name] = _ROOT
    return owners


def snapshot() -> list:
    return sys.path[:]


def _shadows(path: list) -> set:
    """{(raw sys.path entry, name)}: the entry resolves `name` before its owner does."""
    normed = [_norm(e) for e in path]
    first = {}
    for i, n in enumerate(normed):
        first.setdefault(n, i)
    owners = _owners()
    by_owner_index = {}
    for name, owner in owners.items():
        idx = first.get(owner)
        if idx:  # None: owner not importable by path; 0: nothing can precede it
            by_owner_index.setdefault(idx, []).append(name)
    if not by_owner_index:
        return set()
    last = max(by_owner_index)
    found = set()
    for i, (raw, n) in enumerate(zip(path, normed)):
        if i >= last:
            break
        if not n or first[n] != i:
            continue
        names = _listing(n)
        if not names:
            continue
        for idx, candidates in by_owner_index.items():
            if idx <= i:
                continue
            for name in candidates:
                if _provides(n, names, name):
                    found.add((raw, name))
    return found


def server_identity_problem() -> str | None:
    mod = sys.modules.get("server")
    f = getattr(mod, "__file__", None)
    if mod is None or not isinstance(f, str) or not f:
        return None
    if _norm(f) == _APP_SERVER:
        return None
    return (f"sys.modules['server'] is now {f} instead of {_APP_SERVER} "
            "(reloaded or re-imported through a shadowed sys.path, or swapped "
            "and not restored)")


def check_and_repair(before: list | None, server_was_ok: bool = True) -> list:
    """Problems introduced since `before` (a `snapshot()`), as readable lines.
    New shadowing entries are removed from `sys.path` before returning."""
    problems = []
    if before is not None and sys.path != before:
        new = _shadows(sys.path) - _shadows(before)
        if new:
            by_entry = {}
            for raw, name in new:
                by_entry.setdefault(raw, []).append(name)
            for raw, names in sorted(by_entry.items(), key=lambda kv: str(kv[0])):
                shown = ", ".join(sorted(names)[:6]) + (" ..." if len(names) > 6 else "")
                problems.append(
                    f"sys.path entry {raw!r} was left in place and now shadows: {shown}")
            # Remove the earliest occurrence of each offending entry until no
            # new shadow is left (bounded by the entries we found).
            for _ in range(len(sys.path)):
                still = _shadows(sys.path) - _shadows(before)
                if not still:
                    break
                raw = next(iter(still))[0]
                try:
                    sys.path.remove(raw)
                except ValueError:
                    break
    if server_was_ok:
        ident = server_identity_problem()
        if ident:
            problems.append(ident)
    return problems


def message(problems: list, where: str) -> str:
    return (f"{where} left the import system pointing away from the application "
            "(tests/import_shadow_guard.py):\n  " + "\n  ".join(problems) +
            "\nUse monkeypatch.syspath_prepend / monkeypatch.setitem(sys.modules, ...), "
            "or restore what was changed before returning.")
