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

def test_a_fresh_install_seeds_neutral_voices(store):
    """VOICES USED TO BE A HARDCODED TUPLE containing one operator's name, in a
    file that ships to strangers — the "nothing operator-specific goes in the
    repo" rule in CLAUDE.md. A fresh install now gets ROLES, not names."""
    assert {v['name'] for v in store.list_voices()} == {'personal', 'product'}
    with pytest.raises(ValueError):
        store.get_voice('marketing')


def test_no_operator_name_ships_in_the_source():
    """The rule made enforceable. If someone hardcodes a person's handle as a
    voice again, this fails rather than shipping to every other install."""
    for mod in ('mc/desk.py', 'mc/desk_brief.py', 'mc/blueprints/desk_routes.py'):
        src = (PROJECT_ROOT / mod).read_text(encoding='utf-8')
        code = '\n'.join(ln for ln in src.splitlines()
                         if not ln.lstrip().startswith('#'))
        assert "'ron'" not in code and '"ron"' not in code, f'{mod} names an operator'
        assert "'clayrune'" not in code, f'{mod} hardcodes a brand as a voice'


def test_no_account_identifier_ships_in_the_source():
    """Mirrors test_no_operator_name_ships_in_the_source: a destination is which
    ACCOUNT a voice publishes to, and it is Ron's to type, never ours to seed.
    No profile URL, handle, or page id may ship as a default."""
    for mod in ('mc/desk.py', 'mc/desk_brief.py', 'mc/blueprints/desk_routes.py'):
        src = (PROJECT_ROOT / mod).read_text(encoding='utf-8')
        code = '\n'.join(ln for ln in src.splitlines()
                         if not ln.lstrip().startswith('#'))
        for needle in ('linkedin.com/in/', 'linkedin.com/company/', 'x.com/i/',
                       '@RanLevi15', 'leviran1@gmail.com'):
            assert needle not in code, f'{mod} hardcodes an account identifier {needle!r}'
    assert desk._empty_voice('probe')['destination'] == '', \
        'the default destination must be blank, not a seeded account'


def test_the_two_starter_voices_hold_different_standing(store):
    """The pair is not decoration: they own different platforms, which is why a
    story gets written twice rather than cross-posted."""
    by_name = {v['name']: v for v in store.list_voices()}
    assert by_name['personal']['platform'] != by_name['product']['platform']


def test_deleting_the_starters_does_not_resurrect_them(store):
    """Someone who deletes the starters and names their own keeps that choice.

    Seeding is gated on a FLAG, not on "the voices dict is empty" — the latter
    cannot tell a fresh install from a user who cleared it on purpose, and
    re-seeded on the next read, which is a store overruling a human.
    """
    assert store.voice_names()  # materialise the seed first
    store.delete_voice('personal')
    store.delete_voice('product')
    store.create_voice('mine', platform='x')
    assert [v['name'] for v in store.list_voices()] == ['mine']
    assert store.voice_names() == ['mine'], 'a second read must not re-seed'


def test_a_user_can_add_and_remove_a_voice(store):
    v = store.create_voice('newsletter', platform='linkedin', register='plain')
    assert v['platform'] == 'linkedin'
    assert 'newsletter' in store.voice_names()
    assert store.delete_voice('newsletter') is True
    assert 'newsletter' not in store.voice_names()
    assert store.delete_voice('newsletter') is False


def test_a_voice_can_be_given_a_destination(store):
    """The account this voice publishes to — distinct from its platform, so two
    voices on the same platform (two LinkedIn voices) can still resolve to
    different accounts."""
    v = store.create_voice('newsletter', platform='linkedin', destination='page-a')
    assert v['destination'] == 'page-a'
    v = store.update_voice('newsletter', {'destination': 'page-b'})
    assert v['destination'] == 'page-b'


def test_a_voice_with_no_destination_defaults_to_empty(store):
    """Empty means 'the platform's default account' — an existing install that
    never set this must keep working unchanged."""
    v = store.create_voice('newsletter', platform='linkedin')
    assert v['destination'] == ''


def test_voice_names_are_validated(store):
    for bad in ('', 'Has Caps', 'has space', 'x' * 33, 'bad!char'):
        with pytest.raises(ValueError):
            store.create_voice(bad)
    with pytest.raises(ValueError):
        store.create_voice('personal')  # already exists


