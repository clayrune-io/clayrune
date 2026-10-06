"""Desk user-chosen MCP server, slice U2b: the exact dependency closure of a package, its card and its
staged installation (docs/DESK_SERVICE_PROFILES_SPEC.md, section 6.2).

Pinned, each by a hostile fixture (a fake registry; sockets and processes poisoned, so nothing here
can reach a network or run npm):

  * the card lists every dependency with its exact name, version and sha512 BEFORE Save, resolved
    from ranges on the server, highest version inside the range, never a range in the operation;
  * a registry that publishes a newer version after Review, or serves other bytes for an approved
    version, cannot change what is installed: bytes are held to the approved digest;
  * a `.npmrc` (in the package, in the user's home, in the environment), registry overrides and
    ambient loaders cannot move a fetch to another address; a registry that points a tarball at
    another host is refused;
  * a dependency that is not a registry range (git, file, link, alias, workspace, URL) is refused;
  * conflicting versions nest, a cycle ends, an archive that names another package is refused, and
    every size, depth and count limit refuses with nothing written;
  * install is staged: a failure leaves nothing in the package area and nothing registered.
"""
from __future__ import annotations

import json

import pytest

from tests.test_desk_connect_custom import (PKG, REG, _good, _tar, artifact, disk, env,  # noqa: F401
                                            nothing_written, operation, packages_dir, review, save, service, store)

from mc.desk_connect import custom_npm_closure as closure  # noqa: E402
from mc.desk_connect import custom_npm_registry as registry  # noqa: E402
from mc.desk_connect import mcp_package_store  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_caches():
    closure._forget_all_for_tests()
    yield
    closure._forget_all_for_tests()


# ── fixtures ─────────────────────────────────────────────────────────────────

def pub(env, name, version, deps=None, *, members=None, tarball=None, stated=True, deprecated=False, **manifest):
    """Publish `name@version` to the fake registry (its archive and its version list). Returns the archive."""
    m = {'name': name, 'version': version, 'main': 'index.js', 'license': 'MIT'}
    if deps is not None:
        m['dependencies'] = deps
    m.update(manifest)
    mem = members if members is not None else [('package/package.json', 'file', json.dumps(m).encode()),
                                               ('package/index.js', 'file', f'// {name} {version}\n'.encode())]
    tar = _tar(mem)
    url = artifact.tarball_url(name, version)
    env.reg.tars[url] = tar
    doc = env.reg.docs.setdefault(registry.document_url(name), {'name': name, 'versions': {}, 'dist-tags': {}})
    dist = {'tarball': tarball or url}
    if stated:
        dist['integrity'] = mcp_package_store.integrity_of(tar)
    doc['versions'][version] = {'dist': dist, **({'deprecated': 'gone'} if deprecated else {})}
    doc['dist-tags']['latest'] = max(doc['versions'], key=lambda v: tuple(int(x) for x in v.split('-')[0].split('.')))
    return tar


def root(env, deps, **manifest):
    """The package a person types: `fixture-mcp@1.2.3` asking for `deps`."""
    return env.reg.publish('1.2.3', members=_good(dependencies=deps, **manifest))


def card_of(env, **over):
    r = review(env, **over)
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def saved(env, card, **over):
    r = save(env, card, **over)
    assert r.status_code in (200, 201), r.get_json()
    return r.get_json()


def dep_names(card):
    return {(d['name'], d['version'], d['path']) for d in card['dependencies']}


def op_of(env, card):
    return service._prepared[card['request_id']]['op']


def tree(env, op):
    base = artifact.package_dir(op) / 'package'
    return sorted(str(p.relative_to(base)).replace('\\', '/') for p in base.rglob('package.json')
                  if 'node_modules' in p.parts)


# ── 1. the closure is exact, resolved on the server, and shown before Save ──

