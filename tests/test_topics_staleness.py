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
    """The signal that actually matters day to day: someone kept talking, and
    then stopped. The chat must be SETTLED (older than topics_settle_seconds)
    or it is a conversation still in progress — see the active-chat test."""
    _wire(monkeypatch, [_c(-600), _c(-7200)])
    cache = {'generated_at': (NOW - timedelta(minutes=30)).isoformat(),
             'chat_count': 2}
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


# ── clustering seed: automation prompts must not become the topic ────────────

def test_automation_prompt_is_not_used_as_the_clustering_seed(monkeypatch):
    """A steward/scheduled run's turn-1 text is a boilerplate operating prompt.

    Used as a seed it collapses unrelated work into one bucket purely because
    those sessions all START the same way. Observed 2026-08-06: a whole
    load-time investigation clustered under "Autonomous agents" because it had
    been appended to a transcript opening with `[Steward cycle] …`. The real
    subject is the completion summary, or what the user actually typed.
    """
    monkeypatch.setattr(tr, '_load_project', lambda pid: {'project_path': '/p'})
    monkeypatch.setattr(tr, '_recent_transcripts', lambda pp, limit=50: [{
        'session_id': 'sc', 'turns': 6,
        'first_user': '[Steward cycle] You are the autonomous STEWARD of this project',
        'last_user': 'is this what you were asking for?',
        'mtime': NOW.timestamp(),
    }])
    monkeypatch.setattr(tr, '_load_agent_log', lambda pid: [
        {'claude_session_id': 'sc', 'summary': 'Investigated page load time'}])
    sig = tr._gather_signals('p')[0]
    assert sig['ask'] == 'Investigated page load time'
    assert 'Steward cycle' not in sig['ask']


def test_falls_back_to_user_text_when_no_summary(monkeypatch):
    monkeypatch.setattr(tr, '_load_project', lambda pid: {'project_path': '/p'})
    monkeypatch.setattr(tr, '_recent_transcripts', lambda pp, limit=50: [{
        'session_id': 'sc', 'turns': 6,
        'first_user': '[Steward cycle] You are the autonomous STEWARD',
        'last_user': 'fix the header spacing please',
        'mtime': NOW.timestamp(),
    }])
    monkeypatch.setattr(tr, '_load_agent_log', lambda pid: [])
    assert tr._gather_signals('p')[0]['ask'] == 'fix the header spacing please'


def test_ordinary_openers_are_left_alone(monkeypatch):
    """Narrow by design — a false positive costs a chat its best seed."""
    monkeypatch.setattr(tr, '_load_project', lambda pid: {'project_path': '/p'})
    monkeypatch.setattr(tr, '_recent_transcripts', lambda pp, limit=50: [{
        'session_id': 'u', 'turns': 6,
        'first_user': '[note] the browser pane is not discoverable to agents',
        'last_user': 'thanks', 'mtime': NOW.timestamp(),
    }])
    monkeypatch.setattr(tr, '_load_agent_log', lambda pid: [
        {'claude_session_id': 'u', 'summary': 'a summary'}])
    assert tr._gather_signals('p')[0]['ask'].startswith('[note]')


def test_marker_stays_pinned_to_the_canonical_constant():
    """Same discipline as tests/test_distiller_safety.py: a rename of the
    steward marker must not silently un-gate this."""
    from steward import fence
    assert tr._AUTOMATION_PROMPT_RE.match(fence.STEWARD_MARKER + ' do a cycle')


def test_an_active_chat_does_not_make_the_digest_stale(monkeypatch):
    """A chat you are currently in is touched every turn. Counting it would
    keep the banner on for the whole conversation, and a banner that is always
    on is one you stop reading. It is re-clustered when the session ends —
    which is exactly when auto-refresh fires."""
    _wire(monkeypatch, [_c(-3600), _c(-5)])          # one settled, one live
    cache = {'generated_at': NOW.isoformat(), 'chat_count': 2}
    stale, reason, _ = tr._staleness('p', cache)
    assert not stale, reason


def test_a_settled_chat_still_does(monkeypatch):
    """The other half: once it stops moving, it counts."""
    _wire(monkeypatch, [_c(-3600), _c(-600)])
    cache = {'generated_at': (NOW - timedelta(minutes=30)).isoformat(),
             'chat_count': 2}
    stale, reason, _ = tr._staleness('p', cache)
    assert stale and 'changed' in reason
