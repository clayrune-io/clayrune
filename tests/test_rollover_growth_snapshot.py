"""Calibration export keeps enough evidence to audit each growth estimate."""

import runpy
from collections import Counter
from pathlib import Path


def test_emitted_growth_table_has_all_scopes_and_keeps_local_characters_private(tmp_path):
    script = Path(__file__).resolve().parents[1] / 'tools' / 'analyze-rollover-tuning.py'
    analysis = runpy.run_path(str(script))
    model = 'claude-sonnet-5-5'
    sample = analysis['Measures'](
        floors=[80_000] * 10, deltas=[2_600] * 100, floor_chats=set(range(5)),
        growth_chats=Counter({0: 50, 1: 25, 2: 25}),
    )
    concentrated = analysis['Measures'](
        floors=sample.floors, deltas=sample.deltas, floor_chats=sample.floor_chats,
        growth_chats=Counter({0: 51, 1: 49}),
    )
    small = analysis['Measures'](
        floors=[80_000] * 9, deltas=sample.deltas, floor_chats=sample.floor_chats,
        growth_chats=sample.growth_chats,
    )
    cohorts = {
        ('global:builder', model, 'high'): sample,
        ('*', model, 'high'): concentrated,
        ('*', model, '*'): sample,
        ('global:builder', model, 'medium'): small,
        ('private-project:private-operator', model, 'high'): sample,
    }
    destination = tmp_path / 'growth.py'
    analysis['emit_table'](destination, cohorts, '2026-11-18T00:00:00Z', '2026-11-25T17:00:00Z')
    assert 'private-operator' not in destination.read_text(encoding='utf-8')
    table = runpy.run_path(str(destination))['GROWTH_ROWS']
    cell = table['global:builder', model, 'high']
    assert cell.snapshot_date == '2026-11-25'
    assert (cell.floor_count, cell.chat_count, cell.delta_count) == (10, 5, 100)
    assert cell.mean_growth == 2_600 and cell.median_floor == 80_000
    assert cell.coverage_met and table[None, model, None].coverage_met
    assert not table[None, model, 'high'].coverage_met
    assert not table['global:builder', model, 'medium'].coverage_met
