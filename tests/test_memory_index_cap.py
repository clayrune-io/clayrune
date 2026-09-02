"""MEMORY.md writes must REFUSE on overflow, not drift past the budget (MC-917).

index_byte_budget used to be a target we aimed at. A budget is a suggestion; a
cap that refuses the write is a forcing function — the agent has to consolidate
in the SAME turn before retrying, instead of discovering months later that the
index quietly blew past its ceiling. Ours already had: the watermark-GC leak
left 67 stale markers (37.8KB) and silently truncated the index.

Pinned here because the refusal is only useful if three things hold together:
the numbers come back to the caller, the on-disk file is untouched by a refused
write, and _commit_managed_entry keeps its documented "never raises" contract
(it is the background Scribe writer with no requester to hand a 413 to, and has
its own non-raising eviction path instead).
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mc import memory as _mem
from mc import state


@pytest.fixture
def budget(monkeypatch):
    """A small, explicit budget so tests state their own sizes."""
    monkeypatch.setitem(state.CONFIG, 'index_byte_budget', 1000)
    return 1000


def test_content_under_budget_is_accepted(budget):
    assert _mem._index_overflow('x' * 900) is None
    _mem._enforce_index_cap('x' * 900)          # must not raise


def test_exactly_at_budget_is_accepted(budget):
    """<= cap, not < cap: a file sized exactly to budget is legal."""
    assert _mem._index_overflow('x' * 1000) is None


def test_one_byte_over_is_refused(budget):
    assert _mem._index_overflow('x' * 1001) is not None
    with pytest.raises(_mem.MemoryCapExceeded):
        _mem._enforce_index_cap('x' * 1001)


def test_refusal_reports_the_three_numbers_the_caller_needs(budget):
    """The 413 body is only actionable if it says how much to cut."""
    with pytest.raises(_mem.MemoryCapExceeded) as ei:
        _mem._enforce_index_cap('x' * 1500)
    ex = ei.value
    # Named attributes, because the 413 body in project_routes._index_cap_error
    # reads them by name — positional args would let that drift silently.
    assert (ex.current_bytes, ex.budget_bytes, ex.overflow_bytes) == (1500, 1000, 500)
    assert ex.current_bytes - ex.overflow_bytes == ex.budget_bytes
    assert '1500' in str(ex) and '1000' in str(ex)


def test_budget_is_measured_in_BYTES_not_characters(budget):
    """The cap is bytes on purpose — line/char budgets cannot see UTF-8 weight,
    which is how the index overshot before. 400 3-byte chars = 1200B > 1000B."""
    text = '\u4e2d' * 400
    assert len(text) < 1000                      # fits by character count
    ov = _mem._index_overflow(text)
    assert ov is not None and ov[0] == 1200      # refused by byte count


def test_commit_managed_entry_still_never_raises_on_overflow(budget, monkeypatch):
    """Its docstring promises this, and the background Scribe/teardown writers
    depend on it — there is no request/response caller to hand an error to.
    It evicts to the archive instead. Do not route it through the hard gate."""
    import inspect
    doc = (_mem._commit_managed_entry.__doc__ or '').lower()
    assert 'never raise' in doc or 'never raises' in doc, \
        "the non-raising contract must stay documented at the function"
    src = inspect.getsource(_mem._commit_managed_entry)
    assert '_enforce_index_cap' not in src, \
        "_commit_managed_entry must not call the raising gate (MC-917 note)"
