"""Slice U2b: the semver subset that turns a dependency range into ONE exact version, on the server
(`mc/desk_connect/custom_npm_semver.py`). Pure functions; a range never reaches the operation."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mc.desk_connect import custom_npm_semver as semver  # noqa: E402


@pytest.mark.parametrize('version, rng, expected', [
    ('1.2.3', '^1.0.0', True), ('2.0.0', '^1.0.0', False), ('1.99.0', '^1.2.3', True), ('1.2.2', '^1.2.3', False),
    ('0.2.5', '^0.2.3', True), ('0.3.0', '^0.2.3', False), ('0.0.3', '^0.0.3', True), ('0.0.4', '^0.0.3', False),
    ('1.2.9', '~1.2.3', True), ('1.3.0', '~1.2.3', False), ('1.9.9', '1.x', True), ('2.0.0', '1.x', False),
    ('1.2.0', '1.2', True), ('1.3.0', '1.2', False), ('3.0.0', '*', True), ('3.0.0', '', True),
    ('1.5.0', '>=1.2.0 <2.0.0', True), ('2.0.0', '>=1.2.0 <2.0.0', False), ('1.4.0', '1.0.0 - 1.4.0', True),
    ('1.4.1', '1.0.0 - 1.4.0', False), ('2.3.0', '^1.0.0 || ^2.0.0', True), ('3.0.0', '^1.0.0 || ^2.0.0', False),
    ('1.2.3', '1.2.3', True), ('1.2.4', '1.2.3', False), ('1.2.3', '=1.2.3', True), ('1.0.0', '>1.0.0', False),
    # a prerelease satisfies only a range that names a prerelease of the same major.minor.patch
    ('1.0.0-beta.1', '^1.0.0', False), ('1.0.1-beta.1', '>=1.0.1-alpha', True), ('1.0.1-beta.1', '>=1.0.0', False),
    ('2.0.0-rc.1', '*', False),
])
def test_satisfies_follows_node_semver(version, rng, expected):
    assert semver.satisfies(version, semver.parse_range(rng)) is expected


@pytest.mark.parametrize('text', [
    'git+https://evil.example/a.git', 'git://evil.example/a.git', 'file:../local', 'link:../local', 'npm:other@1.0.0',
    'workspace:*', 'https://evil.example/a.tgz', 'http://evil.example/a.tgz', 'user/repo', 'github:user/repo',
    '^1.0.0 evil', '1.2.3.4', '>>1.0.0', '^', '~', '1.0.0 -', '||| 1', '1.0.0\x00', 'a' * 300,
])
def test_a_non_registry_address_or_malformed_range_is_not_a_range(text):
    with pytest.raises(semver.RangeError):
        semver.parse_range(text)


def test_the_highest_version_inside_the_range_is_chosen_and_deprecated_ones_are_a_last_resort():
    versions = ['1.0.0', '1.2.0', '1.3.0', '1.4.0-beta.1', '2.0.0']
    assert semver.max_satisfying(versions, '^1.0.0') == '1.3.0'                 # not 2.0.0, not the prerelease
    assert semver.max_satisfying(versions, '^1.0.0', avoid=frozenset({'1.3.0'})) == '1.2.0'
    assert semver.max_satisfying(['1.3.0'], '^1.0.0', avoid=frozenset({'1.3.0'})) == '1.3.0'
    assert semver.max_satisfying(versions, '^3.0.0') is None


def test_a_dist_tag_is_a_tag_and_a_range_is_not():
    assert semver.is_tag('latest') and semver.is_tag('next')
    assert not semver.is_tag('^1.0.0') and not semver.is_tag('1.x') and not semver.is_tag('*') and not semver.is_tag('')
