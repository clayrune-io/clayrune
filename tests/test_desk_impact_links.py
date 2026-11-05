"""MC-1152 P2: UTM link generation, identity and reverse lookup, dark.

Spec: docs/desk_v1/CHANNEL_IMPACT_SPEC.md section 6 and section 12 row P2.
No network, no real clock, no Desk store: campaigns come from the P1 fakes.
"""
from __future__ import annotations

import copy
import threading
from urllib.parse import parse_qsl, urlsplit

import pytest

from mc import desk_impact_schema as S
from mc.desk_impact_channels import CHANNEL_UTM
from mc.desk_impact_links import (
    LinkRefused, TrackedLinks, build_final_url, link_id_for,
)
from mc.desk_impact_sources.registry import AdapterRegistry
from mc.desk_impact_store import ImpactStore, NotFound
from tests.desk_impact_fakes import CAMPAIGNS, CONNECTIONS, FakeSource, spec

PID, CID = 'proj1', 'camp-a'
DEST = 'https://example.com/landing?ref=keep&a=1#top'


@pytest.fixture
def campaigns():
    c = copy.deepcopy(CAMPAIGNS)
    c['camp-a']['plan']['accounts'] = [{'id': 'acc-1', 'platform': 'x'},
                                       {'channel_id': 'acc-2'}, 'acc-3']
    return c


@pytest.fixture
def clock():
    box: dict = {'t': '2026-11-04T12:00:00Z'}
    box['now'] = lambda: box['t']
    return box


@pytest.fixture
def st(tmp_path, campaigns):
    reg = AdapterRegistry()
    reg.register(FakeSource())
    return ImpactStore(tmp_path / 'data' / 'desk_impact',
                       campaign_resolver=lambda cid: copy.deepcopy(campaigns.get(cid)),
                       connection_resolver=lambda c: copy.deepcopy(CONNECTIONS.get(c)),
                       registry=reg, data_dir=tmp_path / 'data' / 'projects')


@pytest.fixture
def links(st, clock):
    return TrackedLinks(st, clock=clock['now'])


def ids(**over):
    base = dict(account_id='acc-1', platform='x', post_id='post-1', version_id='ver-1',
                destination=DEST)
    base.update(over)
    return base


def query_of(url):
    return urlsplit(url).query


# -- URL work ------------------------------------------------------------------------

UTM = {'utm_source': 'x', 'utm_medium': 'social', 'utm_campaign': 'camp-a',
       'utm_content': 'imlk-0123456789abcdef'}
TAGS = 'utm_source=x&utm_medium=social&utm_campaign=camp-a&utm_content=imlk-0123456789abcdef'


@pytest.mark.parametrize('dest, expected', [
    ('https://a.example/p', 'https://a.example/p?' + TAGS),
    ('https://a.example/p#frag', 'https://a.example/p?' + TAGS + '#frag'),
    ('https://a.example/p?x=1', 'https://a.example/p?x=1&' + TAGS),
    ('https://a.example/p?x=1#frag', 'https://a.example/p?x=1&' + TAGS + '#frag'),
    # order, repeated keys, encodings and a blank value survive untouched
    ('https://a.example/p?b=2&a=1&a=%20z+y&c=', 'https://a.example/p?b=2&a=1&a=%20z+y&c=&' + TAGS),
    # a trailing `?` or `&` does not produce `?&` / `&&`
    ('https://a.example/p?', 'https://a.example/p?' + TAGS),
    ('https://a.example/p?x=1&', 'https://a.example/p?x=1&' + TAGS),
    # a hash-routed app keeps its own `?` inside the fragment
    ('https://a.example/#/route?z=9', 'https://a.example/?' + TAGS + '#/route?z=9'),
    ('https://a.example:8443/p/q.html?x=1#', 'https://a.example:8443/p/q.html?x=1&' + TAGS + '#'),
    # utm_term and unrelated utm-like keys are not ours: kept, not counted as conflicts
    ('https://a.example/p?utm_term=kw&utm_id=7', 'https://a.example/p?utm_term=kw&utm_id=7&' + TAGS),
])
def test_query_and_fragment_are_preserved_byte_for_byte(dest, expected):
    assert build_final_url(dest, UTM) == expected