def test_the_card_lists_every_dependency_with_exact_version_and_sha512_before_save(env):
    root(env, {'alpha': '^1.0.0'})
    pub(env, 'alpha', '1.2.0', {'beta': '~2.1.0'})
    pub(env, 'alpha', '1.3.0', {'beta': '~2.1.0'})
    pub(env, 'alpha', '2.0.0', {'beta': '~2.1.0'})              # outside ^1.0.0
    pub(env, 'alpha', '1.4.0-beta.1')                           # a prerelease is not chosen by a range
    pub(env, 'beta', '2.1.0')
    t = pub(env, 'beta', '2.1.9')
    pub(env, 'beta', '2.2.0')
    card = card_of(env)
    assert dep_names(card) == {('alpha', '1.3.0', 'node_modules/alpha'), ('beta', '2.1.9', 'node_modules/beta')}
    by = {d['name']: d for d in card['dependencies']}
    assert by['beta']['integrity'] == mcp_package_store.integrity_of(t) and by['beta']['integrity'].startswith('sha512-')
    assert card['dependency_totals']['count'] == 2
    assert card['install_steps'] == [] and 'staging folder' in card['install_note']
    assert card['package']['install_scripts_not_run'] == []
    assert any(r['code'] == 'dependencies_installed' for r in card['risks'])
    nothing_written(env)                                        # Review wrote nothing


def test_the_operation_holds_exact_versions_and_digests_never_a_range_and_is_fingerprinted(env):
    root(env, {'alpha': '^1.0.0'})
    pub(env, 'alpha', '1.3.0')
    card = card_of(env)
    op = op_of(env, card)
    assert op['dependencies'] == [{'name': 'alpha', 'version': '1.3.0', 'path': 'node_modules/alpha',
                                   'integrity': mcp_package_store.integrity_of(env.reg.tars[artifact.tarball_url('alpha', '1.3.0')])}]
    assert '^' not in json.dumps(op['dependencies'])
    assert operation.fingerprint(op) == card['fingerprint']
    pub(env, 'alpha', '1.3.0', members=[('package/package.json', 'file', b'{"name":"alpha","version":"1.3.0"}'),
                                        ('package/index.js', 'file', b'// different bytes\n')])
    closure._forget_all_for_tests()
    assert card_of(env)['fingerprint'] != card['fingerprint']   # another digest is another approval


def test_a_package_that_needs_nothing_keeps_its_u2a_directory_and_fingerprint_shape(env):
    card = card_of(env)
    op = op_of(env, card)
    assert 'dependencies' not in op and op['install_steps'] == []
    assert artifact.dir_id(op) == artifact.digest_id(op['integrity'])
    assert 'dependencies' not in card and 'scripts' not in card


def test_save_installs_exactly_the_approved_archives_into_the_digest_directory(env):
    root(env, {'alpha': '^1.0.0'})
    pub(env, 'alpha', '1.3.0', {'beta': '^2.0.0'})
    pub(env, 'beta', '2.4.0')
    card = card_of(env)
    out = saved(env, card)
    assert out['state'] == 'registered'
    op = op_of(env, card)
    base = artifact.package_dir(op)
    assert base.parent == packages_dir(env) and base.name == artifact.dir_id(op) != artifact.digest_id(op['integrity'])
    assert (base / 'package' / 'node_modules' / 'alpha' / 'index.js').read_text() == '// alpha 1.3.0\n'
    assert (base / 'package' / 'node_modules' / 'beta' / 'index.js').read_text() == '// beta 2.4.0\n'
    assert tree(env, op) == ['node_modules/alpha/package.json', 'node_modules/beta/package.json']
    assert json.loads((base / artifact.VERIFIED_MARKER).read_text())['closure'] == base.name
    assert not [p for p in packages_dir(env).iterdir() if p.name.startswith('.')]    # no staging folder is left
    assert not list(base.rglob('.npmrc')) and not list(base.rglob('package-lock.json'))


def test_a_newer_version_published_after_review_cannot_change_what_is_installed(env):
    root(env, {'alpha': '^1.0.0'})
    pub(env, 'alpha', '1.3.0')
    card = card_of(env)
    pub(env, 'alpha', '1.9.0', members=[('package/package.json', 'file', b'{"name":"alpha","version":"1.9.0"}'),
                                        ('package/index.js', 'file', b'// the newer one\n')])
    closure._forget_all_for_tests()
    saved(env, card)
    base = artifact.package_dir(op_of(env, card))
    assert (base / 'package' / 'node_modules' / 'alpha' / 'index.js').read_text() == '// alpha 1.3.0\n'
    assert artifact.tarball_url('alpha', '1.9.0') not in env.reg.tar_fetches


