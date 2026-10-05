"""Slice P1 of docs/DESK_SERVICE_PROFILES_SPEC.md: the service profile data, its loader and
its version 1 compatibility. Offline: nothing here opens a socket or touches ~/.clayrune."""
import copy
import json
import shutil
from pathlib import Path

import pytest

from mc.desk_connect import profile_compat as compat
from mc.desk_connect import profile_loader as loader
from mc.desk_connect import profile_schema as schema
from mc.desk_connect import registry

ROOT = Path(__file__).resolve().parent.parent
SHIPPED = ROOT / 'mc' / 'desk_connect'
V1_GOLD = json.loads((Path(__file__).parent / 'fixtures' / 'desk_registry_v1.json').read_text(encoding='utf-8'))
SHIPPED_IDS = ['x', 'higgsfield', 'google_ai', 'openai', 'notion', 'linkedin', 'youtube', 'google_drive', 'google_photos']


def _copy_shipped(tmp_path) -> Path:
    """A private copy of the shipped index and profiles to break one thing at a time."""
    shutil.copytree(SHIPPED / 'profiles', tmp_path / 'profiles')
    shutil.copy(SHIPPED / 'registry.json', tmp_path / 'registry.json')
    return tmp_path / 'registry.json'


def _edit(path: Path, fn):
    data = json.loads(path.read_text(encoding='utf-8'))
    fn(data)
    path.write_text(json.dumps(data), encoding='utf-8')


def _profile(sid):
    return json.loads((SHIPPED / 'profiles' / f'{sid}.json').read_text(encoding='utf-8'))


# ── the shipped data ──

def test_shipped_index_is_version_2_and_lists_every_v1_service():
    snap = loader.load_snapshot(SHIPPED / 'registry.json')
    assert snap['version'] == 2
    assert [p['service_id'] for p in snap['profiles']] == SHIPPED_IDS
    assert SHIPPED_IDS == [s['id'] for s in V1_GOLD['services']]
    assert {p.name for p in (SHIPPED / 'profiles').glob('*.json')} == {f'{i}.json' for i in SHIPPED_IDS}


def test_every_v1_row_maps_and_the_screens_see_the_old_records_unchanged():
    view = registry.v1_projection()
    assert view['blocked_domains'] == V1_GOLD['blocked_domains']
    assert view['common_options'] == V1_GOLD['common_options']
    assert view['services'] == V1_GOLD['services']       # same ids, hosts, aliases, titles, evidence text, `open`
    assert sum(len(s['options']) for s in V1_GOLD['services']) == 10


def test_a_v1_file_converts_in_memory_to_the_same_screens_data():
    snap = compat.snapshot_from_v1(V1_GOLD)
    assert snap['version'] == 1
    assert compat.v1_view(snap)['services'] == V1_GOLD['services']
    assert snap['common_options'] == V1_GOLD['common_options']


def test_registry_load_accepts_a_v1_file(tmp_path):
    p = tmp_path / 'old.json'
    p.write_text(json.dumps(V1_GOLD), encoding='utf-8')
    reg = registry.load(p)
    assert reg['version'] == 1
    assert [s['id'] for s in reg['services']] == SHIPPED_IDS
    assert reg['by_host']['x.com']['id'] == 'x'


def test_converted_rows_are_imports_with_unknown_dates_and_no_verification():
    snap = compat.snapshot_from_v1(V1_GOLD)
    for prof in snap['profiles']:
        for e in prof['evidence']:
            assert (e['result'], e['url'], e['published'], e['retrieved'], e['attempted'], e['digest']) == ('unverified', None, None, None, None, None)
            assert e['claim_ids'] == []
        for r in prof['routes']:
            assert r['verification'] == {'status': 'unverified', 'last_verified': None, 'last_attempted': None}


def test_imported_linkedin_does_not_become_available():
    snap = compat.snapshot_from_v1(V1_GOLD)
    li = snap['by_id']['linkedin']
    assert [r['support'] for r in li['routes']] == ['restricted']
    shipped = registry.profile('linkedin')
    assert all(r['support'] != 'available' for r in shipped['routes'])
    # and nothing the version 1 flow can select on any non-available row is selectable
    for s in V1_GOLD['services']:
        for o in s['options']:
            assert (o['support'] == 'available') == bool(any(
                r['support'] == 'available' and r.get('connect_method') == o['method'] for r in snap['by_id'][s['id']]['routes']))


