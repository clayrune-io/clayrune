"""Undo method patches a test left on a shared runtime instance (MC-1212, MC-1223).

WHY THIS EXISTS
---------------
`mc.agent_runtime._RUNTIMES` holds ONE instance per provider for the life of the
process, and `get_runtime('claude')` hands that same object to every test and
to every route.

`monkeypatch.setattr(rt, 'resolve_binary', fake)` on such an instance does not
undo to "no instance attribute". pytest records `getattr(rt, 'resolve_binary')`,
which for a method defined on the class is a BOUND METHOD, and its undo assigns
that bound method back onto the instance. The instance now carries
`rt.__dict__['resolve_binary']` for the rest of the run, and it wins over the
class: a later `patch.object(ClaudeRuntime, 'resolve_binary', ...)` changes
nothing the route can see.

Measured 2026-11-19 with two test ids:

    tests/test_claude_runtime.py::test_claude_oneshot_available_false_when_binary_missing
    tests/test_auth_routes.py::TestAuthRoutes::test_claude_login_launch_shim_calls_generic

The second patches the class to `/fake/claude`, the route resolved this
machine's real `claude.CMD`, and its sibling
`TestRemoteLogin::test_remote_login_binary_missing_400` went on to start a real
`claude auth login` (refused by the CLI guard in conftest.py). The same shape
with a direct `rt.resolve_binary = ...` assignment opened a host terminal on
every full run until tests/test_agent_spawn.py was fixed on 2026-11-18.

WHAT IT DOES
------------
After every test, once all fixtures (monkeypatch included) are torn down, any
entry in a registered runtime's instance `__dict__` that shadows a function,
staticmethod, classmethod or property of its class, and was not there when the
test started, is deleted. That is the state the test found. Data attributes
are not touched.

`MC_RUNTIME_SHADOW_REPORT=<file>` appends one line per removal
(`<test id>\t<provider>.<attr>`), to list the tests that patch the shared
instance.
"""
from __future__ import annotations

import inspect
import os
import sys
import types

_METHOD_KINDS = (types.FunctionType, staticmethod, classmethod, property)
_REPORT = os.environ.get("MC_RUNTIME_SHADOW_REPORT", "").strip()


def _runtimes() -> dict:
    # From sys.modules, never a fresh import: see _isolated_allowance_state in
    # conftest.py for why a fixture must not import mc modules itself.
    mod = sys.modules.get("mc.agent_runtime")
    reg = getattr(mod, "_RUNTIMES", None)
    return dict(reg) if isinstance(reg, dict) else {}


def _shadowing(rt) -> set:
    try:
        names = list(vars(rt))
    except TypeError:
        return set()
    cls = type(rt)
    return {n for n in names
            if isinstance(inspect.getattr_static(cls, n, None), _METHOD_KINDS)}


def snapshot() -> dict:
    """{id(runtime instance): names already shadowing a class method}."""
    return {id(rt): _shadowing(rt) for rt in _runtimes().values() if rt is not None}


def restore(before: dict | None, where: str = "") -> list:
    """Delete method shadows added since `before`; returns ['provider.attr', ...]."""
    removed = []
    before = before or {}
    for name, rt in _runtimes().items():
        if rt is None:
            continue
        for attr in sorted(_shadowing(rt) - before.get(id(rt), set())):
            try:
                del rt.__dict__[attr]
            except (KeyError, TypeError, AttributeError):
                continue
            removed.append(f"{name}.{attr}")
    if removed and _REPORT:
        try:
            with open(_REPORT, "a", encoding="utf-8") as f:
                for r in removed:
                    f.write(f"{where}\t{r}\n")
        except OSError:
            pass
    return removed
