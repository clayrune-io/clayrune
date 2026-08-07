"""Restored-chat-buffer size (mc/blueprints/agent_routes.py).

A revived conversation opened 85% truncated. Four call sites built the same
buffer and disagreed about how much of it to keep: dispatch-preload,
/reconstruct and the preview endpoint all passed 300, while the revival path
silently took a default of 40. Measured on a live 269-message chat, the visible
history began mid-thread and the user could not scroll back to their own
opening question — which reads as lost data, not as a display limit.

The model was never affected: `claude -r <csid>` restores full context. That
gap between what the agent knows and what the user can see is exactly why the
truncation notice below matters as much as the cap.
"""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc.blueprints import agent_routes as ar  # noqa: E402
from mc import state  # noqa: E402


def test_default_is_shared_and_generous(monkeypatch):
    monkeypatch.setitem(state.CONFIG, 'transcript_buffer_max_messages', 300)
    assert ar._transcript_buffer_default() == 300


def test_default_is_configurable(monkeypatch):
    monkeypatch.setitem(state.CONFIG, 'transcript_buffer_max_messages', 42)
    assert ar._transcript_buffer_default() == 42


@pytest.mark.parametrize('bad', ['nonsense', None, 0, -5])
def test_bad_config_falls_back_to_a_usable_number(monkeypatch, bad):
    """A malformed knob must never produce a 0-message buffer — that would look
    exactly like the bug this fixes."""
    monkeypatch.setitem(state.CONFIG, 'transcript_buffer_max_messages', bad)
    assert ar._transcript_buffer_default() >= 1


def test_revival_uses_the_shared_default_not_its_own(monkeypatch):
    """The regression itself: revival must not carry a private, smaller cap."""
    seen = {}
    monkeypatch.setitem(state.CONFIG, 'transcript_buffer_max_messages', 300)
    monkeypatch.setattr(ar, '_transcript_buffer_lines',
                        lambda pp, cs, ul, max_messages=None: seen.setdefault('cap', max_messages) and [])
    monkeypatch.setattr(ar, '_buffer_truncation_notice', lambda *a, **k: None)
    ar._revive_history_lines('/p', 'csid', 'Ron')
    # None means "use the shared default" — the one thing it must not do is
    # pass a hardcoded 40.
    assert seen.get('cap') in (None, 300)


def test_truncation_notice_when_history_is_cut(monkeypatch):
    monkeypatch.setattr(ar, '_find_transcript_file', lambda pp, cs: Path('x.jsonl'))
    monkeypatch.setattr(ar, '_parse_transcript_messages',
                        lambda f, max_messages=0: [{'role': 'user'}] * 900)
    note = ar._buffer_truncation_notice('/p', 'csid', 300)
    assert note and '600 of 900' in note
    # It must also say the agent is not similarly limited, or the user will
    # reasonably assume the conversation was damaged.
    assert 'full conversation' in note


def test_no_notice_when_everything_fits(monkeypatch):
    monkeypatch.setattr(ar, '_find_transcript_file', lambda pp, cs: Path('x.jsonl'))
    monkeypatch.setattr(ar, '_parse_transcript_messages',
                        lambda f, max_messages=0: [{'role': 'user'}] * 12)
    assert ar._buffer_truncation_notice('/p', 'csid', 300) is None


def test_notice_never_raises(monkeypatch):
    """Best-effort: a counting failure must not break a revival."""
    monkeypatch.setattr(ar, '_find_transcript_file',
                        lambda pp, cs: (_ for _ in ()).throw(OSError('gone')))
    assert ar._buffer_truncation_notice('/p', 'csid', 300) is None