def test_linkedin_org_posting_gate_is_still_closed():
    from mc import desk_accounts
    assert desk_accounts.LINKEDIN_ORG_POSTING_APPROVED is False


def test_generation_purposes_survive():
    for sid, want in (('higgsfield', {'generate_image', 'generate_video'}), ('google_ai', {'generate_image', 'generate_video'}),
                      ('openai', {'generate_image'})):
        prof = registry.profile(sid)
        assert {p['id'] for p in prof['purposes']} == want
        engines = [r for r in prof['routes'] if r.get('adapter_id')]
        assert engines and all(r['adapter_id'].startswith('engine:') and r['support'] == 'available' for r in engines)
        for p in prof['purposes']:
            assert set(p['routes']) <= {r['id'] for r in prof['routes']}
    pinned = {r['adapter_id'] for p in registry.registry()['snapshot']['profiles'] for r in p['routes'] if r.get('adapter_id')}
    assert pinned == {o['open'] for s in V1_GOLD['services'] for o in s['options'] if o.get('open')}


def test_linkedin_and_x_carry_every_route_with_cited_evidence():
    for sid, routes in (('linkedin', {'oauth', 'browser', 'manual', 'mcp'}), ('x', {'oauth', 'browser', 'manual', 'mcp'})):
        prof = registry.profile(sid)
        kinds = {r['id'].split('-', 1)[1].split('-')[0] for r in prof['routes']}
        assert routes <= kinds | {'member', 'action', 'docs'}, kinds
        assert {t for r in prof['routes'] for t in [r['transport']]} == {'api', 'browser', 'mcp'}
        assert any(r['support'] == 'manual' for r in prof['routes'])
        ev = {e['id']: e for e in prof['evidence']}
        verified = [e for e in ev.values() if e['result'] == 'verified']
        assert verified and all(e['url'].startswith('https://') and e['retrieved'] for e in verified)
        for r in prof['routes']:
            assert r['verification']['last_verified'] is None or r['verification']['status'] in ('verified', 'partial')
            for c in schema.route_claims(r):
                assert any(c in ev[i]['claim_ids'] for i in r['evidence'])


# ── recognition is exact ──

@pytest.mark.parametrize('host,sid', [('x.com', 'x'), ('twitter.com', 'x'), ('www.linkedin.com', 'linkedin'), ('linkedin.com', 'linkedin')])
def test_known_hosts_resolve(host, sid):
    got = registry.lookup(host)
    assert got and got['id'] == sid


@pytest.mark.parametrize('host', ['notx.com', 'x.com.evil.example', 'evil.example', 'xx.com', 'x.com.', 'sub.x.com', 'linkedin.com.evil.example', ''])
def test_look_alike_hosts_do_not_resolve(host):
    assert registry.lookup(host) is None


@pytest.mark.parametrize('text,sid', [('Higgsfield', 'higgsfield'), ('  HIGGSFIELD ', 'higgsfield'), ('google  ai-studio', 'google_ai'), ('LinkedIn', 'linkedin')])
def test_known_names_resolve(text, sid):
    got = registry.lookup_name(text)
    assert got and got['id'] == sid


@pytest.mark.parametrize('text', ['linkedin.com', 'x.com', 'linkedinn', 'xx', None, 7, ''])
def test_look_alike_names_do_not_resolve(text):
    assert registry.lookup_name(text) is None


# ── the loader rejects what it must, and never half-loads ──

