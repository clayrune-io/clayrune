"""Topics digest staleness (mc/blueprints/topics_routes.py).

`stale` used to mean only "no cache exists", so any cache reported itself
fresh. Found 2026-08-06: mission_control's digest was generated 2026-07-28,
covered 30 of 207 transcripts, and still answered `stale: false`.

That is tolerable for a side panel and disqualifying for the primary
navigation surface Tier 3 proposes making it — the user would be steering by
a nine-day-old map with nothing telling them so. These tests pin the two
signals that make the flag mean something, and the best-effort posture that
keeps a broken scan from taking the panel down with it.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc.blueprints import topics_routes as tr  # noqa: E402

NOW = datetime.now(timezone.utc)


def _wire(monkeypatch, convos):
    monkeypatch.setattr(tr, '_load_project', lambda pid: {'project_path': '/p'})
    monkeypatch.setattr(tr, '_recent_transcripts', lambda pp, limit=50: convos)


def _c(mtime_offset_s, turns=5, first='a reasonably long opening message'):
    return {'session_id': 'x', 'turns': turns, 'first_user': first,
            'mtime': (NOW + timedelta(seconds=mtime_offset_s)).timestamp()}


def test_fresh_when_nothing_changed(monkeypatch):
    _wire(monkeypatch, [_c(-3600), _c(-7200)])
    cache = {'generated_at': NOW.isoformat(), 'chat_count': 2}
    stale, reason, n = tr._staleness('p', cache)
    assert (stale, n) == (False, 2) and reason == ''


def test_stale_when_a_chat_moved_after_generation(monkeypatch):
    """The signal that actually matters day to day: someone kept talking."""
    _wire(monkeypatch, [_c(+60), _c(-7200)])
    cache = {'generated_at': NOW.isoformat(), 'chat_count': 2}
    stale, reason, _ = tr._staleness('p', cache)
    assert stale and 'changed' in reason


def test_stale_when_the_chat_count_drifted(monkeypatch):
    """Catches the real-world case: 30 chats when built, 207 now, but every
    transcript predates the digest (e.g. after a restore or a coverage gap)."""
    _wire(monkeypatch, [_c(-3600), _c(-3600), _c(-3600)])
    cache = {'generated_at': NOW.isoformat(), 'chat_count': 2}
    stale, reason, n = tr._staleness('p', cache)
    assert stale and n == 3 and '3 chats now, 2 when built' in reason


def test_trivial_fragments_do_not_count_as_change(monkeypatch):
    """One-turn "ok" transcripts — auth-probe leftovers among them — must not
    make a digest look stale, since the synthesizer drops them too. Counts have
    to compare like with like or the panel nags forever."""
    _wire(monkeypatch, [_c(-3600), _c(+60, turns=1, first='ok')])
    cache = {'generated_at': NOW.isoformat(), 'chat_count': 1}
    stale, reason, n = tr._staleness('p', cache)
    assert (stale, n) == (False, 1), reason


def test_scan_failure_is_not_fatal(monkeypatch):
    """Best-effort: a broken scan reports not-stale rather than 500ing the
    panel — same posture as the rest of the topics surface."""
    monkeypatch.setattr(tr, '_load_project', lambda pid: {'project_path': '/p'})

    def _boom(pp, limit=50):
        raise OSError('transcript dir gone')

    monkeypatch.setattr(tr, '_recent_transcripts', _boom)
    assert tr._staleness('p', {'generated_at': NOW.isoformat(),
                               'chat_count': 9}) == (False, '', 9)


def test_missing_generated_at_falls_back_to_count(monkeypatch):
    """A cache written by an older build has no usable timestamp; the count
    comparison still has to work or such a digest is stale-blind forever."""
    _wire(monkeypatch, [_c(-3600), _c(-3600)])
    stale, _, n = tr._staleness('p', {'chat_count': 1})
    assert stale and n == 2