def test_a_voice_can_be_scoped_to_one_project(store):
    """Ron asked whether a voice could be per-project. It can."""
    store.create_voice('sidegig', platform='x', scope='other_project')
    assert 'sidegig' not in store.voice_names('mission_control')
    assert 'sidegig' in store.voice_names('other_project')
    assert 'personal' in store.voice_names('other_project'), 'globals stay available'


def test_there_is_no_hardcoded_default_voice(store):
    assert store.default_voice() in ('personal', 'product')
    store.delete_voice('personal')
    store.delete_voice('product')
    assert store.default_voice() is None, 'no literal fallback name'


def test_update_voice_ignores_unknown_fields(store):
    v = store.update_voice('personal', {
        'register': 'first person, a builder',
        'banned': ['leverage', 'game-changing'],
        'rewrites': ['SHOULD BE IGNORED'],
    })
    assert v['register'] == 'first person, a builder'
    assert v['banned'] == ['leverage', 'game-changing']
    assert v['rewrites'] == [], 'rewrites are earned by record_edit, not settable'


def test_record_edit_learns_from_a_real_edit(store):
    r = store.record_edit(
        'personal',
        before='Excited to announce our game-changing new feature!',
        after='Shipped drag-to-hire. Took three days and two rewrites.',
        draft_id='d-1')
    assert r is not None
    assert r['draft_id'] == 'd-1'
    assert store.get_voice('personal')['rewrites'][-1]['after'].startswith('Shipped')


def test_cosmetic_edit_teaches_nothing(store):
    assert store.record_edit('personal', before='Shipped it.', after='Shipped it.') is None
    assert store.record_edit('personal', before='', after='something') is None
    # A single trailing character is noise, not voice.
    assert store.record_edit(
        'personal',
        before='Shipped drag-to-hire today and it took three days of rework',
        after='Shipped drag-to-hire today and it took three days of rework.') is None
    assert store.get_voice('personal')['rewrites'] == []


def test_voice_brief_carries_the_actual_rewrites(store):
    store.update_voice('personal', {'register': 'first person', 'banned': ['leverage']})
    store.record_edit('personal',
                      before='We are thrilled to leverage synergies',
                      after='I rewrote the scheduler. It was slower than the old one.')
    brief = store.voice_brief('personal')
    assert 'VOICE: personal' in brief
    assert 'leverage' in brief
    assert 'I rewrote the scheduler' in brief, \
        'the brief must carry verbatim rewrites, not a summary of them'


def test_voice_brief_shows_only_recent_rewrites(store):
    for i in range(20):
        store.record_edit('personal', before=f'before number {i} here',
                          after=f'after number {i} entirely different text')
    brief = store.voice_brief('personal', recent=3)
    assert 'after number 19' in brief
    assert 'after number 5' not in brief


# -- platform rules -------------------------------------------------------------
#
# `PLATFORM_NOTES` used to be a hardcoded dict with exactly two keys, and every
# platform outside them got `desk_brief`'s `.get(platform, '')` — an empty
# string where the char limit and cost should have been. Measured 2026-09-10:
# an 837-char facebook draft and a 2236-char discord draft, both briefed with
# nothing, because nobody could reach the rules to set them.

def test_a_fresh_install_seeds_only_x_and_linkedin(store):
    assert {r['name'] for r in store.list_platform_rules()} == {'x', 'linkedin'}
    assert store.get_platform_rules('facebook') is None, \
        'an unseeded platform has no rules — that is the truth, not a bug'


def test_x_rules_carry_the_verified_char_limit_and_cost(store):
    rules = store.get_platform_rules('x')
    assert rules['char_limit'] == 280
    assert '$0.015' in rules['text'] and '$0.200' in rules['text']


def test_deleting_a_seeded_platforms_rules_does_not_resurrect_it(store):
    """Same seeded-flag trick as the voices: emptiness must not be read as
    'fresh install' or a deliberate deletion comes back on the next read."""
    store.delete_platform_rules('x')
    assert store.get_platform_rules('x') is None
    assert 'x' not in {r['name'] for r in store.list_platform_rules()}


def test_setting_rules_for_a_new_platform_creates_it(store):
    """The whole point: Ron can set rules for a platform we never shipped
    seed text for, and it sticks."""
    assert store.get_platform_rules('facebook') is None
    store.update_platform_rules('facebook', {'text': 'keep it under 500 chars', 'char_limit': 500})
    rules = store.get_platform_rules('facebook')
    assert rules['char_limit'] == 500
    assert 'keep it under 500 chars' in rules['text']


def test_platform_rules_reject_a_malformed_name(store):
    with pytest.raises(ValueError):
        store.update_platform_rules('Not Valid!', {'text': 'x'})