def test_other_bytes_for_an_approved_version_are_refused_and_nothing_is_placed(env):
    root(env, {'alpha': '^1.0.0'})
    pub(env, 'alpha', '1.3.0')
    card = card_of(env)
    env.reg.served[artifact.tarball_url('alpha', '1.3.0')] = _tar(
        [('package/package.json', 'file', b'{"name":"alpha","version":"1.3.0"}'), ('package/index.js', 'file', b'// swapped\n')])
    out = saved(env, card)
    assert out['state'] == 'setup_failed' and out['code'] == 'pin_mismatch'
    assert disk(env) == [] and not env.glob_cfg.exists() and not env.proj_cfg.exists()


def test_a_registry_digest_that_does_not_match_the_archive_is_refused_at_review(env):
    root(env, {'alpha': '^1.0.0'})
    pub(env, 'alpha', '1.3.0')
    doc = env.reg.docs[registry.document_url('alpha')]
    doc['versions']['1.3.0']['dist']['integrity'] = 'sha512-' + 'A' * 86 + '=='
    r = review(env)
    assert r.status_code == 409 and r.get_json()['code'] == 'pin_mismatch'
    nothing_written(env)


# ── 2. nothing ambient chooses a fetch ──────────────────────────────────────

def test_npmrc_in_the_package_the_home_folder_and_the_environment_cannot_move_a_fetch(env, monkeypatch, tmp_path):
    home = tmp_path / 'home'
    home.mkdir()
    (home / '.npmrc').write_text('registry=https://evil.example/\n@scope:registry=https://evil.example/\n')
    for var, val in (('HOME', str(home)), ('USERPROFILE', str(home)), ('npm_config_registry', 'https://evil.example/'),
                     ('NPM_CONFIG_REGISTRY', 'https://evil.example/'), ('NPM_CONFIG_USERCONFIG', str(home / '.npmrc')),
                     ('NODE_OPTIONS', '--require /tmp/evil.js'), ('NODE_PATH', '/tmp/evil'), ('NODE_EXTRA_CA_CERTS', '/tmp/ca.pem')):
        monkeypatch.setenv(var, val)
    root(env, {'alpha': '^1.0.0'}, publishConfig={'registry': 'https://evil.example/'})
    hostile = [('package/package.json', 'file', json.dumps({'name': 'alpha', 'version': '1.0.0', 'main': 'index.js'}).encode()),
               ('package/.npmrc', 'file', b'registry=https://evil.example/\n//evil.example/:_authToken=x\n'),
               ('package/index.js', 'file', b'// alpha\n')]
    pub(env, 'alpha', '1.0.0', members=hostile)
    card = card_of(env)
    saved(env, card)
    fetched = env.reg.doc_fetches + env.reg.tar_fetches
    assert fetched and all(u.startswith(REG) for u in fetched), fetched
    assert not any('evil' in u for u in fetched)
    base = artifact.package_dir(op_of(env, card))
    assert (base / 'package' / 'node_modules' / 'alpha' / '.npmrc').exists()   # the bytes are the archive's own...
    assert not (base / '.npmrc').exists() and not (base / 'package' / '.npmrc').exists()   # ...and no config is read or written


@pytest.mark.parametrize('address', ['https://evil.example/alpha/-/alpha-1.0.0.tgz', 'http://registry.npmjs.org/alpha/-/alpha-1.0.0.tgz',
                                     'https://registry.npmjs.org.evil.example/alpha/-/alpha-1.0.0.tgz',
                                     'https://registry.npmjs.org/other/-/other-1.0.0.tgz'])
def test_a_registry_that_points_a_tarball_at_another_address_is_refused(env, address):
    root(env, {'alpha': '1.0.0'})
    pub(env, 'alpha', '1.0.0', tarball=address)
    r = review(env)
    assert r.status_code == 502 and r.get_json()['code'] == 'registry_address_unexpected'
    assert not any('evil' in u for u in env.reg.tar_fetches)
    nothing_written(env)


