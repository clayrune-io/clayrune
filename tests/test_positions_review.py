"""Reviewing standing positions (mc/positions_review.py) — Dave phase 3.

The reviewer's value is entirely in two properties, and both fail SILENTLY:

  * it must never edit a position. An unattended agent rewriting the rulings
    that steer every other agent is the authority-guard violation in a
    different hat, and nothing about it would look wrong in a log.
  * a flag must be raised ONCE. A condition that tripped last week and still
    trips today is not news; a nightly re-raise reads as diligence right up to
    the point where the channel stops being read, and then it costs you the one
    night that mattered.
"""
import sys
import threading
import time
from datetime import timedelta
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

P = {'id': 'p1'}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import server  # noqa: F401
    from mc import memory as mem
    from mc import positions_review as pr
    monkeypatch.setattr(mem, '_get_memory_path', lambda p: tmp_path / 'MEMORY.md')
    (tmp_path / 'MEMORY.md').write_text('# index\n', encoding='utf-8')
    return mem, pr, tmp_path


def _one(mem, reason='we built it ourselves', expires='if the link layer rots'):
    return mem.write_position(P, subject='Obsidian', verdict='declined',
                              reason=reason, expires_when=expires)


def _rec(mem, fn):
    return next(r for r in mem.list_positions(P) if r['file'] == fn)


# ── what is due ──────────────────────────────────────────────────────────────

def test_a_new_position_is_due_immediately(env):
    mem, pr, _ = env
    fn = _one(mem)
    due = pr.positions_due(P)
    assert [r['file'] for r in due] == [fn]
    assert due[0]['_never_reviewed'] is True


def test_a_reviewed_position_rests_until_the_interval_is_up(env):
    mem, pr, _ = env
    fn = _one(mem)
    now = pr._now()
    pr.record_review(P, _rec(mem, fn), tripped=False, finding='checked', now=now)
    assert pr.positions_due(P, interval_days=7, now=now + timedelta(days=3)) == []
    assert len(pr.positions_due(P, interval_days=7, now=now + timedelta(days=8))) == 1


def test_editing_a_position_makes_it_due_again_at_once(env):
    """The previous review judged text that no longer exists."""
    mem, pr, _ = env
    fn = _one(mem)
    now = pr._now()
    pr.record_review(P, _rec(mem, fn), finding='checked', now=now)
    assert pr.positions_due(P, now=now + timedelta(days=1)) == []
    _one(mem, reason='actually, because the graph view is the blocker')  # supersedes
    due = pr.positions_due(P, now=now + timedelta(days=1))
    assert len(due) == 1 and due[0]['_changed'] is True


def test_a_position_with_no_trigger_is_still_due(env):
    """It is MORE worth a look, not less — nobody has revisited it since the
    day it was made, and 'this needs a trigger' is the useful finding."""
    mem, pr, _ = env
    _one(mem, expires='')
    brief = pr.render_brief(P)
    assert 'NOTHING RECORDED' in brief


def test_the_brief_says_what_was_found_last_time(env):
    mem, pr, _ = env
    fn = _one(mem)
    now = pr._now()
    pr.record_review(P, _rec(mem, fn), finding='0 unresolved links across 412 notes',
                     now=now)
    assert '0 unresolved links' in pr.render_brief(P, now=now + timedelta(days=8))


# ── raising a flag exactly once ──────────────────────────────────────────────

def test_a_trip_is_news_once(env):
    mem, pr, _ = env
    fn = _one(mem)
    first = pr.record_review(P, _rec(mem, fn), tripped=True, finding='they shipped it')
    assert first['newly_flagged'] is True
    again = pr.record_review(P, _rec(mem, fn), tripped=True, finding='still shipped')
    assert again['newly_flagged'] is False
    assert again['flagged'] is True, 'the flag must stay OPEN, just stop shouting'


def test_a_human_edit_re_arms_the_flag(env):
    """Ron rewriting the reason means he re-opened the question; the old flag
    was about text that is gone."""
    mem, pr, _ = env
    fn = _one(mem)
    pr.record_review(P, _rec(mem, fn), tripped=True, finding='they shipped it')
    _one(mem, reason='reconsidered: we keep ours because X')
    again = pr.record_review(P, _rec(mem, fn), tripped=True, finding='still shipped')
    assert again['newly_flagged'] is True


def test_an_agents_own_tripped_false_does_not_clear_a_human_pending_flag(env):
    """MC-910 bug 1. A flag raised for Ron is not this module's to clear —
    only a human closing the question (by editing or forgetting the position,
    which changes its hash) does that. An agent judging on the SAME text that
    the condition no longer holds must record the disagreement, not erase the
    flag Ron may already have been emailed about."""
    mem, pr, _ = env
    fn = _one(mem)
    pr.record_review(P, _rec(mem, fn), tripped=True, finding='they shipped it')
    assert len(pr.open_flags(P)) == 1
    contested = pr.record_review(P, _rec(mem, fn), tripped=False,
                                 finding='looks pulled now')
    assert contested['flagged'] is True, 'flag must stay open — a human has not acted'
    assert contested['contested'] is True
    assert len(pr.open_flags(P)) == 1

    # Re-confirming the trip is not news: the flag was never cleared, so the
    # original email already covers it. A naive "restore the flag" fix would
    # instead treat this as newly_flagged again and duplicate the email.
    reconfirmed = pr.record_review(P, _rec(mem, fn), tripped=True, finding='back')
    assert reconfirmed['newly_flagged'] is False
    assert reconfirmed['contested'] is False


