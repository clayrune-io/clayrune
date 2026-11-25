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
    # No floor is known for a bare reference, so the pin sits above the base.
    config = {'context_rollover_tokens': 200_000,
              'context_rollover_by_character': {'global:dave': 250_000}}
    assert threshold_for(character, config) == 250_000


@pytest.mark.parametrize('character', [None, {}, {'character': None},
                                       'global:builder', 'project:dave',
                                       {'agent_name': 'Dave'}, {'name': 4}])
def test_missing_override_uses_global(character):
    assert threshold_for(character, {
        'context_rollover_tokens': 180_000,
        'context_rollover_by_character': {'global:dave': 120_000},
    }) == 180_000


@pytest.mark.parametrize('value', [1, 59_999, 0, -1, '1000', 199_999])
def test_override_below_the_base_is_ignored_without_a_floor(value):
    assert threshold_with_reason('global:dave', {
        'context_rollover_by_character': {'global:dave': value},
    }) == (200_000, 'base')


@pytest.mark.parametrize('config,expected', [
    ({'context_rollover_by_character': {'global:dave': 200_000}},
     (200_000, 'override:character')),
    ({'context_rollover_tokens': 5000,
      'context_rollover_by_character': {'global:dave': 60_000}},
     (60_000, 'override:character')),
    ({'context_rollover_overrides': [{'model': 'opus', 'tokens': 150_000},
                                     {'character': 'global:dave', 'tokens': 240_000}]},
     (240_000, 'override:character')),
])
def test_override_at_or_above_the_base_applies_without_a_floor(config, expected):
    session = {'character': 'global:dave', 'model': 'opus'}
    assert threshold_with_reason(session, config) == expected


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
    rt._log_ignored_override.cache_clear()
    messages = []
    monkeypatch.setattr(rt, '_log', messages.append)
    config = {'context_rollover_by_character': {'global:dave': 100_000}}
    session = _session()
    assert threshold_with_reason(session, config) == (290_000, 'computed:model')
    assert threshold_for(session, config) == 290_000
    assert len(messages) == 1 and 'global:dave' in messages[0] and '100000' in messages[0]
    rt._log_ignored_override.cache_clear()


@pytest.mark.parametrize('model,expected', [
    ('claude-opus-5-5', 220_000), ('claude-sonnet-5-5', 230_000),
])
def test_computed_limit_uses_live_floor_instead_of_base(model, expected):
    assert threshold_with_reason(_session(model), {}) == (expected, 'computed:model')


@pytest.mark.parametrize('model,reason', [
    ('claude-haiku-5-5', 'price_step'), ('claude-haiku-4-5-20251001', 'window_cap'),
])
def test_hard_caps_apply_to_an_override(model, reason):
    config = {'context_rollover_by_character': {'global:dave': 200_000}}
    assert threshold_with_reason(_session(model, 30_000), config) == (
        100_000, 'override:character/' + reason)


@pytest.mark.parametrize('model', ['claude-haiku-5-5', 'claude-haiku-4-5-20251001'])
def test_hard_caps_never_drop_below_fresh_floor_room(model):
    # F=90k: a 100k cap would leave 10k of room and re-roll every few calls.
    assert threshold_with_reason(_session(model), {}) == (
        150_000, 'computed:reviewed_provisional/fresh_floor')


@pytest.mark.parametrize('pin,expected,reason', [
    (250_000, 250_000, 'override:character'), (150_000, 150_000, 'override:character'),
    (149_999, 290_000, 'computed:model'),
    (400_000, 400_000, 'override:character'),
])
def test_pin_targets_a_limit_when_it_leaves_60k_working_room(pin, expected, reason):
    assert threshold_with_reason(_session(), {
        'context_rollover_by_character': {'global:dave': pin},
    }) == (expected, reason)


def test_window_cap_and_fresh_floor_lower_bound():
    assert threshold_with_reason(_session(floor=400_000), {}) == (500_000, 'computed:model/window_cap')
    assert threshold_with_reason(_session(floor=490_000), {}) == (550_000, 'computed:model/fresh_floor')
    # Tiny global does not roll immediately on the first fresh reading.
    assert threshold_for(_session('claude-opus-5-5', 200_000), {
        'context_rollover_tokens': 60_000,
    }) >= 260_000


@pytest.mark.parametrize('model,floor', [
    ('gpt-6.1-sol', None), ('claude-future-6', None),
    ('claude-fable-5-2', None), ('claude-fable-5-2', -1),
    ('claude-fable-5-2', True), ('claude-fable-5-2', 'unknown'),
])
def test_unknown_floor_ignores_a_pin_below_the_base(model, floor):
    # 2026-11-25 review F1: after a restart no floor is known, and the legacy
    # 100k pin came back with ~11k of room over an 89k start.
    session = _session(model, floor)
    assert threshold_with_reason(session, {}) == (200_000, 'base')
    assert threshold_with_reason(session, {
        'context_rollover_by_character': {'global:dave': 100_000},
    }) == (200_000, 'base')


