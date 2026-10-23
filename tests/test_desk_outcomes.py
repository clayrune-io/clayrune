"""MC-968: observation age, honest unknowns and config boundary."""
from datetime import datetime, timezone

import pytest

from mc.desk_outcomes import receipt_outcomes, threshold_from_config, validate_threshold

NOW = datetime(2026, 10, 23, tzinfo=timezone.utc)


def receipt(value=4, at='2026-10-22T00:00:00Z', **extra):
    return {'id': 'receipt', 'published_at': '2026-10-21T00:00:00Z',
            'outcomes': [{'metric': 'impressions', 'value': value, 'at': at}], **extra}


@pytest.mark.parametrize('value,expected', [(0, True), (4, True), (5, False), (100, False)])
def test_absolute_threshold_and_exact_24_hours(value, expected):
    assert receipt_outcomes(receipt(value), now=NOW)['possibly_suppressed'] is expected


@pytest.mark.parametrize('value', [None, True, False, '0', -1, float('nan'), float('inf'), {}, 10**1000])
def test_invalid_measurement_is_unknown(value):
    result = receipt_outcomes(receipt(value), now=NOW)
    assert result['possibly_suppressed'] is None
    assert result['impressions'] is None


@pytest.mark.parametrize('at', [None, '', 'bad', '2026-10-22',
                               '2026-10-20T00:00:00Z', '2026-10-24T00:00:00Z',
                               '2026-10-21T23:59:59Z'])
def test_missing_invalid_future_or_young_measurement_is_unknown(at):
    assert receipt_outcomes(receipt(at=at), now=NOW)['possibly_suppressed'] is None


@pytest.mark.parametrize('changes', [{'id': None}, {'outcomes': []}, {'published_at': None},
                                      {'published_at': 'bad'}, {'published_at': '2026-10-21'}])
def test_receipt_or_measurement_missing(changes):
    assert receipt_outcomes(receipt(**changes), now=NOW)['possibly_suppressed'] is None


def test_latest_observation_wins_by_time_not_append_order():
    row = receipt()
    row['outcomes'].insert(0, {'metric': 'impressions', 'value': 12, 'at': '2026-10-23T00:00:00Z'})
    row['outcomes'].append({'metric': 'replies', 'value': 0, 'at': '2026-10-22T00:00:00Z'})
    result = receipt_outcomes(row, now=NOW)
    assert result['impressions'] == 12 and result['replies'] == 0
    assert result['possibly_suppressed'] is False
    row['outcomes'][0]['value'] = -1
    assert receipt_outcomes(row, now=NOW)['possibly_suppressed'] is None


def test_timezone_offsets_and_configurable_threshold():
    row = receipt(6, at='2026-10-21T19:00:00-05:00')
    assert receipt_outcomes(row, now=NOW, threshold=7)['possibly_suppressed'] is True
    assert threshold_from_config({}) == 5
    assert threshold_from_config({'desk_suppression_impressions': 7}) == 7
    assert threshold_from_config({'desk_suppression_impressions': None}) == 5


@pytest.mark.parametrize('value', [True, -1, None, '5', float('nan'), float('inf')])
def test_bad_threshold_config_refused(value):
    with pytest.raises(ValueError):
        validate_threshold(value)


def test_malformed_observation_cannot_resurrect_old_low_value():
    row = receipt()
    row['outcomes'].append({'metric': 'impressions', 'value': 15, 'at': 'bad'})
    assert receipt_outcomes(row, now=NOW)['possibly_suppressed'] is None


def test_equal_timestamp_uses_last_appended_measurement():
    row = receipt()
    row['outcomes'].append({'metric': 'impressions', 'value': 15, 'at': '2026-10-22T00:00:00Z'})
    assert receipt_outcomes(row, now=NOW)['possibly_suppressed'] is False
