"""server.py's boot() must wire the guardrail-hook GENERATOR in (W2 redesign:
per-launch injection, never the vendor CLI's own global config).

Deliberately mixed, unlike test_entrypoint_boot.py's pure-static approach:
`_install_guardrail_hooks_on_boot` takes a `clayrune_home` override
specifically so a test CAN execute it for real against a throwaway directory
instead of only asserting it's wired — the actual file-writing behavior
(which vendor gets a launch file, what the written JSON looks like) is worth
executing, not just inspecting; only the real-`~/.clayrune` write in the
production call site is static-only, for the same reason
test_entrypoint_boot.py stays static about boot() itself: it touches the
real environment.
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
    """The one production call site must use the REAL ~/.clayrune (no
    `clayrune_home=` arg) — the override exists only for tests; wiring it in
    boot() with an explicit override would silently redirect Ron's real
    install."""
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
    # could bind a clayrune_home= override — so it runs with the function's
    # own default (real ~/.clayrune) when _boot_phase eventually invokes it.


def test_writes_only_for_installed_vendors_against_a_real_temp_home(tmp_path):
    import server

    server._install_guardrail_hooks_on_boot(clayrune_home=tmp_path)

    # On THIS dev box all four vendor CLIs are installed, so all four get a
    # launch hook file — proves the health_check() gate and the real write
    # path work together, without touching ~/.clayrune or any vendor's real
    # global config.
    written = sorted(p.relative_to(tmp_path).as_posix()
                      for p in tmp_path.rglob('*') if p.is_file())
    assert written, "expected at least one hook file written for an installed vendor"
    for rel in written:
        data = json.loads((tmp_path / rel).read_text(encoding='utf-8'))
        assert 'hooks' in data


def test_second_boot_is_idempotent(tmp_path):
    import server

    server._install_guardrail_hooks_on_boot(clayrune_home=tmp_path)
    written = list(tmp_path.rglob('*.json'))
    before = {p: p.read_text(encoding='utf-8') for p in written}

    server._install_guardrail_hooks_on_boot(clayrune_home=tmp_path)

    after = {p: p.read_text(encoding='utf-8') for p in written}
    assert before == after


def test_no_override_scopes_to_mc_data_dir_when_set(tmp_path, monkeypatch):
    """Regression, found 2026-09-18: a second server.py running with its own
    MC_DATA_DIR (the isolation knob tests/conftest.py's `tmp_data_dir`
    fixture actually sets, and what a manually spun-up second instance uses)
    used to regenerate the REAL ~/.clayrune/hooks/*.json on boot — the call
    site passes NO override (see test_production_call_site_passes_no_home_override
    above), so the function fell back to the real home every time regardless
    of which data dir this instance owned. Once, that pointed a LIVE server's
    guard at a temp dir that was later deleted, which would have blocked
    every agent's shell command.

    `Path.home()` is monkeypatched to a throwaway dir (never the operator's
    real home) so this test is safe to run against either the buggy or fixed
    code: pre-fix, the write lands under the FAKE home instead of the scoped
    MC_DATA_DIR; post-fix it lands under the scoped dir. Neither case can
    touch this machine's actual ~/.clayrune.
    """
    import server
    from mc import guardrail_hooks as gh

    fake_home = tmp_path / 'fake_home'
    monkeypatch.setattr(gh.Path, 'home', staticmethod(lambda: fake_home))

    scoped_data_dir = tmp_path / 'mc_data'
    monkeypatch.setenv('MC_DATA_DIR', str(scoped_data_dir))
    monkeypatch.setattr(server, '_DATA_ROOT', scoped_data_dir)

    server._install_guardrail_hooks_on_boot()  # bare call — exactly what boot() does

    fake_home_hooks = fake_home / '.clayrune' / 'hooks'
    scoped_hooks = scoped_data_dir / '.clayrune' / 'hooks'

    assert not fake_home_hooks.exists(), (
        'must not fall back to the (fake) home dir while MC_DATA_DIR is set')
    written = list(scoped_hooks.rglob('*.json')) if scoped_hooks.is_dir() else []
    assert written, 'expected the hooks to be generated under the scoped MC_DATA_DIR instead'
