"""MC-1179 ML16, the default: `condense_mode` is 'structured' when unset.

'agent' launches a tool-capable agent; 'structured' is one toolless model
call applied by the server. The shipped default was 'agent', spelled as a
literal in four places. It is now one constant, `mc.condense_mode.DEFAULT`.

What is pinned:
  * a config with no `condense_mode` key takes the structured path at both
    call sites (the trigger and the dispatcher);
  * a config that says 'agent' still takes the agent path, and loading it
    does not rewrite it;
  * the default is spelled once, and the Settings page agrees with it.

No model is called: the structured runner and `subprocess.Popen` are replaced.
"""
import importlib
import json
import re
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def s(tmp_data_dir, monkeypatch):
    srv = importlib.import_module("server")
    importlib.reload(srv)
    import mc.memory as m
    import mc.state as state
    monkeypatch.setattr(m, "_native_memory_path", lambda pp: None)
    monkeypatch.setitem(state.CONFIG, "condense_enabled", True)
    monkeypatch.setitem(state.CONFIG, "condense_threshold_kb", 30)
    monkeypatch.setitem(state.CONFIG, "index_line_budget", 10)
    m._condense_triggered_at.clear()
    m._condensing_projects.clear()
    yield m
    m._condense_triggered_at.clear()
    m._condensing_projects.clear()


def _set_mode(monkeypatch, value):
    """`value=None` removes the key: a config that never had one."""
    import mc.state as state
    if value is None:
        monkeypatch.delitem(state.CONFIG, "condense_mode", raising=False)
    else:
        monkeypatch.setitem(state.CONFIG, "condense_mode", value)


def _small_project_over_its_line_budget(s):
    """40 managed entries (~1 KB) against a 10-line budget and a 30 KB size
    gate. The two modes disagree here, which is what tells them apart: the
    structured trigger fires on the line budget; the agent trigger does not,
    because the files are far under its size gate."""
    p = {"id": "tproj"}
    mp = s._get_memory_path(p)
    mp.parent.mkdir(parents=True, exist_ok=True)
    mp.write_text(s._mem_compose(
        "# Index", [f"- [d] **e{i}** x" for i in range(40)], []),
        encoding="utf-8")
    return p


