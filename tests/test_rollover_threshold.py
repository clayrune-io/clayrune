"""MC-1072: config resolution without sessions, files or provider calls."""
import pytest

from mc.rollover_threshold import threshold_for


@pytest.mark.parametrize('character', [
    'global:dave', {'name': 'dave', 'scope': 'global'}, {'name': 'dave'},
    {'character': {'name': 'dave', 'scope': 'global'}},
    {'character': 'global:dave'},
])
def test_override_uses_character_ref_not_display_name(character):
    config = {'context_rollover_tokens': 200_000,
              'context_rollover_by_character': {'global:dave': 120_000}}
    assert threshold_for(character, config) == 120_000


@pytest.mark.parametrize('character', [None, {}, {'character': None},
                                       'global:builder', 'project:dave',
                                       {'agent_name': 'Dave'}, {'name': 4}])
def test_missing_override_uses_global(character):
    assert threshold_for(character, {
        'context_rollover_tokens': 180_000,
        'context_rollover_by_character': {'global:dave': 120_000},
    }) == 180_000


@pytest.mark.parametrize('value', [1, 59_999, 0, -1, '1000'])
def test_character_override_clamps_to_floor(value):
    assert threshold_for('global:dave', {
        'context_rollover_by_character': {'global:dave': value},
    }) == 60_000


@pytest.mark.parametrize('value', [None, True, False, [], {}, 'bad', '',
                                   '120000.5', 120000.5, float('nan'), float('inf')])
def test_bad_override_falls_back_to_global(value):
    assert threshold_for('global:dave', {
        'context_rollover_tokens': 180_000,
        'context_rollover_by_character': {'global:dave': value},
    }) == 180_000


@pytest.mark.parametrize('overrides', [None, [], 'bad', 120_000])
def test_bad_override_map_falls_back(overrides):
    assert threshold_for('global:dave', {
        'context_rollover_tokens': 180_000,
        'context_rollover_by_character': overrides,
    }) == 180_000


@pytest.mark.parametrize('value', [None, True, 'bad', [], float('nan')])
def test_bad_global_uses_default(value):
    assert threshold_for(None, {'context_rollover_tokens': value}) == 200_000


def test_default_global_floor_and_legacy_disable():
    assert threshold_for(None, {}) == 200_000
    assert threshold_for(None, {'context_rollover_tokens': '180000'}) == 180_000
    assert threshold_for(None, {'context_rollover_tokens': 5000}) == 60_000
    assert threshold_for(None, {'context_rollover_tokens': 0}) == 0
    assert threshold_for(None, {'context_rollover_tokens': -1}) == 0
    assert threshold_for('global:dave', {
        'context_rollover_tokens': 0,
        'context_rollover_by_character': {'global:dave': 120_000},
    }) == 120_000