@pytest.mark.parametrize('provider', ['codex', 'gemini', 'qwen', 'Codex'])
def test_provider_that_never_measures_a_floor_keeps_its_pin(provider):
    # MC-1072: only the Claude readers verify a floor, so there is nothing to
    # wait for on another provider.
    session = {'character': 'global:dave', 'provider': provider}
    assert threshold_with_reason(session, {
        'context_rollover_by_character': {'global:dave': 120_000},
    }) == (120_000, 'override:character')


@pytest.mark.parametrize('provider', [None, '', 'claude', 'Claude'])
def test_claude_chat_without_a_floor_ignores_a_pin_below_the_base(provider):
    session = {'character': 'global:dave', 'model': 'opus', 'provider': provider}
    assert threshold_with_reason(session, {
        'context_rollover_by_character': {'global:dave': 120_000},
    }) == (200_000, 'base')


def test_dave_opus_never_gets_the_100k_pin_with_or_without_a_floor(monkeypatch):
    from mc import rollover_threshold as rt
    rt._log_ignored_override.cache_clear()
    messages = []
    monkeypatch.setattr(rt, '_log', messages.append)
    config = {'context_rollover_by_character': {'global:dave': 100_000}}
    session = {'character': 'global:dave', 'model': 'opus'}
    assert rt.resolve(session, config)[:2] == (200_000, 'base')
    assert rt.resolve(session, config)[:2] == (200_000, 'base')
    assert len(messages) == 1 and 'no verified fresh floor' in messages[0]
    assert rt.resolve(dict(session, _mt_fresh_floor=89_000), config)[:2] == (
        220_000, 'computed:model')
    rt._log_ignored_override.cache_clear()


@pytest.mark.parametrize('model', ['gpt-6.1-sol', 'claude-future-6', ''])
@pytest.mark.parametrize('floor,config,expected', [
    # 2026-11-25: a 100k pin over an 89k floor left 11k of working room.
    (89_000, {'context_rollover_by_character': {'global:dave': 100_000}},
     (200_000, 'base')),
    (89_000, {'context_rollover_tokens': 60_000,
              'context_rollover_by_character': {'global:dave': 100_000}},
     (149_000, 'base/fresh_floor')),
    (89_000, {'context_rollover_by_character': {'global:dave': 149_000}},
     (149_000, 'override:character')),
    (89_000, {}, (200_000, 'base')),
    (89_000, {'context_rollover_tokens': 100_000}, (149_000, 'base/fresh_floor')),
    (180_000, {}, (240_000, 'base/fresh_floor')),
])
def test_unknown_model_with_a_floor_keeps_60k_working_room(model, floor, config,
                                                           expected):
    assert threshold_with_reason(_session(model, floor), config) == expected


@pytest.mark.parametrize('model', ['opus', 'opus[1m]', 'sonnet', 'haiku'])
def test_stored_alias_never_gets_the_thrashing_pin(model):
    assert model_inputs(model) is not None
    threshold, reason = threshold_with_reason(_session(model, 89_000), {
        'context_rollover_by_character': {'global:dave': 100_000},
    })
    assert threshold >= 149_000 and reason.startswith('computed:')


@pytest.mark.parametrize('global_tokens', [0, -1, '0'])
@pytest.mark.parametrize('model', ['claude-fable-5-2', 'gpt-6.1-sol'])
def test_global_disable_wins_over_model_and_pin(global_tokens, model):
    assert threshold_with_reason(_session(model), {
        'context_rollover_tokens': global_tokens,
        'context_rollover_by_character': {'global:dave': 100_000},
    }) == (0, 'disabled')


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


@pytest.mark.parametrize('model', [None, 1, 'gpt-6.1-sol', 'fable',
                                   'claude-haiku-4-5-unknown', 'claude-fable-5-20'])
def test_unknown_models_are_not_guessed(model):
    assert model_inputs(model) is None


def test_observed_model_and_agent_model_fallback():
    assert threshold_for(_session(observed_model='claude-haiku-5-5'), {}) == 150_000
    session = {'agent_model': 'claude-fable-5-2', '_mt_fresh_floor': 90_000}
    assert threshold_for(session, {}) == 290_000
    session['model'] = ''  # Native default is unknown until observed.
    assert threshold_for(session, {}) == 200_000