def test_a_human_editing_the_position_is_what_actually_clears_a_flag(env):
    """Editing the reason changes `position_hash`, which is the one channel
    `should_flag` already treats as the human having acted — so a fresh
    tripped=False review of the NEW text is a real clear, not an erasure."""
    mem, pr, _ = env
    fn = _one(mem)
    pr.record_review(P, _rec(mem, fn), tripped=True, finding='they shipped it')
    assert len(pr.open_flags(P)) == 1
    _one(mem, reason='reconsidered after Ron looked at it: keeping ours because X')
    cleared = pr.record_review(P, _rec(mem, fn), tripped=False,
                               finding='confirmed no longer applies on the new text')
    assert cleared['flagged'] is False
    assert cleared['contested'] is False
    assert pr.open_flags(P) == []
    # ...and a later re-trip is news again.
    assert pr.record_review(P, _rec(mem, fn), tripped=True,
                            finding='back')['newly_flagged'] is True


def test_a_clean_review_never_flags(env):
    mem, pr, _ = env
    fn = _one(mem)
    out = pr.record_review(P, _rec(mem, fn), tripped=False, finding='still true')
    assert out['newly_flagged'] is False and out['flagged'] is False


# ── the invariants ───────────────────────────────────────────────────────────

def test_reviewing_never_touches_the_position_itself(env):
    """The whole safety argument. A reviewer that can edit a ruling is a
    reviewer that can grant itself a different set of instructions."""
    mem, pr, tmp = env
    fn = _one(mem)
    before = (tmp / fn).read_text(encoding='utf-8')
    pr.record_review(P, _rec(mem, fn), tripped=True, finding='anything')
    assert (tmp / fn).read_text(encoding='utf-8') == before
    assert mem.list_positions(P)[0]['reason'] == 'we built it ourselves'


def test_the_sidecar_is_json_and_stays_out_of_the_corpus(env):
    """`_mem_corpus` globs '*.md'; a '.json' beside the notes is inert. It must
    also never land in DATA_DIR, where load_projects() would read it as a
    malformed project and 500 both restart endpoints."""
    mem, pr, tmp = env
    fn = _one(mem)
    pr.record_review(P, _rec(mem, fn), finding='checked')
    assert (tmp / pr.REVIEW_STATE_FILE).is_file()
    assert pr.REVIEW_STATE_FILE.endswith('.json')
    hits = mem._memory_search(P, 'Obsidian checked review', 6)
    assert not any(pr.REVIEW_STATE_FILE in (h.get('file') or '') for h in hits)


def test_concurrent_reviewers_do_not_lose_each_others_state(env, monkeypatch):
    """MC-910 bug 2. `_write_state` used to rewrite the whole sidecar with no
    read-modify-write guard, so two reviewers racing (the exact MC-909
    double-dispatch scenario) could each read the file before either wrote,
    and the second write would silently drop the first reviewer's entry —
    this file has to be safe on its own rather than depend on MC-909 staying
    fixed. A sleep is injected inside the write step to force the window a
    real race would only sometimes hit; without the lock this reliably loses
    entries, with it every writer's update survives."""
    mem, pr, tmp = env
    files = [mem.write_position(P, subject=f'Subject {i}', verdict='declined',
                                reason=f'reason {i}', expires_when=f'trigger {i}')
             for i in range(6)]

    orig_write = pr._atomic_write_text

    def slow_write(path, text, **kw):
        time.sleep(0.02)
        return orig_write(path, text, **kw)

    monkeypatch.setattr(pr, '_atomic_write_text', slow_write)

    def go(fn):
        pr.record_review(P, _rec(mem, fn), tripped=True, finding=f'found for {fn}')

    threads = [threading.Thread(target=go, args=(fn,)) for fn in files]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    state = pr.read_state(P)
    for fn in files:
        assert fn in state, f'{fn} missing from sidecar — lost update under concurrency'
        assert state[fn]['finding'] == f'found for {fn}'
    assert len(state) == len(files)
    assert not (tmp / f'{pr.REVIEW_STATE_FILE}.lock').exists(), 'lock left held after use'


def test_corrupt_state_degrades_to_empty_rather_than_raising(env):
    mem, pr, tmp = env
    _one(mem)
    (tmp / pr.REVIEW_STATE_FILE).write_text('{not json', encoding='utf-8')
    assert pr.read_state(P) == {}
    assert len(pr.positions_due(P)) == 1


def test_a_project_with_no_memory_path_says_so(env, monkeypatch):
    mem, pr, _ = env
    monkeypatch.setattr(mem, '_get_memory_path', lambda p: None)
    with pytest.raises(ValueError, match='memory path'):
        pr._state_path(P)