@pytest.mark.parametrize('rng', ['git+https://evil.example/a.git', 'github:evil/a', 'evil/a', 'file:../a', 'link:../a', 'npm:other@1.0.0',
                                 'workspace:*', 'https://evil.example/a.tgz', 'http://evil.example/a.tgz'])
def test_a_dependency_that_is_not_a_registry_range_is_refused(env, rng):
    root(env, {'alpha': rng})
    r = review(env)
    assert r.status_code == 422 and r.get_json()['code'] == 'dependency_unsupported'
    assert 'alpha' in r.get_json()['error']
    assert env.reg.tar_fetches == [artifact.tarball_url(PKG, '1.2.3')]      # nothing but the root was downloaded
    nothing_written(env)


def test_a_dependency_with_a_hidden_or_oversized_range_or_a_bad_name_is_refused(env):
    for deps in ({'alpha': '^1.0.0‮'}, {'alpha': 'x' * 400}, {'../evil': '1.0.0'}, {'a b': '1.0.0'}, {'alpha': 5}, ['alpha']):
        root(env, deps)
        r = review(env)
        assert r.status_code == 422 and r.get_json()['code'] == 'dependencies_unreadable', (deps, r.get_json())
    nothing_written(env)


def test_no_process_starts_during_review_or_save_when_nothing_is_approved_to_run(env):
    root(env, {'alpha': '^1.0.0'})
    pub(env, 'alpha', '1.0.0', scripts={'postinstall': 'node evil.js'})
    saved(env, card_of(env))                                    # Popen / os.system / sockets are poisoned by the fixture


# ── 3. a missing, wrong or hostile registry answer refuses with nothing written ──

def test_a_dependency_the_registry_does_not_have_or_cannot_satisfy_is_refused(env):
    root(env, {'ghost': '^1.0.0'})
    r = review(env)
    assert r.status_code == 404 and r.get_json()['code'] == 'dependency_not_found'
    root(env, {'alpha': '^5.0.0'})
    pub(env, 'alpha', '1.0.0')
    r = review(env)
    assert r.status_code == 422 and r.get_json()['code'] == 'dependency_unsatisfiable'
    nothing_written(env)


def test_an_archive_that_names_another_package_than_the_one_asked_for_is_refused(env):
    root(env, {'alpha': '1.0.0'})
    pub(env, 'alpha', '1.0.0', members=[('package/package.json', 'file', b'{"name":"evil","version":"1.0.0"}'),
                                        ('package/index.js', 'file', b'x')])
    r = review(env)
    assert r.status_code == 502
    nothing_written(env)
    pub(env, 'alpha', '1.0.0', members=[('package/package.json', 'file', b'{"name":"alpha","version":"9.9.9"}'),
                                        ('package/index.js', 'file', b'x')])
    closure._forget_all_for_tests()
    assert review(env).status_code == 502


def test_a_registry_document_for_another_package_is_refused(env):
    root(env, {'alpha': '1.0.0'})
    pub(env, 'alpha', '1.0.0')
    env.reg.docs[registry.document_url('alpha')]['name'] = 'someone-else'
    r = review(env)
    assert r.status_code == 502 and r.get_json()['code'] == 'registry_mismatch'
    nothing_written(env)


def test_traversal_links_and_bombs_inside_a_dependency_archive_are_refused(env):
    root(env, {'alpha': '1.0.0'})
    good_m = ('package/package.json', 'file', b'{"name":"alpha","version":"1.0.0"}')
    for bad in ([good_m, ('package/../../escape.js', 'file', b'x')], [good_m, ('package/link', 'symlink', '../../../etc/passwd')],
                [good_m, ('package/hard', 'hardlink', 'package/index.js')], [good_m, ('package/pipe', 'fifo', None)],
                [good_m, ('/abs/evil.js', 'file', b'x')]):
        pub(env, 'alpha', '1.0.0', members=bad)
        closure._forget_all_for_tests()
        r = review(env)
        assert r.status_code in (400, 422, 502), (bad[1], r.status_code, r.get_json())
        nothing_written(env)


