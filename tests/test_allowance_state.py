"""Tests for mc/allowance_state.py — VENDOR_AGNOSTIC_PROGRAM.md §4.

The Codex fixture below is the REAL line captured 2026-09-18 from
~/.codex/sessions/2026/09/17/rollout-2026-09-17T22-19-23-01a0b2f4-*.jsonl
(ordinal 82), not an invented shape. The Claude fixture's field SHAPE
(resetsAt as unix epoch seconds, not an ISO string) mirrors the real
`rate_limit_info` this box captured live in data/system_status.json on
2026-09-18T06:26 (status: "allowed" there — this test flips status to
'rejected', Anthropic's documented-but-not-live-captured exhausted value,
which is why detect_from_claude_rate_limit_event marks verified=False).
"""
import json

import pytest

from mc import allowance_state as al


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path):
    al.wire(tmp_path / 'allowance_state.json')
    yield
    al._STATE = {}


# ── record / get / clear / expiry ──────────────────────────────────────────

def test_not_exhausted_by_default():
    assert al.get('claude') is None
    assert al.is_exhausted('claude') is False
    assert al.refusal_message('claude') == ''


def test_record_and_get_roundtrip():
    al.record_exhaustion('codex', limit_kind='usage_limit',
                          resets_at='2026-09-24T14:58:00+00:00',
                          raw_ref='hit limit')
    entry = al.get('codex')
    assert entry is not None
    assert entry['limit_kind'] == 'usage_limit'
    assert al.is_exhausted('codex') is True


def test_clear_exhaustion_removes_state():
    al.record_exhaustion('gemini', limit_kind='unknown')
    assert al.is_exhausted('gemini') is True
    al.clear_exhaustion('gemini')
    assert al.is_exhausted('gemini') is False


def test_expired_reset_time_auto_clears_on_read():
    al.record_exhaustion('claude', limit_kind='five_hour',
                          resets_at='2000-01-01T00:00:00+00:00')
    assert al.get('claude') is None  # already in the past


def test_unknown_reset_time_never_auto_clears():
    al.record_exhaustion('gemini', limit_kind='unknown', resets_at=None)
    assert al.is_exhausted('gemini') is True  # needs clear_exhaustion(), not time


def test_state_persists_across_wire_reload(tmp_path):
    path = tmp_path / 'allowance_state.json'
    al.wire(path)
    al.record_exhaustion('qwen', limit_kind='daily')
    assert path.is_file()
    al.wire(path)  # simulate a fresh process reading the same file
    assert al.is_exhausted('qwen') is True


def test_refusal_message_names_vendor_limit_and_reset():
    al.record_exhaustion('codex', limit_kind='usage_limit',
                          resets_at_display='Sep 24, 2026 7:58 AM')
    msg = al.refusal_message('codex')
    assert 'codex' in msg
    assert 'usage_limit' in msg
    assert 'Sep 24, 2026 7:58 AM' in msg
    assert 'no fallback' in msg


def test_display_text_unknown_reset_time():
    al.record_exhaustion('qwen', limit_kind='unknown')
    assert al.display_text('qwen') == 'Out of allowance, resets reset time unknown'


# ── Codex: real captured event ──────────────────────────────────────────────

CODEX_REAL_TASK_COMPLETE = json.loads(
    '{"timestamp":"2026-09-18T05:21:28.415Z","ordinal":82,"type":"event_msg",'
    '"payload":{"type":"task_complete","turn_id":"01a0b2f4-81a1-7202-87ed-'
    '35b5637c0e4c","last_agent_message":null,"error":{"message":"You\'ve hit '
    'your usage limit. Visit https://chatgpt.com/codex/settings/usage to '
    'purchase more credits or try again at Sep 24th, 2026 7:58 AM.",'
    '"codex_error_info":"usage_limit_exceeded"},"started_at":1789708763,'
    '"completed_at":1789708888,"duration_ms":124861,'
    '"time_to_first_token_ms":5276}}'
)


def test_detect_codex_real_captured_usage_limit_event():
    hit = al.detect('codex', CODEX_REAL_TASK_COMPLETE)
    assert hit is not None
    assert hit['limit_kind'] == 'usage_limit'
    assert hit['verified'] is True
    assert hit['resets_at_display'] == 'Sep 24th, 2026 7:58 AM'
    assert hit['resets_at'] is not None
    assert hit['resets_at'].startswith('2026-09-24T07:58:00')
    assert 'usage_limit_exceeded' not in hit['raw_ref'] or 'hit your usage limit' in hit['raw_ref']


def test_observe_codex_real_event_records_and_refuses():
    result = al.observe('codex', CODEX_REAL_TASK_COMPLETE)
    assert result is not None
    assert al.is_exhausted('codex') is True
    msg = al.refusal_message('codex')
    assert 'codex' in msg
    assert 'Sep 24th, 2026 7:58 AM' in msg


def test_detect_codex_ignores_unrelated_task_complete():
    ok = {"type": "event_msg", "payload": {"type": "task_complete", "error": None}}
    assert al.detect('codex', ok) is None


def test_detect_codex_dotted_error_shape_also_recognized():
    dotted = {"type": "error",
              "error": {"message": "try again at Sep 24th, 2026 7:58 AM.",
                        "codex_error_info": "usage_limit_exceeded"}}
    hit = al.detect('codex', dotted)
    assert hit is not None
    assert hit['verified'] is True


# ── Claude: real field shape, documented-not-captured exhausted value ───────

def test_detect_claude_rejected_status_with_epoch_resets_at():
    msg = {
        'type': 'rate_limit_event',
        'rate_limit_info': {
            'status': 'rejected',
            'resetsAt': 1789714800,  # real field shape: unix epoch seconds
            'rateLimitType': 'five_hour',
            'overageStatus': 'rejected',
            'isUsingOverage': False,
        },
    }
    hit = al.detect('claude', msg)
    assert hit is not None
    assert hit['limit_kind'] == 'five_hour'
    assert hit['verified'] is False  # documented value, not live-captured exhausted
    assert hit['resets_at'] is not None
    assert hit['resets_at'].startswith('2026-09-18')


def test_detect_claude_allowed_status_is_not_exhaustion():
    msg = {
        'type': 'rate_limit_event',
        'rate_limit_info': {'status': 'allowed', 'resetsAt': 1789714800,
                             'rateLimitType': 'five_hour'},
    }
    assert al.detect('claude', msg) is None


def test_detect_claude_ignores_non_rate_limit_messages():
    assert al.detect('claude', {'type': 'assistant'}) is None


# ── Gemini / Qwen: text-heuristic, explicitly unverified ───────────────────

def test_detect_gemini_quota_error_text():
    msg = {'type': 'result', 'status': 'error',
           'error': {'message': 'You have exhausted your daily quota on this model'}}
    hit = al.detect('gemini', msg)
    assert hit is not None
    assert hit['verified'] is False
    assert hit['resets_at'] is None


def test_detect_gemini_non_quota_error_is_not_exhaustion():
    msg = {'type': 'result', 'status': 'error',
           'error': {'message': 'network unreachable'}}
    assert al.detect('gemini', msg) is None


def test_detect_qwen_quota_error_text():
    msg = {'type': 'result', 'is_error': True,
           'error': {'message': '429 Resource has been exhausted (rate limit)'}}
    hit = al.detect('qwen', msg)
    assert hit is not None
    assert hit['verified'] is False


def test_detect_unknown_provider_returns_none():
    assert al.detect('opencode', {'type': 'error'}) is None