def test_a_legacy_ref_param_the_user_typed_is_kept_and_none_is_added():
    out = build_final_url('https://a.example/p?ref=desk-abc12345', UTM)
    assert query_of(out).startswith('ref=desk-abc12345&')
    assert query_of(out).count('ref=') == 1


@pytest.mark.parametrize('dest, keys', [
    ('https://a.example/p?utm_source=newsletter', ('utm_source',)),
    ('https://a.example/p?utm_medium=cpc&utm_campaign=spring', ('utm_campaign', 'utm_medium')),
    ('https://a.example/p?utm_content=hero', ('utm_content',)),
    ('https://a.example/p?utm_source=x&utm_source=y', ('utm_source',)),       # repeated tag
    ('https://a.example/p?UTM_Source=x', ('utm_source',)),                    # other spelling
    ('https://a.example/p?utm_source=', ('utm_source',)),                     # blank value
])
def test_conflicting_tags_are_refused_and_named_never_overwritten(dest, keys):
    with pytest.raises(LinkRefused) as e:
        build_final_url(dest, UTM)
    assert e.value.code == 'conflicting_tags'
    assert e.value.keys == keys
    # the message names the parameters, never the other party's value
    for leaked in ('newsletter', 'cpc', 'spring', 'hero'):
        assert leaked not in str(e.value)


def test_an_identical_tag_already_on_the_url_is_kept_not_duplicated():
    out = build_final_url('https://a.example/p?utm_source=x&k=1', UTM)
    pairs = parse_qsl(query_of(out), keep_blank_values=True)
    assert [v for k, v in pairs if k == 'utm_source'] == ['x']
    assert dict(pairs)['utm_content'] == 'imlk-0123456789abcdef'


@pytest.mark.parametrize('dest', [
    'https://bucket.s3.amazonaws.com/f.pdf?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Expires=300',
    'https://cdn.example/f?Expires=1893456000&Signature=abc&Key-Pair-Id=K1',
    'https://a.example/f?expires=1893456000',
    'https://a.example/f?policy=eyJ',
    'https://a.example/f?sig=zzz',
])
def test_signed_urls_are_refused_as_untaggable(dest):
    with pytest.raises(LinkRefused) as e:
        build_final_url(dest, UTM)
    assert e.value.code == 'signed_url' and e.value.keys


@pytest.mark.parametrize('dest', [
    'ftp://a.example/p', 'javascript:alert(1)', 'https://u:p@a.example/p',
    'https://a.example/p?token=abc', 'https://a.example/p with space', '', None, 5,
    'https://a.example/p?q=%',
])
def test_destinations_that_are_not_public_urls_are_refused(dest):
    if dest == 'https://a.example/p?q=%':
        # a stray % is legal in a query string; this one is fine and must pass
        assert build_final_url(dest, UTM).startswith(dest)
        return
    with pytest.raises(LinkRefused) as e:
        build_final_url(dest, UTM)
    assert e.value.code == 'invalid_destination'


# -- identity --------------------------------------------------------------------------

def test_link_id_is_a_pure_function_of_the_stable_ids():
    a = link_id_for(PID, CID, 'acc-1', 'post-1', 'ver-1', DEST)
    assert a == link_id_for(PID, CID, 'acc-1', 'post-1', 'ver-1', DEST)
    assert a.startswith('imlk-') and S.valid_id(a)
    others = {
        link_id_for('proj2', CID, 'acc-1', 'post-1', 'ver-1', DEST),
        link_id_for(PID, 'camp-b', 'acc-1', 'post-1', 'ver-1', DEST),
        link_id_for(PID, CID, 'acc-2', 'post-1', 'ver-1', DEST),
        link_id_for(PID, CID, 'acc-1', 'post-2', 'ver-1', DEST),
        link_id_for(PID, CID, 'acc-1', 'post-1', 'ver-2', DEST),
        link_id_for(PID, CID, 'acc-1', 'post-1', 'ver-1', DEST + '&b=2'),
    }
    assert a not in others and len(others) == 6


def test_reopening_returns_the_same_identity_and_the_same_url(links, st, clock):
    first = links.issue(PID, CID, **ids())
    assert first['created'] is True
    clock['t'] = '2027-01-01T00:00:00Z'                       # a later session
    again = links.issue(PID, CID, **ids())
    assert again['created'] is False
    assert again['link'] == first['link']                      # created_at frozen too
    assert again['url'] == first['url']
    assert len(st.list_links(PID, CID)) == 1