def test_the_closure_limits_refuse_before_anything_is_saved(env, monkeypatch):
    names = [f'dep{i}' for i in range(6)]
    root(env, {n: '1.0.0' for n in names})
    for n in names:
        pub(env, n, '1.0.0')
    monkeypatch.setattr(closure, 'MAX_PACKAGES', 4)
    r = review(env)
    assert r.status_code == 413 and r.get_json()['code'] == 'closure_too_large'
    monkeypatch.setattr(closure, 'MAX_PACKAGES', 200)
    monkeypatch.setattr(closure, 'MAX_DOWNLOAD_TOTAL', 100)
    closure._forget_all_for_tests()
    assert review(env).status_code == 413
    monkeypatch.setattr(closure, 'MAX_DOWNLOAD_TOTAL', 10 ** 9)
    monkeypatch.setattr(artifact, 'MAX_TREE_MEMBERS', 5)
    closure._forget_all_for_tests()
    assert review(env).status_code == 413
    nothing_written(env)


def test_a_chain_deeper_than_the_limit_is_refused(env, monkeypatch):
    root(env, {'dep0': '1.0.0'})
    for i in range(6):
        pub(env, f'dep{i}', '1.0.0', {f'dep{i + 1}': '1.0.0'})
    pub(env, 'dep6', '1.0.0')
    monkeypatch.setattr(closure, 'MAX_LEVELS', 3)
    r = review(env)
    assert r.status_code == 413 and r.get_json()['code'] == 'closure_too_large'
    nothing_written(env)


def test_a_slow_closure_is_refused_at_its_deadline(env, monkeypatch):
    root(env, {'alpha': '1.0.0'})
    pub(env, 'alpha', '1.0.0', {'beta': '1.0.0'})
    pub(env, 'beta', '1.0.0')
    clock = iter(range(0, 10 ** 6, 100))
    monkeypatch.setattr(closure, '_explore', lambda r, now=None, _e=closure._explore: _e(r, now=lambda: next(clock)))
    r = review(env)
    assert r.status_code == 413 and r.get_json()['code'] == 'closure_timeout'
    nothing_written(env)


# ── 4. placement: conflicts nest, cycles end, bundled and optional are honoured ──

def test_two_versions_of_one_dependency_nest_the_way_node_finds_them(env):
    root(env, {'alpha': '1.0.0', 'beta': '1.0.0'})
    pub(env, 'alpha', '1.0.0', {'shared': '^1.0.0'})
    pub(env, 'beta', '1.0.0', {'shared': '^2.0.0'})
    pub(env, 'shared', '1.5.0')
    pub(env, 'shared', '2.5.0')
    card = card_of(env)
    paths = dep_names(card)
    assert ('shared', '1.5.0', 'node_modules/shared') in paths                       # alpha is served first, so it is the top-level one
    assert ('shared', '2.5.0', 'node_modules/beta/node_modules/shared') in paths    # beta's own copy sits next to beta
    saved(env, card)
    base = artifact.package_dir(op_of(env, card)) / 'package'
    assert (base / 'node_modules' / 'shared' / 'index.js').read_text() == '// shared 1.5.0\n'
    assert (base / 'node_modules' / 'beta' / 'node_modules' / 'shared' / 'index.js').read_text() == '// shared 2.5.0\n'


def test_a_shared_dependency_is_installed_once_and_a_cycle_ends(env):
    root(env, {'alpha': '1.0.0', 'beta': '1.0.0'})
    pub(env, 'alpha', '1.0.0', {'beta': '1.0.0'})
    pub(env, 'beta', '1.0.0', {'alpha': '1.0.0'})                # a cycle
    card = card_of(env)
    assert dep_names(card) == {('alpha', '1.0.0', 'node_modules/alpha'), ('beta', '1.0.0', 'node_modules/beta')}
    assert saved(env, card)['state'] == 'registered'