def test_no_operator_platform_rule_text_ships_in_the_source():
    """Mirrors test_no_operator_name_ships_in_the_source: the only platform
    rule TEXT allowed in the repo is the verified x/linkedin seed. A rule for
    facebook or discord is Ron's to type into the UI, never ours to commit."""
    src = (PROJECT_ROOT / 'mc/desk.py').read_text(encoding='utf-8')
    for name in ('facebook', 'discord', 'reddit', 'instagram', 'threads', 'bluesky'):
        assert f"'{name}'" not in src and f'"{name}"' not in src, \
            f'mc/desk.py hardcodes rule text for {name!r}, which is Ron\'s to author'


# -- campaigns ----------------------------------------------------------------

def test_campaign_lifecycle(store):
    c = store.create_campaign('Agent persistence', 'Clayrune keeps agents alive',
                              voice='product', agenda='launch window')
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


def test_campaign_visual_defaults_to_a_real_screenshot_and_can_be_overridden(store):
    """A campaign without an explicit visual still carries one (a media-blank
    draft is the dead-`media`-field bug this feature exists to close), and an
    explicit request overrides the default rather than being ignored.
    """
    c = store.create_campaign('t', 'thesis')
    assert c['visual'] == store.DEFAULT_VISUAL_REQUIREMENT
    assert 'screenshot' in c['visual']

    c2 = store.create_campaign('t2', 'thesis2', visual='a chart of weekly signups')
    assert c2['visual'] == 'a chart of weekly signups'

    updated = store.update_campaign(c['id'], {'visual': 'a before/after diff screenshot'})
    assert updated['visual'] == 'a before/after diff screenshot'


# -- ledger + repetition ------------------------------------------------------

def test_ledger_records_and_lists(store):
    store.record_published(platform='x', voice='personal', body='Shipped the Desk',
                           project_id='mission_control')
    rows = store.list_ledger()
    assert len(rows) == 1
    assert rows[0]['outcome'] is None
    assert store.list_ledger(platform='linkedin') == []


def test_similar_published_catches_a_re_announcement(store):
    store.record_published(
        platform='x', voice='personal',
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
    store.record_published(platform='x', voice='personal',
                           body='Shipped drag-to-hire today, it took three days')
    assert not store.already_said(
        'The backup restore points now keep ten snapshots plus anything pinned')


def test_empty_body_is_never_a_repeat(store):
    store.record_published(platform='x', voice='personal', body='anything at all here')
    assert store.similar_published('') == []


def test_record_outcome(store):
    p = store.record_published(platform='x', voice='personal', body='hello world post')
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


# -- a campaign is an argument, and carries the voices that carry it ----------

def test_a_campaign_can_run_in_more_than_one_voice(store):
    """Ron's question: should a campaign set the platform? No — but a campaign
    with only ONE voice can only ever reach one room, and a thesis usually
    deserves both. So the campaign holds the argument and a SET of voices."""
    c = store.create_campaign('Agent persistence', 'Clayrune keeps agents alive',
                              voices=['personal', 'product'])
    assert c['voices'] == ['personal', 'product']
    assert set(store.campaign_platforms(c)) == {'x', 'linkedin'}


def test_platform_is_derived_from_the_voice_never_stored(store):
    """Storing both would let them disagree, and a first-person post in the
    product's voice on the wrong network is the incoherence the split prevents."""
    c = store.create_campaign('t', 'th', voices=['product'])
    assert 'platform' not in c
    assert store.campaign_platforms(c) == ['linkedin']


def test_a_single_voice_still_works_and_is_normalised(store):
    c = store.create_campaign('t', 'th', voice='personal')
    assert c['voices'] == ['personal']
    assert c['voice'] == 'personal', 'the singular field stays in sync'


def test_duplicate_voices_collapse(store):
    c = store.create_campaign('t', 'th', voices=['personal', 'personal'])
    assert c['voices'] == ['personal']


def test_a_campaign_rejects_an_unknown_voice_in_the_set(store):
    with pytest.raises(ValueError):
        store.create_campaign('t', 'th', voices=['personal', 'nope'])


def test_voices_can_be_changed_after_the_fact(store):
    c = store.create_campaign('t', 'th', voices=['personal'])
    up = store.update_campaign(c['id'], {'voices': ['product']})
    assert up['voices'] == ['product'] and up['voice'] == 'product'
    with pytest.raises(ValueError):
        store.update_campaign(c['id'], {'voices': []})