def _dispatch(s, p, monkeypatch):
    """Run `_dispatch_condense` with both executors replaced; say which ran."""
    ran = []

    class _NoLaunch(Exception):
        pass

    def _fake_popen(cmd, *a, **k):
        ran.append("agent")
        raise _NoLaunch("test: no process is started")

    def _fake_structured(project):
        ran.append("structured")
        with s._condense_lock:
            s._condensing_projects.discard(project["id"])

    monkeypatch.setattr(s.subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(s, "_run_structured_condense", _fake_structured)
    monkeypatch.setattr(s, "_resolve_claude", lambda: "claude-test-double")
    monkeypatch.setattr(s, "_model_for_provider", lambda *a, **k: "")
    s._dispatch_condense(p)
    deadline = time.time() + 10
    while p["id"] in s._condensing_projects and time.time() < deadline:
        time.sleep(0.02)
    return ran


# ── both call sites ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("value", [None, "", "structured"])
def test_trigger_takes_the_structured_path_when_unset(s, monkeypatch, value):
    _set_mode(monkeypatch, value)
    p = _small_project_over_its_line_budget(s)
    assert s._should_condense(p, include_claude_md=True) is True


def test_trigger_takes_the_agent_path_when_config_says_agent(s, monkeypatch):
    _set_mode(monkeypatch, "agent")
    p = _small_project_over_its_line_budget(s)
    assert s._should_condense(p, include_claude_md=True) is False


@pytest.mark.parametrize("value", [None, "", "structured"])
def test_dispatch_runs_the_structured_executor_when_unset(s, monkeypatch, value):
    _set_mode(monkeypatch, value)
    p = _small_project_over_its_line_budget(s)
    assert _dispatch(s, p, monkeypatch) == ["structured"]


def test_dispatch_launches_the_agent_when_config_says_agent(s, monkeypatch):
    _set_mode(monkeypatch, "agent")
    p = _small_project_over_its_line_budget(s)
    assert _dispatch(s, p, monkeypatch) == ["agent"]


# ── what a config file gets ──────────────────────────────────────────────────

def test_an_install_with_no_key_reads_structured(tmp_data_dir):
    (tmp_data_dir / "config.json").write_text(
        json.dumps({"port": 0}), encoding="utf-8")
    srv = importlib.reload(importlib.import_module("server"))
    assert srv.CONFIG["condense_mode"] == "structured"


def test_no_config_file_reads_structured(tmp_data_dir):
    assert not (tmp_data_dir / "config.json").exists()
    srv = importlib.reload(importlib.import_module("server"))
    assert srv.CONFIG["condense_mode"] == "structured"


def test_an_explicit_agent_is_kept_and_the_file_is_not_rewritten(tmp_data_dir):
    cfg = tmp_data_dir / "config.json"
    text = json.dumps({"port": 0, "condense_mode": "agent",
                       "desk_v1_default_migrated": True}, indent=2)
    cfg.write_text(text, encoding="utf-8")
    srv = importlib.reload(importlib.import_module("server"))
    assert srv.CONFIG["condense_mode"] == "agent"
    assert json.loads(cfg.read_text(encoding="utf-8"))["condense_mode"] == "agent"


# ── spelled once ─────────────────────────────────────────────────────────────

def test_the_default_is_one_constant():
    from mc import condense_mode
    assert condense_mode.DEFAULT == "structured"
    assert condense_mode.configured({}) == "structured"
    assert condense_mode.configured({"condense_mode": ""}) == "structured"
    assert condense_mode.configured({"condense_mode": None}) == "structured"
    assert condense_mode.configured({"condense_mode": "agent"}) == "agent"
    assert condense_mode.is_structured({}) is True
    assert condense_mode.is_structured({"condense_mode": "agent"}) is False


def test_no_other_file_spells_a_fallback():
    """A literal fallback beside the constant is how the four spellings came
    to exist. Any read of the key outside mc/condense_mode.py goes through it."""
    spelled = re.compile(
        r"""\.get\(\s*['"]condense_mode['"]\s*,|['"]condense_mode['"]\s*:\s*['"]""")
    offenders = []
    for path in [REPO / "server.py", *sorted((REPO / "mc").rglob("*.py")),
                 *sorted((REPO / "tools").rglob("*.py"))]:
        if path.name == "condense_mode.py":
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8",
                                                errors="replace").splitlines(), 1):
            if spelled.search(line):
                offenders.append(f"{path.relative_to(REPO)}:{n}: {line.strip()}")
    assert offenders == []


def test_the_settings_page_hydrates_to_the_same_default():
    from mc import condense_mode
    js = (REPO / "static" / "js" / "settings-drill.js").read_text(encoding="utf-8")
    found = re.findall(r"cfg\.condense_mode\s*\|\|\s*'([a-z]+)'", js)
    assert found == [condense_mode.DEFAULT]


def test_the_memory_doc_states_the_same_default():
    """tests/test_memory_doc_config.py reads server.py's defaults with ast and
    keeps only literals. The default is now a name there, so that test no
    longer sees this row; it is checked here against the constant instead."""
    from mc import condense_mode
    doc = REPO / "docs" / "MEMORY_SYSTEM.md"
    if not doc.exists():
        pytest.skip("docs/MEMORY_SYSTEM.md is operator-local (gitignored)")
    rows = re.findall(r"^\|\s*`condense_mode`\s*\|\s*`([a-z]+)`\s*\|",
                      doc.read_text(encoding="utf-8"), re.M)
    assert rows == [condense_mode.DEFAULT]