def test_the_layout_is_the_same_whatever_order_downloads_finish_in(env, monkeypatch):
    root(env, {'alpha': '^1.0.0', 'beta': '^1.0.0', 'gamma': '^1.0.0'})
    for n in ('alpha', 'beta', 'gamma'):
        pub(env, n, '1.0.0', {'shared': '^1.0.0' if n != 'gamma' else '^2.0.0'})
    pub(env, 'shared', '1.1.0')
    pub(env, 'shared', '2.1.0')
    first = card_of(env)['dependencies']
    import time
    real = closure._registry.fetch
    monkeypatch.setattr(closure._registry, 'fetch', lambda n, **k: (time.sleep(0.05 if n == 'alpha' else 0), real(n, **k))[1])
    closure._forget_all_for_tests()
    assert [d for d in card_of(env)['dependencies']] == first


def test_packages_the_archive_carries_inside_itself_stay_as_the_archive_has_them(env):
    bundled = _good(dependencies={'inner': '^1.0.0', 'alpha': '1.0.0'}, bundledDependencies=['inner']) + \
        [('package/node_modules/inner/package.json', 'file', b'{"name":"inner","version":"1.0.0"}'),
         ('package/node_modules/inner/index.js', 'file', b'// carried\n')]
    env.reg.publish('1.2.3', members=bundled)
    pub(env, 'alpha', '1.0.0')
    card = card_of(env)
    assert dep_names(card) == {('alpha', '1.0.0', 'node_modules/alpha')}        # `inner` is not fetched: it is in the archive
    assert not any('inner' in u for u in env.reg.tar_fetches + env.reg.doc_fetches)
    saved(env, card)
    base = artifact.package_dir(op_of(env, card)) / 'package' / 'node_modules'
    assert (base / 'inner' / 'index.js').read_text() == '// carried\n' and (base / 'alpha').is_dir()


def test_a_folder_the_archive_already_holds_is_never_merged_into(env):
    carried = _good(dependencies={'alpha': '1.0.0'}) + [('package/node_modules/alpha/stray.js', 'file', b'// not a package\n')]
    env.reg.publish('1.2.3', members=carried)
    pub(env, 'alpha', '1.0.0')
    r = review(env)                                              # the place alpha belongs is taken by files Clayrune did not approve
    assert r.status_code == 422 and r.get_json()['code'] == 'dependency_conflict'
    nothing_written(env)


def test_optional_dependencies_are_listed_not_installed_and_required_peers_are(env):
    root(env, {'alpha': '1.0.0'}, optionalDependencies={'fsevents': '^2.0.0'}, peerDependencies={'peerdep': '^1.0.0', 'opt-peer': '^1.0.0'},
         peerDependenciesMeta={'opt-peer': {'optional': True}})
    pub(env, 'alpha', '1.0.0')
    pub(env, 'peerdep', '1.4.0')
    card = card_of(env)
    assert {d['name'] for d in card['dependencies']} == {'alpha', 'peerdep'}
    assert card['optional_not_installed'] == [{'package': f'{PKG}@1.2.3', 'optional': ['fsevents']}]
    assert not any('fsevents' in u or 'opt-peer' in u for u in env.reg.doc_fetches)


def test_a_deprecated_version_is_chosen_only_when_nothing_else_fits(env):
    root(env, {'alpha': '^1.0.0'})
    pub(env, 'alpha', '1.1.0')
    pub(env, 'alpha', '1.2.0', deprecated=True)
    assert [d['version'] for d in card_of(env)['dependencies']] == ['1.1.0']
    root(env, {'beta': '^1.0.0'})
    pub(env, 'beta', '1.2.0', deprecated=True)
    card = card_of(env)
    assert card['dependencies'][0]['version'] == '1.2.0' and card['dependencies'][0]['deprecated'] is True


def test_a_dist_tag_dependency_is_resolved_to_the_exact_version_it_names(env):
    root(env, {'alpha': 'latest'})
    pub(env, 'alpha', '1.0.0')
    pub(env, 'alpha', '1.1.0')
    assert [d['version'] for d in card_of(env)['dependencies']] == ['1.1.0']


# ── 5. staged installation ──────────────────────────────────────────────────

