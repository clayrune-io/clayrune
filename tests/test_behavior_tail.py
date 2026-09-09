"""mc/behavior_tail.py — the per-turn conduct-rule tail (extends MC-944's
"behaviour rules re-deliver every turn, facts deliver once" split, from
retrieved memory to the standing SHARED_RULES.md conduct rules).

WHAT THIS CLOSES. The reply-length / no-dangling-promise / no-narration rules
lived once, ~46KB into a system prompt built at spawn (_build_agent_context).
A long Mode-B session never sees that block again, so by the time a reply is
generated hundreds of turns later, the rule governing it is thousands of
tokens back — exactly the "attention decays with distance" failure Ron
reported (the Stop hook fires repeatedly despite the rule being IN the
prompt). This test file pins three things: the block is small (a per-turn,
per-provider cost paid forever), it is genuinely last in the assembled system
prompt for more than one provider, and a promoted rule appears exactly once
in the whole prompt (never both in SHARED_RULES.md and the tail).

Fixture borrowed from tests/test_character_persona.py: importing `server`
wires the blueprint's module-level deps (SHARED_RULES_PATH, PORT, memory
helpers) so `_build_agent_context` runs without a live Flask request.
"""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import server  # noqa: F401 — wires the blueprint deps
    from mc.blueprints import agent_routes as ar
    from mc import memory as mem

    proj_path = tmp_path / 'proj'
    (proj_path / '.claude' / 'agents').mkdir(parents=True)
    memdir = tmp_path / 'mem'
    memdir.mkdir()
    (memdir / 'MEMORY.md').write_text('# index\n', encoding='utf-8')
    monkeypatch.setattr(mem, '_get_memory_path', lambda p: memdir / 'MEMORY.md')
    return ar, tmp_path, str(proj_path)


def _project(proj_path, provider='claude'):
    return {'id': 'p1', 'name': 'P1', 'project_path': proj_path, 'provider': provider}


# ── the block itself ─────────────────────────────────────────────────────────

def test_tail_stays_under_the_750_byte_budget():
    """A per-turn, per-provider, per-project cost forever — this is the whole
    reason the brief caps it. Default answer to a failure here is STILL to
    shrink the text. Raised 600 -> 750 on 2026-09-09 (Ron) for exactly one
    addition: the GOAL clause, which changes the turn's stop condition from
    "I have said enough" to "the objective is met" and is the whole reason
    agents stop asking permission for reversible work. Nothing else earns a
    raise; the next thing that wants space cuts something instead."""
    import mc.behavior_tail as bt
    assert len(bt._TAIL_TEXT.encode('utf-8')) <= 750


def test_render_empty_when_disabled(monkeypatch):
    import mc.behavior_tail as bt
    from mc import state
    monkeypatch.setitem(state.CONFIG, 'behavior_tail_enabled', False)
    assert bt.render() == ''


def test_render_nonempty_by_default(monkeypatch):
    import mc.behavior_tail as bt
    from mc import state
    monkeypatch.setitem(state.CONFIG, 'behavior_tail_enabled', True)
    assert bt.render() == bt._TAIL_TEXT


def test_config_read_failure_defaults_to_enabled(monkeypatch):
    """`_cfg` failing (e.g. mc.state not importable) must not disable the
    rail — `enabled()` fails OPEN, same posture as memory_turn.enabled()."""
    import mc.behavior_tail as bt
    monkeypatch.setattr(bt, '_cfg', lambda k, d: (_ for _ in ()).throw(RuntimeError('boom')))
    assert bt.enabled() is True
    assert bt.render() == bt._TAIL_TEXT


def test_render_never_raises_even_if_enabled_itself_breaks(monkeypatch):
    import mc.behavior_tail as bt
    monkeypatch.setattr(bt, 'enabled', lambda: (_ for _ in ()).throw(RuntimeError('boom')))
    assert bt.render() == ''


# ── position in the assembled system prompt, across providers ───────────────

@pytest.mark.parametrize('provider', ['claude', 'gemini'])
def test_tail_is_the_last_thing_before_the_user_message(env, provider):
    """`_build_agent_context` returns the SYSTEM PROMPT; the actual user
    message is a separate channel (stdin write / task arg) that always comes
    after it. So "last thing in the assembled prompt before the user
    message" means: last element of this function's output, for the
    provider-agnostic dispatch path that both Claude and every AgentRuntime
    (Gemini, Codex, ...) go through."""
    ar, _, proj_path = env
    ctx = ar._build_agent_context(_project(proj_path, provider), task='hello')
    import mc.behavior_tail as bt
    assert ctx.rstrip().endswith(bt._TAIL_TEXT.strip())


def test_promoted_rule_appears_exactly_once_in_the_whole_prompt(env):
    """The whole point of promoting a rule to the tail is that it is NOT also
    left in the bulk SHARED_RULES.md block — a rule stated twice costs twice
    and reads as a contradiction-shaped repeat. Pin on a phrase unique to the
    promoted reply-shape rule."""
    ar, _, proj_path = env
    ctx = ar._build_agent_context(_project(proj_path), task='hello')
    needle = 'at most 5 one-line bullets'
    assert ctx.count(needle) == 1, (
        f'expected the promoted reply-shape rule exactly once, found '
        f'{ctx.count(needle)} — check it was removed from SHARED_RULES.md')


def test_disabling_the_flag_removes_it_from_the_prompt(env, monkeypatch):
    ar, _, proj_path = env
    from mc import state
    monkeypatch.setitem(state.CONFIG, 'behavior_tail_enabled', False)
    ctx = ar._build_agent_context(_project(proj_path), task='hello')
    assert 'REPLY SHAPE (binding, re-stated every turn)' not in ctx


def test_shared_rules_no_longer_states_the_promoted_rules():
    """Source-level guard on data/SHARED_RULES.md itself (gitignored,
    operator content — not a promise about what a stranger's fresh install
    ships, just this machine's copy staying in sync with the split)."""
    from mc.blueprints import agent_routes as ar
    path = ar.SHARED_RULES_PATH
    if not path.exists():
        pytest.skip('no local data/SHARED_RULES.md on this machine')
    text = path.read_text(encoding='utf-8')
    assert 'at most 5 bullets of supporting fact' not in text
    assert 'Never end a turn on a promise of future work' not in text


def test_flag_is_registered_in_both_defaults_and_editable_keys():
    """The exact half-registered trap tests/test_ranker_constants.py exists
    to catch: a key missing from _CONFIG_EDITABLE_KEYS makes PUT /api/config
    a 200-shaped no-op (`{"updated": []}`); a key missing from server.py's
    defaults dict has no documented fresh-install value. Both, or neither."""
    import ast
    root = Path(__file__).resolve().parent.parent
    tree = ast.parse((root / 'server.py').read_text(encoding='utf-8'))
    defaults = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == '_load_config':
            for stmt in ast.walk(node):
                if (isinstance(stmt, ast.Assign)
                        and any(getattr(t, 'id', '') == 'defaults' for t in stmt.targets)
                        and isinstance(stmt.value, ast.Dict)):
                    for k, v in zip(stmt.value.keys, stmt.value.values):
                        if isinstance(k, ast.Constant):
                            try:
                                defaults[k.value] = ast.literal_eval(v)
                            except Exception:
                                pass
    assert defaults.get('behavior_tail_enabled') is True
    settings_src = (root / 'mc' / 'blueprints' / 'settings_routes.py').read_text(encoding='utf-8')
    assert "'behavior_tail_enabled'" in settings_src
