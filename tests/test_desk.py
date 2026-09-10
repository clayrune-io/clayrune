"""The Desk state model (mc/desk.py) — the four stores a queue is not.

What these tests are actually guarding, in order of how much it would cost to
lose it:

  1. The signal feed is append-only and a bad line costs one line, not the feed.
     This project has already lost ~3 weeks of steward findings to a store that
     dropped data silently (CLAUDE.md, 2026-08-15); the feed must never repeat it.
  2. `record_edit` learns from a human edit. The 2026-09-09 field scan could not
     verify a closed learning loop in ANY surveyed product — this is the
     differentiator, so it gets a test that a cosmetic edit teaches nothing and
     a real one teaches something.
  3. `similar_published` catches a re-announcement. Without it an autonomous
     writer re-announces the same feature every month, in front of the exact
     B2B founder-credibility audience the scan says punishes that hardest.
  4. Nothing in the module publishes. `record_published` records a human's
     release; if a future edit gives this module an outbound network call, the
     approval gate has stopped being structural.

Determinism: patches `mc.desk.STORE_PATH` and `mc.desk.SIGNALS_PATH` to tmp_path,
mirroring tests/test_automation_suggestions.py.
"""
import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc import desk  # noqa: E402


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(desk, 'STORE_PATH', tmp_path / 'desk.json')
    monkeypatch.setattr(desk, 'SIGNALS_PATH', tmp_path / 'desk_signals.jsonl')
    return desk


# -- signal feed --------------------------------------------------------------

def test_signal_appends_and_reads_back(store):
    store.append_signal('mission_control', 'release', 'Shipped drag-to-hire')
    store.append_signal('apex_trader', 'commit', 'bump deps')
    rows = store.list_signals()
    assert len(rows) == 2
    assert {r['project_id'] for r in rows} == {'mission_control', 'apex_trader'}


def test_signal_feed_is_append_only_on_disk(store):
    store.append_signal('p', 'note', 'one')
    store.append_signal('p', 'note', 'two')
    lines = store.SIGNALS_PATH.read_text(encoding='utf-8').strip().splitlines()
    assert len(lines) == 2, 'each signal is its own line; nothing rewrites the file'
    assert json.loads(lines[0])['summary'] == 'one'


def test_one_corrupt_line_does_not_cost_the_feed(store):
    store.append_signal('p', 'note', 'good one')
    with store.SIGNALS_PATH.open('a', encoding='utf-8') as fh:
        fh.write('{not json at all\n')
    store.append_signal('p', 'note', 'good two')
    rows = store.list_signals()
    assert len(rows) == 2, 'a bad line is skipped, the rest survive'


def test_consumption_is_a_later_line_not_a_rewrite(store):
    sig = store.append_signal('p', 'release', 'Shipped the Desk')
    before = store.SIGNALS_PATH.read_text(encoding='utf-8')
    store.mark_signal_consumed(sig['id'], 'draft-123')

    assert store.SIGNALS_PATH.read_text(encoding='utf-8').startswith(before), \
        'marking consumed must APPEND, never rewrite the original entry'
    rows = store.list_signals()
    assert rows[0]['consumed_by'] == 'draft-123'
    assert store.list_signals(unconsumed_only=True) == []


def test_score_prefers_shipped_over_chore(store):
    shipped = store.score_signal('release', 'Shipped drag-to-hire, now live')
    chore = store.score_signal('commit', 'chore: bump dependency, lint fixes')
    assert shipped > chore
    assert 0.0 <= chore <= 1.0 and 0.0 <= shipped <= 1.0
    assert shipped >= store.STORY_SCORE_FLOOR
    assert chore < store.STORY_SCORE_FLOOR


def test_min_score_filter(store):
    store.append_signal('p', 'release', 'Shipped a thing users can feel')
    store.append_signal('p', 'commit', 'chore: whitespace')
    hot = store.list_signals(min_score=store.STORY_SCORE_FLOOR)
    assert len(hot) == 1
    assert 'Shipped' in hot[0]['summary']


def test_no_signals_path_does_not_explode(monkeypatch):
    monkeypatch.setattr(desk, 'SIGNALS_PATH', None)
    entry = desk.append_signal('p', 'note', 'unwired')
    assert entry['summary'] == 'unwired'
    assert desk.list_signals() == []


# -- voice profiles -----------------------------------------------------------

def test_two_voices_and_only_two(store):
    assert store.VOICES == ('ron', 'clayrune')
    assert {v['name'] for v in store.list_voices()} == {'ron', 'clayrune'}
    with pytest.raises(ValueError):
        store.get_voice('marketing')


def test_update_voice_ignores_unknown_fields(store):
    v = store.update_voice('ron', {
        'register': 'first person, a builder',
        'banned': ['leverage', 'game-changing'],
        'rewrites': ['SHOULD BE IGNORED'],
    })
    assert v['register'] == 'first person, a builder'
    assert v['banned'] == ['leverage', 'game-changing']
    assert v['rewrites'] == [], 'rewrites are earned by record_edit, not settable'


def test_record_edit_learns_from_a_real_edit(store):
    r = store.record_edit(
        'ron',
        before='Excited to announce our game-changing new feature!',
        after='Shipped drag-to-hire. Took three days and two rewrites.',
        draft_id='d-1')
    assert r is not None
    assert r['draft_id'] == 'd-1'
    assert store.get_voice('ron')['rewrites'][-1]['after'].startswith('Shipped')