@pytest.mark.parametrize('model,floor,character,effort,reference,source', [
    ('claude-fable-5-2', 78_463, None, None, 260_000, 'model'),
    ('claude-opus-5-5', 80_675, None, None, 200_000, 'model'),
    ('claude-sonnet-5-5', 72_724, None, None, 210_000, 'model'),
    ('claude-sonnet-5-5', 74_005, 'global:builder', 'high', 200_000, 'cell'),
])
def test_reproduces_calibration_references(model, floor, character, effort, reference, source):
    session = _session(model, floor, character=character, effective_effort=effort)
    tokens, reason = threshold_with_reason(session, {})
    assert abs(tokens - reference) <= 10_000
    assert tokens % 10_000 == 0
    assert reason == f'computed:{source}'


def test_override_specificity_and_stale_joint_fallthrough():
    config = {
        'context_rollover_by_character': {'global:builder': 180_000},
        'context_rollover_overrides': [
            {'effort': 'high', 'tokens': 175_000},
            {'character': 'global:builder', 'tokens': 185_000},
            {'model': 'sonnet', 'tokens': 205_000},
            {'model': 'sonnet', 'effort': 'high', 'tokens': 215_000},
            {'model': 'claude-sonnet-5-5', 'character': 'global:builder', 'tokens': 225_000},
            {'model': 'sonnet', 'character': 'global:builder', 'effort': 'high', 'tokens': 100_000},
        ],
    }
    session = _session('sonnet[1m]', 89_000, character='global:builder', effective_effort='high')
    assert threshold_with_reason(session, config) == (225_000, 'override:model+character')
    config['context_rollover_overrides'] = config['context_rollover_overrides'][:3]
    assert threshold_with_reason(session, config) == (205_000, 'override:model')
    config['context_rollover_overrides'] = config['context_rollover_overrides'][:2]
    assert threshold_with_reason(session, config) == (185_000, 'override:character')


def test_growth_inheritance_and_base():
    fable = _session(floor=78_463, character='global:code-reviewer', effective_effort='high')
    assert threshold_with_reason(fable, {})[1] == 'computed:model_effort'
    fable['effective_effort'] = 'medium'  # Model/effort sample misses coverage as well.
    assert threshold_with_reason(fable, {})[1] == 'computed:model'
    assert threshold_with_reason(_session('claude-fable-5-1'), {})[1] == 'computed:reviewed_family'
    assert threshold_with_reason(_session('unseen-model', 89_000), {}) == (200_000, 'base')
    assert threshold_with_reason(_session('sonnet', None), {}) == (200_000, 'base')


def test_stale_legacy_pin_allows_below_base_computed_limit():
    config = {'context_rollover_tokens': 250_000,
              'context_rollover_by_character': {'global:dave': 100_000}}
    assert threshold_with_reason(_session('opus', 89_000, effective_effort='high'), config) == (
        200_000, 'computed:model_effort')
    assert threshold_with_reason(_session('opus', 80_000, effective_effort='high'), config) == (
        190_000, 'computed:model_effort')


@pytest.mark.parametrize('missing', ['write_read_ratio', 'mean_growth_tokens'])
def test_reviewed_model_with_missing_cost_input_uses_base(monkeypatch, missing):
    model = 'claude-fable-5-1'
    monkeypatch.setitem(MODEL_TABLE[model], missing, None)
    assert threshold_with_reason(_session(model), {}) == (200_000, 'base')


# ── 2026-11-25 review F8: the hard caps do not wait for a floor ────────────

@pytest.mark.parametrize('model,config,expected', [
    # An override above half the window used to pass straight through.
    ('claude-fable-5-2', {'context_rollover_by_character': {'global:dave': 900_000}},
     (500_000, 'override:character/window_cap')),
    # The base on a 200k-window model was the whole window: no roll ever fired.
    ('claude-haiku-4-5-20251001', {}, (100_000, 'base/window_cap')),
    ('claude-haiku-5-5', {}, (100_000, 'base/price_step')),
    ('claude-haiku-5-5', {'context_rollover_by_character': {'global:dave': 300_000}},
     (100_000, 'override:character/price_step')),
])
def test_unknown_floor_still_gets_the_window_and_price_caps(model, config, expected):
    assert threshold_with_reason(_session(model, None), config) == expected


def test_unknown_floor_under_the_cap_is_untouched():
    assert threshold_with_reason(_session('claude-opus-5-5', None), {
        'context_rollover_by_character': {'global:dave': 100_000},
    }) == (200_000, 'base')
    assert threshold_with_reason(_session('claude-future-6', None), {
        'context_rollover_by_character': {'global:dave': 900_000},
    }) == (900_000, 'override:character')      # no table row, no cap to apply