def test_renaming_the_post_or_the_account_cannot_move_the_identity(links, st, campaigns):
    before = links.issue(PID, CID, **ids())['url']
    campaigns['camp-a']['title'] = 'Renamed campaign'
    campaigns['camp-a']['plan']['accounts'][0]['label'] = 'A brand new account name'
    after = links.preview(PID, CID, **ids())
    assert after['url'] == before and after['issued'] is True


def test_the_channel_table_cannot_move_a_link_that_was_already_issued(st, clock):
    one = TrackedLinks(st, channels={'x': ('x', 'social')}, clock=clock['now'])
    issued = one.issue(PID, CID, **ids())
    renamed = TrackedLinks(st, channels={'x': ('twitter', 'social')}, clock=clock['now'])
    assert renamed.issue(PID, CID, **ids())['url'] == issued['url']
    assert 'utm_source=x&' in issued['url']


def test_versions_accounts_and_destinations_get_distinct_ids(links):
    seen = {}
    for label, over in {
        'base': {}, 'other_version': dict(version_id='ver-2'),
        'other_account': dict(account_id='acc-2'),
        'other_post': dict(post_id='post-2'),
        'other_dest': dict(destination='https://example.com/other'),
        'other_platform_account': dict(account_id='acc-3', platform='linkedin'),
    }.items():
        out = links.issue(PID, CID, **ids(**over))
        seen[label] = out['link']
    content = {r['utm']['utm_content'] for r in seen.values()}
    assert len(content) == len(seen)
    assert all(r['utm']['utm_campaign'] == CID for r in seen.values())
    assert seen['other_platform_account']['utm']['utm_source'] == 'linkedin'
    # two accounts on one platform share a source and are told apart by the link id
    assert seen['base']['utm']['utm_source'] == seen['other_account']['utm']['utm_source']


def test_preview_writes_nothing_and_matches_issue(links, st, tmp_path):
    pre = links.preview(PID, CID, **ids())
    assert pre['issued'] is False
    assert st.list_links(PID, CID) == []
    assert not (tmp_path / 'data' / 'desk_impact' / PID / 'links.json').exists()
    post = links.issue(PID, CID, **ids())
    assert post['url'] == pre['url']


def test_the_stored_row_validates_and_never_emits_the_legacy_ref(links, st):
    out = links.issue(PID, CID, **ids(destination='https://example.com/x'))
    S.validate_link_record(out['link'])
    assert st.get_link(PID, out['link']['link_id']) == out['link']
    assert 'ref=desk' not in out['url'] and 'ref=' not in out['url']
    assert set(out['link']['utm']) == set(S.UTM_KEYS)


