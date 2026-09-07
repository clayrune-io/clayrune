"""mc/negation_interrupt.py — plan-time negation interrupt (MC-944,
MEMORY_DESIGN_V2_SPEC.md §5.4, build-sequence step 8). REPORT MODE ONLY.

WHAT THIS CLOSES. `mc.memory_turn` (step 5, shipped `ba1e955`) re-renders
standing positions on every LIVE TURN — but that is delivery going IN to the
agent. Nothing previously watched what the agent WRITES: an `Agent` dispatch
prompt, a spec written to `docs/**`, or a plan file under `~/.claude/plans/`
can re-propose something a standing position already declined, and on
master, before this change, nothing notices. This module is the SECOND, LATER
firing point — at the moment the proposal is written down, not when it is
delivered or when it executes.

THE ACCEPTANCE SHAPE (brief, MC-944 build step 8 "Verify"): plant a known
position, write a plan that re-proposes exactly that rejected thing, and
assert the position surfaces WITH its reason and expires_when — plus the
inverse, an unrelated plan produces no fire. `test_agent_dispatch_...` below
uses a REAL position from the live vault
(`position_aresidencydashboardandanautomaticpromotedemotemo.md`, declined: an
automatic promote/demote mover) verbatim, since re-proposing it is a live risk
the brief calls out by name.

Fixture borrowed from tests/test_positions.py (`env`: monkeypatch
`mem._get_memory_path` straight to a tmp file — no full server reload
needed, and `mem.DATA_DIR` is monkeypatched separately so the negation log,
which is deliberately a SIBLING of DATA_DIR/data/projects/, never touches the
real repo's data/ tree during a test run).
"""
import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import server  # noqa: F401 — triggers memory.wire()
    from mc import memory as mem
    from mc import negation_interrupt as ni

    monkeypatch.setattr(mem, '_get_memory_path', lambda p: tmp_path / 'MEMORY.md')
    (tmp_path / 'MEMORY.md').write_text('# index\n', encoding='utf-8')

    data_root = tmp_path / 'data_root'
    monkeypatch.setattr(mem, 'DATA_DIR', data_root / 'data' / 'projects')

    return mem, ni, tmp_path, data_root


P = {'id': 'p1'}

# Verbatim from the live vault (…/memory/
# position_aresidencydashboardandanautomaticpromotedemotemo.md), the case the
# brief names by name.
REAL_POSITION = """---
name: aresidencydashboardandanautomaticpromotedemotemo
subject: a residency dashboard and an automatic promote/demote mover for memory
position: declined
reason: "Measured 2026-08-24 over 189 real tasks. Demotion has nothing to move: 529 of 586 archive lines are never delivered and already cost zero tokens, because a line nobody retrieves takes no slot — they are in the coldest tier, which IS the demoted state. Promotion is real but came to two notes in 189 tasks, i.e. one one-liner twice a quarter, which a human decides faster than a mover can be trusted to. The whole payoff of the telemetry was an anomaly nobody could see from the code: a standing position reaching 57% of tasks. So the durable output is a weekly WATCH (tools/memory-eval/delivery_review.py) that raises each finding once, not a dashboard read when someone remembers to look."
triggers: residency dashboard, memory mover, promote demote notes, delivery report screen
expires_when: promotion candidates exceed roughly one a week, or a demotion lever appears that actually saves tokens (e.g. resident index pressure against its 24KB cap, which is at 17.2KB today)
decided: 2026-08-24
---
"""


def _seed_real_position(tmp_path):
    (tmp_path / 'position_aresidencydashboardandanautomaticpromotedemotemo.md'
     ).write_text(REAL_POSITION, encoding='utf-8')


def _log_file(data_root, pid='p1'):
    return data_root / 'data' / 'negation_interrupt_log' / f'{pid}.jsonl'


# ── fires: the accepted case ────────────────────────────────────────────────

def test_agent_dispatch_reproposing_a_declined_position_fires_with_reason(env):
    mem, ni, tmp_path, data_root = env
    _seed_real_position(tmp_path)

    plan_prompt = (
        "Build a residency dashboard for memory: a screen that shows delivery "
        "stats and lets us review which notes should move tiers."
    )
    fires = ni.observe_tool_call(
        P, 'sess-1', 'Agent',
        {'description': 'residency dashboard', 'prompt': plan_prompt})

    assert len(fires) == 1
    rec = fires[0]['rec']
    assert rec['subject'].startswith('a residency dashboard')
    assert rec['reason']
    assert rec['expires_when']

    log_file = _log_file(data_root)
    assert log_file.exists(), "the log IS the deliverable (step 9 reads it)"
    rows = [json.loads(l) for l in log_file.read_text(encoding='utf-8').splitlines()]
    assert len(rows) == 1
    row = rows[0]
    assert row['result'] == 'fire'
    assert row['source'] == 'agent_dispatch'
    assert row['position_file'] == (
        'position_aresidencydashboardandanautomaticpromotedemotemo.md')
    # the reason is the payload — a bare "this was rejected" cannot be judged
    assert 'Measured 2026-08-24' in row['reason']
    assert 'promotion candidates exceed' in row['expires_when']
    assert row['decided'] == '2026-08-24'


def test_docs_write_reproposing_the_position_also_fires(env):
    """§5.4 item 1 — a spec written to docs/** is the same act as an Agent
    dispatch: a plan written down, not executed."""
    mem, ni, tmp_path, data_root = env
    _seed_real_position(tmp_path)

    spec_text = (
        "## Proposal\nAdd a residency dashboard and a memory mover that "
        "promotes and demotes notes automatically based on usage."
    )
    fires = ni.observe_tool_call(
        P, 'sess-2', 'Write',
        {'file_path': 'docs/MY_NEW_MEMORY_SPEC.md', 'content': spec_text})
    assert len(fires) == 1