def test_loader_rejects_duplicates_cross_references_and_paths(tmp_path):
    idx = _copy_shipped(tmp_path)
    prof = lambda sid: tmp_path / 'profiles' / f'{sid}.json'   # noqa: E731

    def broken(mutate_index=None, mutate_profile=None, sid='x', rename=None):
        d = tmp_path / 'case'
        if d.exists():
            shutil.rmtree(d)
        shutil.copytree(tmp_path, d, ignore=shutil.ignore_patterns('case'))
        if mutate_index:
            _edit(d / 'registry.json', mutate_index)
        if mutate_profile:
            _edit(d / 'profiles' / f'{sid}.json', mutate_profile)
        if rename:
            (d / 'profiles' / f'{rename[0]}.json').rename(d / 'profiles' / f'{rename[1]}.json')
        with pytest.raises(schema.ProfileError) as e:
            loader.load_snapshot(d / 'registry.json')
        return str(e.value)

    assert 'listed twice' in broken(lambda i: i['profiles'].append(dict(i['profiles'][0])))
    assert 'listed twice' in broken(lambda i: i['profiles'].append({'id': 'x2', 'file': i['profiles'][0]['file']}))
    for bad in ('../x.json', 'profiles/../x.json', 'profiles/sub/x.json', '/etc/passwd', 'profiles\\x.json', 'C:/x.json', 'profiles/x.json '):
        assert 'file must be exactly' in broken(lambda i, b=bad: i['profiles'][0].update(file=b)), bad
    assert 'does not exist' in broken(lambda i: i['profiles'][0].update(id='ghost', file='profiles/ghost.json'))
    assert 'not listed in the index' in broken(lambda i: i['profiles'].pop())
    assert 'is not the entry id' in broken(mutate_profile=lambda p: p.update(service_id='linkedin'))
    assert 'claimed by two services' in broken(mutate_profile=lambda p: p['hosts'].append('linkedin.com'))
    assert 'claimed by two services' in broken(mutate_profile=lambda p: p['aliases'].append('Higgsfield'))
    assert 'blocked Clayrune origin' in broken(mutate_profile=lambda p: p['hosts'].append('docs.clayrune.io'))
    assert 'unknown field' in broken(lambda i: i.update(extra=1))
    assert 'version must be 2' in broken(lambda i: i.update(version=3))
    # cross-references inside one profile
    assert 'does not exist' in broken(mutate_profile=lambda p: p['routes'][0]['evidence'].append('nope'))
    assert 'must be existing route ids' in broken(mutate_profile=lambda p: p['purposes'][0]['routes'].append('nope'))
    assert 'have no evidence listed' in broken(mutate_profile=lambda p: p['routes'][0]['auth'][0].update(claim='made.up'))
    assert 'cited by no route' in broken(mutate_profile=lambda p: p['evidence'][0]['claim_ids'].append('orphan.claim'))
    assert 'listed twice' in broken(mutate_profile=lambda p: p['routes'][0]['coverage'].append(dict(p['routes'][0]['coverage'][0])))
    assert 'is not backed by the purpose' in broken(mutate_profile=lambda p: p['purposes'][0]['routes'].remove(p['routes'][0]['id']))


def test_a_listed_symlink_out_of_the_directory_is_refused(tmp_path):
    idx = _copy_shipped(tmp_path)
    outside = tmp_path / 'outside.json'
    shutil.move(str(tmp_path / 'profiles' / 'x.json'), outside)
    try:
        (tmp_path / 'profiles' / 'x.json').symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip('symlinks are not available here')
    with pytest.raises(schema.ProfileError, match='resolves outside'):
        loader.load_snapshot(idx)


def test_one_bad_profile_means_no_snapshot_at_all(tmp_path):
    idx = _copy_shipped(tmp_path)
    _edit(tmp_path / 'profiles' / 'youtube.json', lambda p: p.update(hosts=[]))     # the 8th of 9 files
    with pytest.raises(schema.ProfileError):
        loader.load_snapshot(idx)
    with pytest.raises(registry.RegistryError):
        registry.load(idx)


def test_unreadable_json_is_refused(tmp_path):
    idx = _copy_shipped(tmp_path)
    (tmp_path / 'profiles' / 'notion.json').write_text('{not json', encoding='utf-8')
    with pytest.raises(registry.RegistryError, match='cannot read profile notion.json'):
        registry.load(idx)


# ── the schema's own rules ──

def _check(mutate, sid='linkedin'):
    raw = _profile(sid)
    mutate(raw)
    with pytest.raises(schema.ProfileError) as e:
        schema.check_profile(raw)
    return str(e.value)


def test_schema_rejects_a_verification_it_cannot_back():
    ver = lambda p: next(e for e in p['evidence'] if e['result'] == 'verified')     # noqa: E731
    unv = lambda p: next(e for e in p['evidence'] if e['result'] == 'unverified')   # noqa: E731
    assert 'needs its url and the date' in _check(lambda p: ver(p).update(retrieved=None))
    assert 'has no retrieval or publication date' in _check(lambda p: unv(p).update(retrieved='2026-10-05'))
    assert 'not a real date' in _check(lambda p: ver(p).update(retrieved='2026-02-30'))
    assert 'cannot be before retrieved' in _check(lambda p: ver(p).update(retrieved='2026-10-05', attempted='2026-10-01'))
    assert 'schema_version must be 1' in _check(lambda p: p.update(schema_version=2))
    assert 'unknown field' in _check(lambda p: p.update(secret='x'))


