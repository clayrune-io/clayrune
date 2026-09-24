"""MC-964 Step D.2/D.3 — state-contradiction surfacing.

§7 decision 2 (adopted position mc964memoryoverhaul...): a newer memory unit
or a user chat assertion naming an exhausted vendor triggers ONE re-probe
(`allowance_state.heal()`, already throttled 30s/vendor), never a loop, never
a silent fallback to another vendor. `_allowance_conflict_block` is the single
function both triggers go through, called from `_build_agent_context` right
after the RELEVANT MEMORY read-floor block is built (agent_routes.py).

The acceptance fixture from docs/MEMORY_OVERHAUL_PLAN.md §6 Step D: a live
"codex exhausted, resets +5d" record plus a newer memory unit "Ron topped up"
produces ONE probe, not a refusal — reproduced here as
`test_a_matching_memory_unit_triggers_exactly_one_reprobe_not_a_refusal`.
"""
import pytest

from mc import allowance_state as al
from tests.test_revive_notify_carry import ar  # noqa: F401 — reuse its fixture


@pytest.fixture(autouse=True)
def _isolated_allowance_state(tmp_path):
    al.wire(tmp_path / 'allowance_state.json')
    al._LAST_PROBE.clear()
    yield
    al._STATE = {}
    al._LAST_PROBE.clear()


def _patch_probe(monkeypatch, answer):
    from mc import agent_runtime
    for rt in agent_runtime._RUNTIMES.values():
        monkeypatch.setattr(rt, 'probe_allowance', lambda: answer)


def test_no_states_no_block(ar):  # noqa: F811
    assert ar._allowance_conflict_block('anything', []) == ''


def test_no_mention_no_block_and_no_probe(ar, monkeypatch):  # noqa: F811
    al.record_exhaustion('codex', limit_kind='usage_limit',
                          resets_at_display='Sep 24, 2026 7:58 AM')
    probed = []

    def _spy():
        probed.append(True)
        return True
    _patch_probe(monkeypatch, None)
    from mc import agent_runtime
    monkeypatch.setattr(agent_runtime._RUNTIMES['codex'], 'probe_allowance', _spy)

    assert ar._allowance_conflict_block('unrelated chat message', []) == ''
    assert not probed, 'a vendor never mentioned must not spend a probe'


def test_a_matching_memory_unit_triggers_exactly_one_reprobe_not_a_refusal(ar, monkeypatch):  # noqa: F811
    """The plan's own acceptance fixture: codex exhausted, resets +5d, plus a
    newer memory unit 'Ron topped up' -> one probe, not a refusal."""
    al.record_exhaustion('codex', limit_kind='usage_limit',
                          resets_at_display='Sep 29, 2026 7:58 AM')
    probe_calls = []

    def _probe():
        probe_calls.append(True)
        return True  # vendor itself says usable now
    _patch_probe(monkeypatch, None)
    from mc import agent_runtime
    monkeypatch.setattr(agent_runtime._RUNTIMES['codex'], 'probe_allowance', _probe)

    hits = [{'file': 'archive_2026_09_20.md',
              'snippet': 'Ron topped up codex credits'}]
    block = ar._allowance_conflict_block('', hits)

    assert len(probe_calls) == 1, 'exactly one re-probe, never a loop'
    assert al.is_exhausted('codex') is False, 'the real probe cleared the stale record'
    assert 'codex' in block
    assert 'cleared' in block
    assert 'archive_2026_09_20.md' in block


def test_still_exhausted_after_reprobe_names_the_vendor_and_age(ar, monkeypatch):  # noqa: F811
    al.record_exhaustion('codex', limit_kind='usage_limit',
                          resets_at_display='Sep 29, 2026 7:58 AM')
    _patch_probe(monkeypatch, False)  # vendor confirms still out

    block = ar._allowance_conflict_block("codex should be back by now", [])

    assert 'codex' in block
    assert 'still exhausted' in block
    assert al.is_exhausted('codex') is True, 'a False probe answer must not clear the record'


def test_chat_assertion_alone_triggers_the_same_reprobe_as_a_memory_unit(ar, monkeypatch):  # noqa: F811
    al.record_exhaustion('gemini', limit_kind='unknown')
    _patch_probe(monkeypatch, True)

    block = ar._allowance_conflict_block('I topped up gemini just now', [])

    assert 'this message' in block
    assert al.is_exhausted('gemini') is False


def test_reprobe_is_throttled_within_the_heal_window(ar, monkeypatch):
    """heal()'s own 30s/vendor throttle is what bounds repeats across calls —
    this function does not add its own throttle, it relies on that one."""
    al.record_exhaustion('codex', limit_kind='usage_limit')
    probe_calls = []

    def _probe():
        probe_calls.append(True)
        return False
    _patch_probe(monkeypatch, None)
    from mc import agent_runtime
    monkeypatch.setattr(agent_runtime._RUNTIMES['codex'], 'probe_allowance', _probe)

    ar._allowance_conflict_block('topped up codex', [])
    ar._allowance_conflict_block('topped up codex', [])  # same turn cadence, back to back

    assert len(probe_calls) == 1, 'the 30s/vendor throttle must survive a second call'


def test_refusal_message_carries_age_and_last_probe_date(ar, monkeypatch):  # noqa: F811
    al.record_exhaustion('codex', limit_kind='usage_limit',
                          resets_at_display='Sep 29, 2026 7:58 AM')
    _patch_probe(monkeypatch, False)
    al.heal('codex', lambda: False)

    msg = al.refusal_message('codex')
    assert 'record' in msg
    assert 'last probed' in msg