def test_plan_file_write_also_fires(env):
    """§5.4 item 2 — ~/.claude/plans/*.md, the headless-safe plan surface
    agent_routes._is_plan_path already tracks for the PLAN tab."""
    mem, ni, tmp_path, data_root = env
    _seed_real_position(tmp_path)

    plans_dir = Path.home() / '.claude' / 'plans'
    fp = str(plans_dir / 'my_new_plan.md')
    text = "Step 1: build a residency dashboard with an automatic memory mover."
    fires = ni.observe_tool_call(P, 'sess-3', 'Write',
                                  {'file_path': fp, 'content': text})
    assert len(fires) == 1


# ── the inverse: no fire on an unrelated plan ───────────────────────────────

def test_unrelated_plan_produces_no_fire(env):
    mem, ni, tmp_path, data_root = env
    _seed_real_position(tmp_path)

    plan_prompt = (
        "Fix the mobile calendar widget: drag-and-drop reordering drops the "
        "event on release instead of committing the new time slot."
    )
    fires = ni.observe_tool_call(
        P, 'sess-4', 'Agent',
        {'description': 'mobile calendar bug', 'prompt': plan_prompt})
    assert fires == []
    assert not _log_file(data_root).exists()


def test_irrelevant_tool_call_never_touches_the_vault_or_logs(env):
    """Bash / a Write outside docs+plans must never even reach the matcher —
    asserts the cheap is_relevant() pre-filter agent_routes.py relies on to
    avoid a project load on every tool call, not just the end result."""
    mem, ni, tmp_path, data_root = env
    _seed_real_position(tmp_path)

    assert ni.is_relevant('Bash', {'command': 'ls'}) is False
    assert ni.is_relevant(
        'Write', {'file_path': 'server.py',
                   'content': 'residency dashboard memory mover'}) is False
    assert ni.observe_tool_call(P, 's', 'Bash', {'command': 'ls'}) == []
    assert not _log_file(data_root).exists()


# ── near-misses: the diagnostic step 9 needs ────────────────────────────────

def test_partial_overlap_logs_a_near_miss_not_a_fire(env):
    """A plan mentioning ONLY 'memory' and 'dashboard' scattered far apart
    should not conjunctively match any single trigger phrase, but the
    overlap is worth a near-miss row for step 9's false-positive tuning."""
    mem, ni, tmp_path, data_root = env
    _seed_real_position(tmp_path)

    # 'dashboard' and 'memory' appear, but never co-located with the rest of
    # any one trigger phrase within the match window — padded far apart.
    filler = ' '.join(['unrelated'] * 60)
    plan_prompt = f"dashboard idea. {filler} some memory notes exist."
    fires = ni.observe_tool_call(
        P, 'sess-5', 'Agent',
        {'description': 'vague idea', 'prompt': plan_prompt})
    assert fires == []

    log_file = _log_file(data_root)
    assert log_file.exists(), "partial overlap must still log a near-miss row"
    rows = [json.loads(l) for l in log_file.read_text(encoding='utf-8').splitlines()]
    assert rows and all(r['result'] == 'near_miss' for r in rows)
    assert rows[0]['coverage'] < 1.0
    # near-misses are diagnostic only — no reason/expires_when payload
    assert all('reason' not in r for r in rows)


# ── caps + config gates ─────────────────────────────────────────────────────

def test_max_hits_caps_fires_per_scan(env, monkeypatch):
    """Condition 16: cap at N hits per scan so one write cannot produce a
    wall of interrupts."""
    mem, ni, tmp_path, data_root = env
    _seed_real_position(tmp_path)
    mem.write_position(
        P, subject='Obsidian as the memory substrate', verdict='declined',
        reason='we already built the graph machinery ourselves',
        expires_when='our graph machinery stops resolving links',
        triggers='obsidian substrate, obsidian vault')

    from mc import state
    monkeypatch.setitem(state.CONFIG, 'negation_interrupt_max_hits', 1)

    plan_prompt = (
        "Adopt Obsidian as the memory substrate, and separately add a "
        "residency dashboard with an automatic memory mover."
    )
    fires = ni.observe_tool_call(
        P, 'sess-6', 'Agent',
        {'description': 'two proposals', 'prompt': plan_prompt})
    assert len(fires) == 1


def test_non_report_mode_never_fires_or_logs(env, monkeypatch):
    """Condition 15: only 'report' is implemented by this build step —
    'advisory'/'block' are later, unbuilt stages, and setting the mode to
    either must not guess at behaviour that doesn't exist yet."""
    mem, ni, tmp_path, data_root = env
    _seed_real_position(tmp_path)

    from mc import state
    monkeypatch.setitem(state.CONFIG, 'negation_interrupt_mode', 'advisory')

    fires = ni.observe_tool_call(
        P, 's', 'Agent',
        {'description': 'x',
         'prompt': 'a residency dashboard and automatic memory mover'})
    assert fires == []
    assert not _log_file(data_root).exists()


def test_observe_tool_call_never_raises_on_malformed_input(env):
    """Never-raises contract — a bug here must not cost a live turn its
    stream processing (module docstring)."""
    mem, ni, tmp_path, data_root = env
    _seed_real_position(tmp_path)
    assert ni.observe_tool_call(P, 's', 'Agent', None) == []
    assert ni.observe_tool_call(P, 's', 'Agent', {'prompt': None}) == []
    assert ni.observe_tool_call(None, 's', 'Agent', {'prompt': 'x'}) == []
