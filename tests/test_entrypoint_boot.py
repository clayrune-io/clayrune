"""Both entry points must run the SAME startup path.

Regression for the 2026-09-02 finding: `app.py` — the desktop/pywebview
launcher, and the script build-macos.spec freezes — did
`from server import app, _start_scheduler`. That name has never existed at
server.py's top level (the scheduler lives in mc/blueprints/scheduler_routes),
so the import raised, killed app.py's Flask thread, and the frozen desktop app
served nothing at all. Even had it resolved, app.py would have started only the
scheduler: no session guardian, no worktree gc, no prior-stray reaping, no
builtin skill/MCP install, no auth probe.

These tests are deliberately STATIC (parse app.py, introspect server) rather
than executing boot() — boot() starts a dozen daemon threads and touches the
real data dir, which a unit test must not do.
"""
import ast
import inspect
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _app_py_tree():
    return ast.parse((REPO / "app.py").read_text(encoding="utf-8"))


def _server_imports_in_app():
    """Every `from server import ...` in app.py, as {name: [aliases]}."""
    out = []
    for node in ast.walk(_app_py_tree()):
        if isinstance(node, ast.ImportFrom) and node.module == "server":
            out.extend(a.name for a in node.names)
    return out


def test_app_py_only_imports_names_server_actually_exports():
    """The bug in one assertion: app.py may not import a name server lacks."""
    import server
    missing = [n for n in _server_imports_in_app() if not hasattr(server, n)]
    assert not missing, (
        f"app.py imports {missing} from server, which does not define them. "
        "This raises at desktop launch and kills the Flask thread.")


def test_server_exposes_a_reusable_boot():
    import server
    assert callable(getattr(server, "boot", None)), \
        "server.boot() is the shared startup path for both entry points."
    assert "check_port" in inspect.signature(server.boot).parameters


def test_app_py_calls_boot():
    """app.py must run the full startup path, not a hand-picked subset."""
    calls = [n.func.id for n in ast.walk(_app_py_tree())
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
    assert "boot" in calls, "app.py must call server.boot()"


def test_main_block_delegates_to_boot_and_adds_no_startup_work():
    """Startup work added to __main__ instead of boot() silently skips the
    desktop app — exactly how the two paths drifted apart in the first place.
    __main__ may only boot, log, and serve."""
    tree = ast.parse((REPO / "server.py").read_text(encoding="utf-8"))
    main = [n for n in tree.body
            if isinstance(n, ast.If) and ast.unparse(n.test) == "__name__ == '__main__'"]
    assert len(main) == 1
    called = [ast.unparse(n.func) for n in ast.walk(main[0])
              if isinstance(n, ast.Call)]
    allowed = {"boot", "_log", "_serve_dual_stack", "f-string"}
    stray = [c for c in called if c.split("(")[0] not in allowed
             and not c.startswith("_time.")]
    assert stray == [], f"__main__ does startup work outside boot(): {stray}"
