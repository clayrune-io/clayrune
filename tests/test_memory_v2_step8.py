"""MEMORY_DESIGN_V2_SPEC.md §16 build-sequence step 8 — negation
(MC-944 step 8, backlog MC-964 item b2d85e51, §5 Conditions 11-13).

Scope: the Condition 11/12 obligation/waiver scan
(`scan_for_negation_obligations`) that fires alongside step 7's mint at the
SAME three close triggers, and the Condition 13 resident Negation Ledger
(`render_negation_ledger`, `rank_negation_ledger`). The write-act interrupt
(§5.4, Condition 15/16) is already shipped on master
(`mc/negation_interrupt.py`, `a49d269`, predates this build sequence) and is
NOT re-tested here.
"""
import json
import sys
from datetime import datetime
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


@pytest.fixture()
def ledger_env(tmp_path, monkeypatch):
    """Ledger flag ON; positions live directly under `tmp_path` (mirrors
    `env`'s `_get_memory_path` override) so `write_position`/`list_positions`
    and the ledger's own interrupt-log path agree on where `DATA_DIR` is."""
    import server  # noqa: F401
    from mc import memory as mem
    monkeypatch.setattr(mem, '_get_memory_path', lambda p: tmp_path / 'MEMORY.md')
    monkeypatch.setattr(mem, 'DATA_DIR', tmp_path / 'data' / 'projects')
    monkeypatch.setitem(mem.state.CONFIG, 'negation_ledger_enabled', True)
    return mem, tmp_path


def _fire_log(mem, tmp, project_id, position_file, ts_list):
    p = tmp / 'data' / 'negation_interrupt_log' / f'{project_id}.jsonl'
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, 'a', encoding='utf-8') as f:
        for ts in ts_list:
            f.write(json.dumps({'result': 'fire', 'position_file': position_file,
                                 'ts': ts}) + '\n')


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


# ── Negation Ledger (Condition 13/14) ───────────────────────────────────────

def test_ledger_disabled_by_default_is_a_noop(env_disabled):
    mem, tmp = env_disabled
    mem.write_position(P, 'declined thing', 'declined', 'because reasons',
                        decided='2026-06-01')
    assert mem.render_negation_ledger(P) == ''


def test_ledger_empty_when_no_positions(ledger_env):
    mem, tmp = ledger_env
    assert mem.render_negation_ledger(P) == ''
    assert mem.rank_negation_ledger(P) == []


def test_ledger_line_has_no_reason(ledger_env):
    mem, tmp = ledger_env
    mem.write_position(P, 'a two-level lazy index', 'declined',
                        'the corpus is too small to need one', decided='2026-08-16',
                        slug='twolevellazyindex')
    block = mem.render_negation_ledger(P)
    assert 'NEGATION LEDGER' in block
    assert 'a two-level lazy index' in block
    assert 'declined' in block
    assert '2026-08-16' in block
    assert '[twolevellazyindex]' in block
    assert 'too small to need one' not in block  # the reason itself never renders


def test_ledger_cold_start_is_most_recent_n(ledger_env):
    mem, tmp = ledger_env
    for i in range(5):
        mem.write_position(P, f'subject {i}', 'declined', 'reason',
                            decided=f'2026-01-0{i + 1}', slug=f's{i}')
    ranked = mem.rank_negation_ledger(P, now=datetime(2026, 9, 25))
    # zero confirmed-pressure everywhere -> pure recency, newest first
    assert [r['name'] for r in ranked] == ['s4', 's3', 's2', 's1', 's0']


def test_ledger_caps_at_negation_ledger_max(ledger_env):
    mem, tmp = ledger_env
    mem.state.CONFIG['negation_ledger_max'] = 3
    for i in range(6):
        mem.write_position(P, f'subject {i}', 'declined', 'reason',
                            decided=f'2026-01-0{i + 1}', slug=f's{i}')
    ranked = mem.rank_negation_ledger(P, now=datetime(2026, 9, 25))
    assert len(ranked) == 3
    block = mem.render_negation_ledger(P)
    assert len(block.encode('utf-8')) <= mem._negation_ledger_line_budget() + len(
        "--- NEGATION LEDGER (decisions already made — a slug here means "
        "the full ruling exists; check it before re-proposing) ---\n".encode('utf-8'))


def test_ledger_respects_byte_cap(ledger_env):
    mem, tmp = ledger_env
    mem.state.CONFIG['negation_ledger_max'] = 20
    for i in range(20):
        mem.write_position(P, f'a fairly long subject line number {i:02d} of many words',
                            'declined', 'reason', decided=f'2026-0{(i % 9) + 1}-01',
                            slug=f'longsubject{i:02d}')
    block = mem.render_negation_ledger(P)
    assert len(block.encode('utf-8')) <= mem._negation_ledger_line_budget() + 200  # header slack