def test_cosmetic_edit_teaches_nothing(store):
    assert store.record_edit('ron', before='Shipped it.', after='Shipped it.') is None
    assert store.record_edit('ron', before='', after='something') is None
    # A single trailing character is noise, not voice.
    assert store.record_edit(
        'ron',
        before='Shipped drag-to-hire today and it took three days of rework',
        after='Shipped drag-to-hire today and it took three days of rework.') is None
    assert store.get_voice('ron')['rewrites'] == []


def test_voice_brief_carries_the_actual_rewrites(store):
    store.update_voice('ron', {'register': 'first person', 'banned': ['leverage']})
    store.record_edit('ron',
                      before='We are thrilled to leverage synergies',
                      after='I rewrote the scheduler. It was slower than the old one.')
    brief = store.voice_brief('ron')
    assert 'VOICE: ron' in brief
    assert 'leverage' in brief
    assert 'I rewrote the scheduler' in brief, \
        'the brief must carry verbatim rewrites, not a summary of them'


def test_voice_brief_shows_only_recent_rewrites(store):
    for i in range(20):
        store.record_edit('ron', before=f'before number {i} here',
                          after=f'after number {i} entirely different text')
    brief = store.voice_brief('ron', recent=3)
    assert 'after number 19' in brief
    assert 'after number 5' not in brief


# -- campaigns ----------------------------------------------------------------

def test_campaign_lifecycle(store):
    c = store.create_campaign('Agent persistence', 'Clayrune keeps agents alive',
                              voice='clayrune', agenda='launch window')
    assert c['state'] == 'proposed'
    assert store.update_campaign(c['id'], {'state': 'running'})['state'] == 'running'
    assert len(store.list_campaigns(state='running')) == 1
    assert store.delete_campaign(c['id']) is True
    assert store.list_campaigns() == []


def test_campaign_rejects_bad_state_and_voice(store):
    c = store.create_campaign('t', 'thesis')
    with pytest.raises(ValueError):
        store.update_campaign(c['id'], {'state': 'launched'})
    with pytest.raises(ValueError):
        store.create_campaign('t', 'thesis', voice='marketing')


def test_update_missing_campaign_returns_none(store):
    assert store.update_campaign('camp-nope', {'state': 'running'}) is None
    assert store.delete_campaign('camp-nope') is False


# -- ledger + repetition ------------------------------------------------------

def test_ledger_records_and_lists(store):
    store.record_published(platform='x', voice='ron', body='Shipped the Desk',
                           project_id='mission_control')
    rows = store.list_ledger()
    assert len(rows) == 1
    assert rows[0]['outcome'] is None
    assert store.list_ledger(platform='linkedin') == []


def test_similar_published_catches_a_re_announcement(store):
    store.record_published(
        platform='x', voice='ron',
        body='Shipped drag-to-hire today: grab an agent off the Floor '
             'and drop it on a project to hire it.')
    hits = store.similar_published(
        'Shipped drag-to-hire today: grab an agent off the Floor '
        'and drop it onto a project to hire it.')
    assert hits, 'a near-identical repost must be caught'
    assert hits[0]['overlap'] > 0.35
    assert store.already_said('Shipped drag-to-hire today: grab an agent off '
                              'the Floor and drop it on a project to hire it.')


def test_a_different_post_is_not_flagged(store):
    store.record_published(platform='x', voice='ron',
                           body='Shipped drag-to-hire today, it took three days')
    assert not store.already_said(
        'The backup restore points now keep ten snapshots plus anything pinned')


def test_empty_body_is_never_a_repeat(store):
    store.record_published(platform='x', voice='ron', body='anything at all here')
    assert store.similar_published('') == []


def test_record_outcome(store):
    p = store.record_published(platform='x', voice='ron', body='hello world post')
    assert store.record_outcome(p['id'], {'likes': 4})['outcome'] == {'likes': 4}
    assert store.record_outcome('post-nope', {'likes': 1}) is None


# -- the structural guarantee -------------------------------------------------

def test_module_has_no_publish_path():
    """The approval gate is a platform TERM, not our caution.

    Pinterest requires per-item human choice, YouTube requires express consent,
    and Postiz's own agent docs ask for a human in the loop. If this module ever
    grows an outbound call, the gate has stopped being structural — exactly the
    Meta March-2026 failure, where human-in-the-loop was *expected* but not
    *enforced* and the agent posted anyway.
    """
    src = (PROJECT_ROOT / 'mc' / 'desk.py').read_text(encoding='utf-8')
    for forbidden in ('requests.', 'urllib.request', 'httpx.', 'socket.',
                      'subprocess.'):
        assert forbidden not in src, f'{forbidden} has no business in the Desk store'


def test_store_is_not_inside_data_projects(store):
    """A stray *.json under DATA_DIR becomes a malformed project and 500s both
    restart endpoints (the LOAD-BEARING rule in CLAUDE.md)."""
    store.append_signal('p', 'note', 'x')
    store.create_campaign('t', 'thesis')
    for path in (store.STORE_PATH, store.SIGNALS_PATH):
        assert path.parent.name != 'projects', \
            'Desk state is a SIBLING of DATA_DIR, never a member'


def test_corrupt_store_degrades_to_empty_not_to_crash(store):
    store.create_campaign('t', 'thesis')
    store.STORE_PATH.write_text('{ broken json', encoding='utf-8')
    assert store.list_campaigns() == []
    # And a write after corruption still works rather than raising.
    assert store.create_campaign('t2', 'thesis2')['title'] == 't2'