def test_schema_keeps_secrets_and_odd_values_out():
    assert 'credential parameter NAMES' in _check(lambda p: p['routes'][0]['auth'][0].update(params=['sk-live 1234 abc']), 'x')
    assert 'must be a plain https address' in _check(lambda p: p['evidence'][0].update(url='http://developer.x.com/a'), 'x')
    assert 'must be a plain https address' in _check(lambda p: p['evidence'][0].update(url='https://u:p@developer.x.com/a'), 'x')
    assert 'must cite a claim' in _check(lambda p: p['routes'][0]['cost']['items'][0].update(claim=None), 'x')
    assert 'only an available route can name an adapter' in _check(lambda p: p['routes'][1].update(adapter_id='account:x'), 'x')
    assert 'must name the adapter' in _check(lambda p: p['routes'][0].pop('adapter_id'), 'x')
    assert 'home_url must be' in _check(lambda p: p.update(home_url='https://evil.example'), 'x')
    assert 'dot-free' in _check(lambda p: p['aliases'].append('x.com'), 'x')


def test_a_route_with_no_cited_claim_never_reads_as_verified():
    prof = copy.deepcopy(_profile('x'))
    prof['routes'] = [r for r in prof['routes'] if r['id'] == 'x-manual']
    out = schema.route_verification(prof['routes'][0], {e['id']: e for e in prof['evidence']})
    assert out['last_verified'] is None and out['status'] != 'verified'


def test_last_verified_is_the_oldest_cited_claim_not_the_newest():
    route = {'auth': [{'claim': 'a'}], 'coverage': [{'claim': 'b'}], 'requirements': [], 'limits': [], 'cost': {'items': []},
             'evidence': ['e1', 'e2']}
    ev = {'e1': {'result': 'verified', 'claim_ids': ['a'], 'retrieved': '2026-01-01', 'attempted': None},
          'e2': {'result': 'verified', 'claim_ids': ['b'], 'retrieved': '2026-10-05', 'attempted': '2026-10-06'}}
    assert schema.route_verification(route, ev) == {'status': 'verified', 'last_verified': '2026-01-01', 'last_attempted': '2026-10-06'}
    ev['e1']['result'] = 'unavailable'
    got = schema.route_verification(route, ev)
    assert got['status'] == 'partial' and got['last_verified'] is None


def test_v1_conversion_is_validated_like_a_profile():
    def bad(mutate):
        raw = copy.deepcopy(V1_GOLD)
        mutate(raw)
        with pytest.raises(schema.ProfileError) as e:
            compat.snapshot_from_v1(raw)
        return str(e.value)
    assert 'claimed by two services' in bad(lambda r: r['services'][1]['hosts'].append('x.com'))
    assert 'blocked Clayrune origin' in bad(lambda r: r['services'][1]['hosts'].append('www.clayrune.io'))
    assert 'unknown field' in bad(lambda r: r['services'][0]['options'][0].update(extra=1))
    assert 'method must be one of' in bad(lambda r: r['services'][0]['options'][0].update(method='ssh'))


def test_two_routes_sharing_a_v1_method_are_ambiguous_not_first_wins():
    prof = copy.deepcopy(registry.profile('higgsfield'))
    twin = copy.deepcopy(next(r for r in prof['routes'] if r['connect_method'] == 'api_key'))
    twin['id'] = 'higgsfield-api-key-2'
    prof['routes'].append(twin)
    rec = compat.project_service(prof)
    assert 'api_key' in rec['ambiguous_methods'] and all(o['method'] != 'api_key' for o in rec['options'])
    with pytest.raises(compat.AmbiguousMethod):
        compat.route_for_method(prof, 'api_key')
    assert compat.route_for_method(prof, 'oauth')['id'] == 'higgsfield-oauth'
    with pytest.raises(KeyError):
        compat.route_for_method(prof, 'mcp')


def test_age_days_is_none_for_an_unknown_date():
    from datetime import date
    assert loader.age_days(None) is None
    assert loader.age_days('2026-10-01', date(2026, 10, 5)) == 4
