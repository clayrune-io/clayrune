"""MC-1072: config resolution without sessions, files or provider calls."""
import pytest

from mc.rollover_threshold import threshold_for, threshold_with_reason
from mc.rollover_model_table import MODEL_TABLE, model_inputs


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
    }) == 0



def _session(model='claude-fable-5-2', floor=90_000, **extra):
    return {'character': 'global:dave', 'model': model, '_mt_fresh_floor': floor, **extra}


def test_dave_fable_ignores_thrashing_pin_and_logs_once(monkeypatch):
    from mc import rollover_threshold as rt
    rt._log_ignored_pin.cache_clear()
    messages = []
    monkeypatch.setattr(rt, '_log', messages.append)
    config = {'context_rollover_by_character': {'global:dave': 100_000}}
    session = _session()
    assert threshold_with_reason(session, config) == (277_349, 'pin_ignored_below_floor')
    assert threshold_for(session, config) == 277_349
    assert len(messages) == 1 and 'global:dave' in messages[0] and '100000' in messages[0]
    rt._log_ignored_pin.cache_clear()


@pytest.mark.parametrize('model', ['claude-opus-5-5', 'claude-sonnet-5-5'])
def test_economical_models_stay_at_global_lower_bound(model):
    assert threshold_with_reason(_session(model), {}) == (200_000, 'cost_optimal')


@pytest.mark.parametrize('model,reason', [
    ('claude-haiku-5-5', 'price_step'), ('claude-haiku-4-5-20251001', 'window_cap'),
])
def test_hard_caps_win_even_below_lower_bound(model, reason):
    assert threshold_with_reason(_session(model), {}) == (100_000, reason)


@pytest.mark.parametrize('pin,expected,reason', [
    (250_000, 250_000, 'pin'), (150_000, 150_000, 'pin'),
    (149_999, 277_349, 'pin_ignored_below_floor'),
    (400_000, 277_349, 'cost_optimal'),
])
def test_pin_caps_only_when_it_leaves_60k_working_room(pin, expected, reason):
    assert threshold_with_reason(_session(), {
        'context_rollover_by_character': {'global:dave': pin},
    }) == (expected, reason)


def test_window_cap_and_fresh_floor_lower_bound():
    assert threshold_with_reason(_session(floor=490_000), {}) == (500_000, 'window_cap')
    # Tiny global does not roll immediately on the first fresh reading.
    assert threshold_for(_session('claude-opus-5-5', 200_000), {
        'context_rollover_tokens': 60_000,
    }) >= 260_000


@pytest.mark.parametrize('model,floor', [
    ('gpt-6.1-sol', 90_000), ('claude-future-6', 90_000),
    ('claude-fable-5-2', None), ('claude-fable-5-2', -1),
    ('claude-fable-5-2', True), ('claude-fable-5-2', 'unknown'),
])
def test_unknown_model_or_floor_preserves_legacy_pin(model, floor):
    session = _session(model, floor)
    assert threshold_with_reason(session, {}) == (200_000, 'global')
    assert threshold_with_reason(session, {
        'context_rollover_by_character': {'global:dave': 100_000},
    }) == (100_000, 'pin')


@pytest.mark.parametrize('global_tokens', [0, -1, '0'])
@pytest.mark.parametrize('model', ['claude-fable-5-2', 'gpt-6.1-sol'])
def test_global_disable_wins_over_model_and_pin(global_tokens, model):
    assert threshold_with_reason(_session(model), {
        'context_rollover_tokens': global_tokens,
        'context_rollover_by_character': {'global:dave': 100_000},
    }) == (0, 'global')


@pytest.mark.parametrize('model', list(MODEL_TABLE))
def test_model_normalization_and_growth_sources(model):
    row = MODEL_TABLE[model]
    assert model_inputs(model + '-20261101[1m]') is row
    assert model_inputs(model + '[1m]') is row
    assert row['growth_source'] in ('measured', 'family', 'provisional')
    if row['cache_write_price'] is not None:
        assert row['write_read_ratio'] == pytest.approx(
            row['cache_write_price'] / row['cache_read_price'])
    else:
        assert row['input_price'] is None and row['cache_read_price'] is None
        assert row['write_read_ratio'] == 12.5
    family = model.split('-')[1]
    assert row['mean_growth_tokens'] == {'fable': 3900, 'opus': 1600,
                                         'sonnet': 2600, 'haiku': 2600}[family]
    measured = model in ('claude-fable-5-2', 'claude-opus-5-5', 'claude-sonnet-5-5')
    assert row['growth_source'] == ('measured' if measured else
                                    'provisional' if family == 'haiku' else 'family')


@pytest.mark.parametrize('model', [None, 1, 'gpt-6.1-sol', 'sonnet',
                                   'claude-haiku-4-5-unknown', 'claude-fable-5-20'])
def test_unknown_models_are_not_guessed(model):
    assert model_inputs(model) is None


def test_observed_model_and_agent_model_fallback():
    assert threshold_for(_session(observed_model='claude-haiku-5-5'), {}) == 100_000
    session = {'agent_model': 'claude-fable-5-2', '_mt_fresh_floor': 90_000}
    assert threshold_for(session, {}) == 277_349
    session['model'] = ''  # Native default is unknown until observed.
    assert threshold_for(session, {}) == 200_000
