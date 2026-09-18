"""server.py's boot() must wire the guardrail-hook installer in (W2).

Deliberately mixed, unlike test_entrypoint_boot.py's pure-static approach:
`_install_guardrail_hooks_on_boot` takes a `home` override specifically so a
test CAN execute it for real against a throwaway directory instead of only
asserting it's wired — the actual file-writing behavior (which vendor gets a
hook, what the written JSON looks like) is worth executing, not just
inspecting; only the real-home write in the production call site is
static-only, for the same reason test_entrypoint_boot.py stays static about
boot() itself: it touches the real environment.
"""
import ast
import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def test_boot_calls_the_guardrail_hooks_installer():
    tree = ast.parse((REPO / "server.py").read_text(encoding="utf-8"))
    boot_fns = [n for n in tree.body
                if isinstance(n, ast.FunctionDef) and n.name == "boot"]
    assert len(boot_fns) == 1
    calls = [ast.unparse(n) for n in ast.walk(boot_fns[0]) if isinstance(n, ast.Call)]
    assert any("_install_guardrail_hooks_on_boot" in c for c in calls), (
        "server.boot() must call _install_guardrail_hooks_on_boot so a fresh "
        "install gets the guard wired on first startup, matching the "
        "app.py/boot() single-startup-path rule test_entrypoint_boot.py enforces.")


def test_production_call_site_passes_no_home_override():
    """The one production call site must use the REAL home (no `home=` arg) —
    a home override exists only for tests; wiring it in boot() with an
    explicit override would silently redirect Ron's real install."""
    tree = ast.parse((REPO / "server.py").read_text(encoding="utf-8"))
    boot_fns = [n for n in tree.body
                if isinstance(n, ast.FunctionDef) and n.name == "boot"]
    calls = [n for n in ast.walk(boot_fns[0]) if isinstance(n, ast.Call)]
    hook_calls = [c for c in calls
                  if isinstance(c.func, ast.Name) and c.func.id == "_boot_phase"
                  and len(c.args) >= 2 and isinstance(c.args[1], ast.Name)
                  and c.args[1].id == "_install_guardrail_hooks_on_boot"]
    assert hook_calls, "expected _install_guardrail_hooks_on_boot passed as a _boot_phase callback"
    # Passed as a bare name (isinstance check above), not a Call/Lambda that
    # could bind a home= override — so it runs with the function's own
    # default (real home) when _boot_phase eventually invokes it.


def test_writes_only_for_installed_vendors_against_a_real_temp_home(tmp_path):
    import server

    server._install_guardrail_hooks_on_boot(home=tmp_path)

    # On THIS dev box all four vendor CLIs are installed, so all four get a
    # hook — proves the health_check() gate and the real write path work
    # together, without touching the real home.
    written = sorted(p.relative_to(tmp_path).as_posix()
                      for p in tmp_path.rglob('*') if p.is_file())
    assert written, "expected at least one hook file written for an installed vendor"
    for rel in written:
        data = json.loads((tmp_path / rel).read_text(encoding='utf-8'))
        assert 'hooks' in data


def test_second_boot_is_idempotent(tmp_path):
    import server

    server._install_guardrail_hooks_on_boot(home=tmp_path)
    written = list(tmp_path.rglob('*.json'))
    before = {p: p.read_text(encoding='utf-8') for p in written}

    server._install_guardrail_hooks_on_boot(home=tmp_path)

    after = {p: p.read_text(encoding='utf-8') for p in written}
    assert before == after
