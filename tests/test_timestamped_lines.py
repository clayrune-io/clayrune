"""MC-954 day-divider correctness: `TimestampedLines` must stamp each line at
PRODUCTION time (when it was appended), never at OBSERVATION time (when a
reader first looked).

The bug this guards against (found in code review of d7a2062, fixed here):
the original day-divider implementation stamped each line's timestamp inside
the `/agent/stream` SSE route — the first time ANY client happened to poll
it. An unattended run producing lines while nobody's chat is open (a
scheduled backlog runner working overnight) got every one of those lines
stamped with the time a human LATER opened the chat, hours or days after
they actually ran. `TimestampedLines` fixes this by stamping at every
`append`/`extend` call site instead, so correctness doesn't depend on any
particular reader having been present.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc.core import TimestampedLines  # noqa: E402


def test_append_stamps_at_production_time_not_observation_time():
    """The core regression: lines appended with no reader attached must keep
    their ORIGINAL append-time timestamps once a reader (e.g. an SSE stream)
    finally looks at them later."""
    lines = TimestampedLines()
    lines.append('[22:00] overnight run started')
    early_ts = lines.ts[0]

    # Simulate real time passing with nobody watching (the overnight case).
    time.sleep(0.05)

    lines.append('[01:00] overnight run finished')
    late_ts = lines.ts[1]

    # A reader "opens the chat" now — reading must NOT rewrite history.
    _ = list(lines)
    assert lines.ts[0] == early_ts
    assert lines.ts[1] == late_ts
    assert early_ts != late_ts
    assert early_ts < late_ts


def test_extend_stamps_each_item_independently():
    lines = TimestampedLines()
    lines.extend(['a', 'b', 'c'])
    assert len(lines.ts) == 3
    assert all(lines.ts)


def test_iadd_delegates_to_extend_and_stamps():
    lines = TimestampedLines()
    lines += ['a', 'b']
    assert len(lines) == len(lines.ts) == 2


def test_explicit_historical_ts_is_preserved_not_overwritten():
    """Revive/resume paths know a line's REAL historical date (from a
    transcript) — passing ts= must be honored verbatim, not replaced with
    now()."""
    lines = TimestampedLines()
    lines.append('old line from a prior day', ts='2026-01-01T00:00:00Z')
    assert lines.ts[0] == '2026-01-01T00:00:00Z'


def test_construction_with_known_ts_list():
    lines = TimestampedLines(['a', 'b'], ts=['2026-01-01T00:00:00Z', None])
    assert lines.ts == ['2026-01-01T00:00:00Z', None]
    assert list(lines) == ['a', 'b']


def test_construction_ts_length_mismatch_raises():
    with pytest.raises(ValueError):
        TimestampedLines(['a', 'b'], ts=['only-one'])


def test_slice_trim_keeps_lines_and_ts_in_lock_step():
    """The 2000-line cap trim (`session['log_lines'] = session['log_lines'][-1500:]`)
    must slice `.ts` identically — an off-by-one here would misalign every
    later line-index-to-date lookup for the rest of the session."""
    lines = TimestampedLines()
    for i in range(10):
        lines.append(f'line{i}', ts=f'ts{i}')
    trimmed = lines[-5:]
    assert isinstance(trimmed, TimestampedLines)
    assert list(trimmed) == ['line5', 'line6', 'line7', 'line8', 'line9']
    assert trimmed.ts == ['ts5', 'ts6', 'ts7', 'ts8', 'ts9']


def test_no_ts_known_is_none_never_fabricated_as_now():
    """A line with no production-time stamp available must read back as
    None — never silently promoted to `now()` by a later reader."""
    lines = TimestampedLines(['a'], ts=[None])
    assert lines.ts[0] is None


def test_pop_and_delitem_keep_ts_aligned():
    lines = TimestampedLines(['a', 'b', 'c'], ts=['ta', 'tb', 'tc'])
    lines.pop(1)
    assert list(lines) == ['a', 'c']
    assert lines.ts == ['ta', 'tc']
    del lines[0]
    assert list(lines) == ['c']
    assert lines.ts == ['tc']