def test_a_failure_part_way_leaves_nothing_in_the_package_area_and_nothing_registered(env):
    root(env, {'alpha': '1.0.0', 'beta': '1.0.0'})
    pub(env, 'alpha', '1.0.0')
    pub(env, 'beta', '1.0.0')
    card = card_of(env)
    env.reg.served[artifact.tarball_url('beta', '1.0.0')] = _tar(
        [('package/package.json', 'file', b'{"name":"beta","version":"1.0.0"}'), ('package/index.js', 'file', b'// swapped\n')])
    out = saved(env, card)
    assert out['state'] == 'setup_failed'
    assert disk(env) == []
    assert not env.glob_cfg.exists() and not env.proj_cfg.exists()
    rec = store.all_records()[0]
    assert rec['state'] == 'setup_failed'                          # the approval is on record; it never reads as connected
    assert service.connections(lambda pid: str(env.proj_dir))[0]['state'] == 'setup_failed'
    env.reg.served.clear()
    assert save(env, card).status_code in (200, 201)               # the same Review can be saved again
    assert service.connections(lambda pid: str(env.proj_dir))[0]['state'] == 'registered'


def test_an_existing_folder_for_the_approval_is_never_overwritten(env):
    root(env, {'alpha': '1.0.0'})
    pub(env, 'alpha', '1.0.0')
    card = card_of(env)
    op = op_of(env, card)
    stray = artifact.package_dir(op)
    stray.mkdir(parents=True)
    (stray / 'keep.txt').write_text('mine')
    out = saved(env, card)
    assert out['state'] == 'setup_failed' and out['code'] == 'package_store_conflict'
    assert (stray / 'keep.txt').read_text() == 'mine'


def test_a_dependency_folder_the_closure_does_not_account_for_is_refused_at_install(env, monkeypatch):
    from mc.desk_connect import custom_npm_install as inst
    root(env, {'alpha': '1.0.0'})
    pub(env, 'alpha', '1.0.0')
    card = card_of(env)
    op = op_of(env, card)
    real = inst._unpack_into

    def hostile(data, stage, dest, n):
        if n == 1:
            (stage / 'package' / 'node_modules' / 'alpha').mkdir(parents=True)
        return real(data, stage, dest, n)
    monkeypatch.setattr(inst, '_unpack_into', hostile)
    with pytest.raises(inst.ActivationError) as e:
        inst.install(op)
    assert e.value.code == 'package_invalid'
    assert disk(env) == []


@pytest.mark.parametrize('mutate', [
    lambda op: op['dependencies'][0].update(path='../../escape'), lambda op: op['dependencies'][0].update(path='node_modules/other'),
    lambda op: op['dependencies'][0].update(integrity='sha1-AAAA'), lambda op: op['dependencies'][0].update(version='^1.0.0'),
    lambda op: op['dependencies'][0].update(name='../evil'), lambda op: op['dependencies'][0].update(extra='x'),
    lambda op: op['dependencies'].append(dict(op['dependencies'][0])),
    lambda op: op['dependencies'].append({'name': 'x', 'version': '1.0.0', 'path': 'node_modules/y/node_modules/x',
                                          'integrity': op['dependencies'][0]['integrity']}),
    lambda op: op.update(dependencies='all'), lambda op: op.update(install_steps=[{'path': ''}]),
    lambda op: op.update(tarball='https://evil.example/x.tgz'),
])
def test_a_tampered_operation_is_refused_by_install_before_anything_is_fetched(env, mutate):
    from mc.desk_connect import custom_npm_install as inst
    root(env, {'alpha': '1.0.0'})
    pub(env, 'alpha', '1.0.0')
    op = json.loads(json.dumps(op_of(env, card_of(env))))
    mutate(op)
    fetched = len(env.reg.tar_fetches)
    with pytest.raises(inst.ActivationError):
        inst.install(op)
    assert len(env.reg.tar_fetches) == fetched and disk(env) == []


def test_the_self_contained_check_never_calls_a_closure_installed(env):
    root(env, {'alpha': '1.0.0'})
    pub(env, 'alpha', '1.0.0')
    card = card_of(env)
    saved(env, card)
    op = op_of(env, card)
    assert artifact.is_installed(op) is False                      # `is_installed` of U2a does not vouch for a closure
    with pytest.raises(artifact.ActivationError):
        artifact.install(op)
    from mc.desk_connect import custom_npm_install as inst
    assert inst.is_installed(op) is True
