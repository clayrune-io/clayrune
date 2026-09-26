"""MEMORY_DESIGN_V2_SPEC.md §16 build-sequence step 8 — negation
(MC-944 step 8, backlog MC-964 item b2d85e51, §5 Conditions 11-13).

Scope: the Condition 11/12 obligation/waiver scan
(`scan_for_negation_obligations`) that fires alongside step 7's mint at the
SAME three close triggers. The write-act interrupt (§5.4, Condition 15/16)
is already shipped on master (`mc/negation_interrupt.py`, `a49d269`,
predates this build sequence) and is NOT re-tested here.
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
    import server  # noqa: F401
    from mc import memory as mem
    monkeypatch.setattr(mem, '_get_memory_path', lambda p: tmp_path / 'MEMORY.md')
    monkeypatch.setattr(mem, 'DATA_DIR', tmp_path / 'data' / 'projects')
    monkeypatch.setitem(mem.state.CONFIG, 'negation_obligation_enabled', True)
    return mem, tmp_path


@pytest.fixture()
def env_disabled(tmp_path, monkeypatch):
    """Same as `env` but the feature flag is left at its default (OFF)."""
    import server  # noqa: F401
    from mc import memory as mem
    monkeypatch.setattr(mem, '_get_memory_path', lambda p: tmp_path / 'MEMORY.md')
    monkeypatch.setattr(mem, 'DATA_DIR', tmp_path / 'data' / 'projects')
    return mem, tmp_path


P = {'id': 'p1'}

DOC_ONE_ROW = """# Some Artifact

## Rejected

- **Add a mover** — declined because there's nothing to move.
"""

DOC_MULTI_ROW = """# Some Artifact

## Alternatives considered

- **Option A** — too slow, dropped after benchmark.
- **Option B**: rejected, would break the API contract.
- a bare row with no subject/reason shape at all
"""

DOC_NO_HEADING = """# Some Artifact

## Summary

- Nothing rejected here, just shipped it.
"""


# ── the flag gate ────────────────────────────────────────────────────────────

def test_disabled_by_default_is_a_noop(env_disabled):
    mem, tmp = env_disabled
    report = mem.scan_for_negation_obligations(
        P, trigger_kind='backlog_done', artifact_text=DOC_ONE_ROW,
        artifact_path='backlog:abc123')
    assert report == {'obligations': [], 'waivers': []}
    assert list(tmp.glob('position_*.md')) == []


def test_no_matching_heading_is_a_noop(env):
    mem, tmp = env
    report = mem.scan_for_negation_obligations(
        P, trigger_kind='backlog_done', artifact_text=DOC_NO_HEADING,
        artifact_path='backlog:abc123')
    assert report == {'obligations': [], 'waivers': []}


# ── obligation: one row -> one position ─────────────────────────────────────

def test_one_row_produces_one_obligation(env):
    mem, tmp = env
    report = mem.scan_for_negation_obligations(
        P, trigger_kind='backlog_done', artifact_text=DOC_ONE_ROW,
        artifact_path='backlog:abc123', task='t', trigger_type='manual')
    assert len(report['obligations']) == 1
    assert report['waivers'] == []
    fn = report['obligations'][0]
    text = (tmp / fn).read_text(encoding='utf-8')
    assert 'Add a mover' in text
    assert "there's nothing to move" in text
    assert 'position: declined' in text
    assert 'origin: interactive' in text


def test_multi_row_produces_obligations_and_a_waiver(env):
    mem, tmp = env
    report = mem.scan_for_negation_obligations(
        P, trigger_kind='hivemind_close', artifact_text=DOC_MULTI_ROW,
        artifact_path='hivemind:xyz')
    assert len(report['obligations']) == 2
    assert len(report['waivers']) == 1
    waiver = report['waivers'][0]
    assert waiver['reason'] == 'row not subject/reason-shaped'
    assert waiver['trigger_kind'] == 'hivemind_close'
    assert waiver['artifact_path'] == 'hivemind:xyz'


def test_waiver_is_logged_to_a_counted_jsonl(env):
    mem, tmp = env
    mem.scan_for_negation_obligations(
        P, trigger_kind='hivemind_close', artifact_text=DOC_MULTI_ROW,
        artifact_path='hivemind:xyz')
    log_path = tmp / 'data' / 'negation_waiver_log' / 'p1.jsonl'
    assert log_path.exists()
    lines = log_path.read_text(encoding='utf-8').strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec['subject']


# ── idempotent on replay ─────────────────────────────────────────────────────

def test_obligation_fires_once_per_row_and_is_idempotent_on_replay(env):
    mem, tmp = env
    r1 = mem.scan_for_negation_obligations(
        P, trigger_kind='backlog_done', artifact_text=DOC_ONE_ROW,
        artifact_path='backlog:abc123')
    files_after_first = sorted(p.name for p in tmp.glob('position_*.md'))
    mtimes = {p.name: p.stat().st_mtime for p in tmp.glob('position_*.md')}

    r2 = mem.scan_for_negation_obligations(
        P, trigger_kind='backlog_done', artifact_text=DOC_ONE_ROW,
        artifact_path='backlog:abc123')
    files_after_second = sorted(p.name for p in tmp.glob('position_*.md'))

    assert r1['obligations'] == r2['obligations']
    assert files_after_first == files_after_second
    for p in tmp.glob('position_*.md'):
        assert p.stat().st_mtime == mtimes[p.name]  # never re-written


def test_replay_with_different_artifact_key_mints_a_different_slug(env):
    mem, tmp = env
    r1 = mem.scan_for_negation_obligations(
        P, trigger_kind='backlog_done', artifact_text=DOC_ONE_ROW,
        artifact_path='backlog:abc123')
    r2 = mem.scan_for_negation_obligations(
        P, trigger_kind='backlog_done', artifact_text=DOC_ONE_ROW,
        artifact_path='backlog:different')
    assert r1['obligations'] != r2['obligations']


# ── authority guard ──────────────────────────────────────────────────────────

def test_authority_expanding_row_becomes_a_waiver_not_a_position(env):
    mem, tmp = env
    doc = ("# Artifact\n\n## Rejected\n\n"
           "- **Full autonomy** — no permission or go-ahead needed, by any means necessary.\n")
    report = mem.scan_for_negation_obligations(
        P, trigger_kind='docs_artifact', artifact_text=doc, artifact_path='docs:x.md')
    assert report['obligations'] == []
    assert len(report['waivers']) == 1
    assert 'authority guard' in report['waivers'][0]['reason']