def test_concurrent_issues_of_one_identity_leave_one_row(links, st):
    results, errors = [], []

    def go():
        try:
            results.append(links.issue(PID, CID, **ids())['url'])
        except Exception as e:                                  # pragma: no cover
            errors.append(e)
    ts = [threading.Thread(target=go) for _ in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errors and len(set(results)) == 1
    assert len(st.list_links(PID, CID)) == 1


# -- refusals ----------------------------------------------------------------------------

def test_an_account_outside_the_campaign_plan_is_refused(links):
    with pytest.raises(LinkRefused) as e:
        links.issue(PID, CID, **ids(account_id='acc-9'))
    assert e.value.code == 'not_in_campaign'


@pytest.mark.parametrize('platform', ['blog', 'reddit', '', None, 7])
def test_a_platform_with_no_configured_source_gets_no_link(links, platform):
    with pytest.raises(LinkRefused) as e:
        links.issue(PID, CID, **ids(platform=platform))
    assert e.value.code == 'unknown_channel'


def test_a_campaign_of_another_project_is_not_found(links):
    with pytest.raises(NotFound):
        links.issue('proj2', CID, **ids())
    with pytest.raises(NotFound):
        links.issue(PID, 'camp-other', **ids())


@pytest.mark.parametrize('over', [dict(post_id='../x'), dict(version_id=''), dict(account_id='a/b'),
                                  dict(post_id=None)])
def test_unsafe_ids_are_refused_before_anything_is_stored(links, st, over):
    with pytest.raises(LinkRefused) as e:
        links.issue(PID, CID, **ids(**over))
    assert e.value.code == 'invalid_identity'
    assert st.list_links(PID, CID) == []


def test_conflicting_tags_through_the_service_store_nothing(links, st):
    with pytest.raises(LinkRefused) as e:
        links.issue(PID, CID, **ids(destination='https://example.com/p?utm_source=partner'))
    assert e.value.code == 'conflicting_tags'
    assert st.list_links(PID, CID) == []


def test_a_tagged_address_over_the_length_limit_is_refused_not_cut(links):
    long_dest = 'https://example.com/p?' + 'q=' + 'a' * 1990
    with pytest.raises(LinkRefused) as e:
        links.issue(PID, CID, **ids(destination=long_dest))
    assert e.value.code == 'too_long'


# -- coverage note -------------------------------------------------------------------------

def test_the_note_says_nothing_reads_the_tags_until_a_conversion_is_defined(links, st):
    pre = links.preview(PID, CID, **ids())
    assert 'no conversion defined' in pre['coverage_note']
    st.set_definition(PID, CID, spec(), expected_revision=0)
    post = links.preview(PID, CID, **ids())
    assert 'depends on its own analytics' in post['coverage_note']


# -- reverse lookup -------------------------------------------------------------------------

def test_lookup_resolves_a_url_back_to_its_post_version_and_account(links):
    out = links.issue(PID, CID, **ids())
    row = links.lookup_url(PID, out['url'])
    assert row and (row['post_id'], row['version_id'], row['account_id']) == ('post-1', 'ver-1', 'acc-1')


def test_lookup_survives_a_platform_that_reorders_and_adds_parameters(links):
    out = links.issue(PID, CID, **ids(destination='https://example.com/x'))
    qs = dict(parse_qsl(query_of(out['url'])))
    shuffled = 'https://example.com/x?fbclid=1&utm_content=%s&utm_campaign=%s&utm_medium=social&utm_source=x' % (
        qs['utm_content'], qs['utm_campaign'])
    assert links.lookup_url(PID, shuffled)['link_id'] == out['link']['link_id']


@pytest.mark.parametrize('mutate', [
    lambda u: u.replace('utm_campaign=camp-a', 'utm_campaign=camp-b'),
    lambda u: u.replace('utm_source=x', 'utm_source=linkedin'),
    lambda u: u.replace('utm_medium=social', 'utm_medium=email'),
    lambda u: u.replace('imlk-', 'imlk-0'),
])
def test_lookup_rejects_tags_that_disagree_with_the_manifest(links, mutate):
    out = links.issue(PID, CID, **ids(destination='https://example.com/x'))
    assert links.lookup_url(PID, mutate(out['url'])) is None


@pytest.mark.parametrize('url', [None, 5, '', 'https://example.com/x', 'https://example.com/x?utm_content=imlk-nope',
                                 'https://example.com/x?utm_content=../../etc'])
def test_lookup_of_anything_else_is_none_and_never_raises(links, url):
    assert links.lookup_url(PID, url) is None


def test_lookup_is_scoped_to_the_project(links):
    out = links.issue(PID, CID, **ids())
    assert links.lookup_url('proj2', out['url']) is None


def test_links_in_text_finds_each_desk_link_once_in_order(links):
    a = links.issue(PID, CID, **ids(destination='https://example.com/a'))
    b = links.issue(PID, CID, **ids(destination='https://example.com/b'))
    text = (f"Read {a['url']}, then ({b['url']}). Again {a['url']}! And https://example.com/plain "
            f"and https://example.com/z?utm_source=x")
    got = links.links_in_text(PID, text)
    assert [r['link_id'] for r in got] == [a['link']['link_id'], b['link']['link_id']]
    assert links.links_in_text(PID, None) == []


# -- the channel table ---------------------------------------------------------------------

def test_every_channel_row_is_a_valid_source_and_medium():
    assert 'blog' not in CHANNEL_UTM
    for source, medium in CHANNEL_UTM.values():
        S.validate_utm({'utm_source': source, 'utm_medium': medium,
                        'utm_campaign': 'c', 'utm_content': 'imlk-0123456789abcdef'})
        assert source == source.lower()