def test_ledger_pin_overrides_recency_capped_at_pin_max(ledger_env):
    mem, tmp = ledger_env
    mem.state.CONFIG['negation_pin_max'] = 1
    mem.write_position(P, 'old but pinned', 'declined', 'reason',
                        decided='2020-01-01', slug='oldpinned', pin=True)
    mem.write_position(P, 'newer unpinned', 'declined', 'reason',
                        decided='2026-09-01', slug='newerunpinned')
    ranked = mem.rank_negation_ledger(P, now=datetime(2026, 9, 25))
    assert ranked[0]['name'] == 'oldpinned'
    assert ranked[1]['name'] == 'newerunpinned'


def test_ledger_confirmed_pressure_outranks_pure_recency(ledger_env):
    mem, tmp = ledger_env
    # older, but its interrupt fired and the ruling was later superseded
    # (fire precedes 'decided') -> confirmed pressure.
    mem.write_position(P, 'pressured older ruling', 'declined', 'reason',
                        decided='2026-06-01', slug='pressured')
    # newer, never fired -> zero pressure, wins only on recency.
    mem.write_position(P, 'quiet newer ruling', 'declined', 'reason',
                        decided='2026-08-01', slug='quiet')
    _fire_log(mem, tmp, 'p1', 'position_pressured.md', ['2026-05-01T00:00:00Z'])
    ranked = mem.rank_negation_ledger(P, now=datetime(2026, 9, 25))
    assert ranked[0]['name'] == 'pressured'
    assert ranked[0]['_pressure'] == 1
    assert ranked[1]['name'] == 'quiet'


def test_ledger_pressure_fire_after_decided_does_not_count(ledger_env):
    """A fire that happened AFTER the current `decided` date did not precede
    a supersession -- the ruling in force today predates that fire, so it
    cannot be what the fire's re-proposal pressure was measuring."""
    mem, tmp = ledger_env
    mem.write_position(P, 'ruling', 'declined', 'reason', decided='2026-01-01',
                        slug='ruling')
    _fire_log(mem, tmp, 'p1', 'position_ruling.md', ['2026-06-01T00:00:00Z'])
    ranked = mem.rank_negation_ledger(P, now=datetime(2026, 9, 25))
    assert ranked[0]['_pressure'] == 0


def test_ledger_pressure_outside_window_does_not_count(ledger_env):
    mem, tmp = ledger_env
    mem.write_position(P, 'ruling', 'declined', 'reason', decided='2026-09-20',
                        slug='ruling')
    # fire is > 180 days before 'now' (2026-09-25) -> outside the window
    _fire_log(mem, tmp, 'p1', 'position_ruling.md', ['2025-01-01T00:00:00Z'])
    ranked = mem.rank_negation_ledger(P, now=datetime(2026, 9, 25))
    assert ranked[0]['_pressure'] == 0


def test_ledger_reserves_slots_for_recency_over_pressure(ledger_env):
    """Without the reserve, a plain `(-pressure, decided desc)` sort would
    put EVERY one of 10 equally-pressured-but-old positions ahead of a
    brand-new, zero-pressure one -- pressure is the primary sort key, so
    recency only breaks ties WITHIN a pressure tier, never across tiers. The
    reserve exists so a fresh position is never fully crowded out just
    because older ones once triggered a confirmed re-proposal (Condition 14)."""
    mem, tmp = ledger_env
    mem.state.CONFIG['negation_ledger_max'] = 6
    # 10 old, equally-pressured positions -- more than enough to fill every
    # slot on pressure ranking alone if there were no reserve. Each fire
    # precedes its position's `decided` date so it counts as confirmed
    # pressure, and all fall within the 180-day window of `now` below.
    for i in range(10):
        mem.write_position(P, f'pressured {i:02d}', 'declined', 'reason',
                            decided=f'2026-05-{i + 1:02d}', slug=f'pressured{i:02d}')
        _fire_log(mem, tmp, 'p1', f'position_pressured{i:02d}.md',
                   ['2026-04-01T00:00:00Z'])
    # 1 brand-new, zero-pressure position -- most recent of all by far.
    mem.write_position(P, 'brand new', 'declined', 'reason', decided='2026-09-24',
                        slug='brandnew')
    ranked = mem.rank_negation_ledger(P, now=datetime(2026, 9, 25))
    names = [r['name'] for r in ranked]
    assert len(ranked) == 6
    assert 'brandnew' in names  # recency reserve rescues it despite zero pressure


def test_ledger_consumer_unattended_withholds_unattended_origin(ledger_env):
    mem, tmp = ledger_env
    mem.write_position(P, 'attended ruling', 'declined', 'reason',
                        decided='2026-01-01', slug='attended', task='t',
                        trigger_type='manual')
    mem.write_position(P, 'unattended ruling', 'declined', 'reason',
                        decided='2026-01-02', slug='unattended')  # no task/trigger_type -> unattended
    ranked_attended = mem.rank_negation_ledger(P, consumer_unattended=False)
    ranked_unattended = mem.rank_negation_ledger(P, consumer_unattended=True)
    assert {r['name'] for r in ranked_attended} == {'attended', 'unattended'}
    assert {r['name'] for r in ranked_unattended} == {'attended'}
