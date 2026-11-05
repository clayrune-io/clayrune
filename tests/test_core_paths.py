"""Resolved containment, including MC-1158's Windows mkdir race."""
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath, PureWindowsPath
from types import SimpleNamespace

import pytest

from mc import core


def _resolved_pair(monkeypatch, candidate, base, platform='nt'):
    """Supply actual resolve() output spellings without touching a network share."""
    paths = iter((candidate, base))
    monkeypatch.setattr(core, 'Path', lambda _: SimpleNamespace(resolve=lambda: next(paths)))
    monkeypatch.setattr(core, 'os', SimpleNamespace(name=platform, sep='\\' if platform == 'nt' else '/'))


@pytest.mark.parametrize('extended_side', ['candidate', 'base', 'both'])
@pytest.mark.parametrize('same_path', [False, True])
@pytest.mark.parametrize('unc', [False, True])
def test_windows_mixed_resolved_namespaces(monkeypatch, extended_side, same_path, unc):
    root = r'\\server\share\store' if unc else r'C:\store'
    candidate = root if same_path else root + r'\new\campaign\checklist.json'

    def extended(path):
        return '\\\\?\\UNC\\' + path[2:] if unc else '\\\\?\\' + path

    if extended_side in ('candidate', 'both'):
        candidate = extended(candidate)
    if extended_side in ('base', 'both'):
        root = extended(root)
    _resolved_pair(monkeypatch, PureWindowsPath(candidate), PureWindowsPath(root))
    assert core.path_is_within('candidate', 'base') is True


@pytest.mark.parametrize('candidate,base,expected', [
    (r'C:\store\child', r'C:\STORE', True),
    (r'C:\store-other\child', r'C:\store', False),
    (r'\\?\C:\store-other\child', r'C:\store', False),
    (r'C:\store\child', r'D:\store', False),
    (r'C:\store', 'C:\\', True),
    (r'\\?\C:\store', 'C:\\', True),
    (r'\\server\share\store', '\\\\server\\share\\', True),
    (r'\\?\UNC\server\share\store', '\\\\server\\share\\', True),
    (r'\\?\UNC\other\share\store', r'\\server\share', False),
    (r'\\?\UNC\server\share2\store', r'\\server\share', False),
    (r'\\?\C:\store.\child', r'C:\store', False),
    (r'\\?\C:\store \child', r'C:\store', False),
    (r'\\?\Volume{abc}\store', r'C:\store', False),
    (r'\\.\C:\store\child', r'C:\store', False),
])
def test_windows_boundaries(monkeypatch, candidate, base, expected):
    _resolved_pair(monkeypatch, PureWindowsPath(candidate), PureWindowsPath(base))
    assert core.path_is_within('candidate', 'base') is expected


@pytest.mark.parametrize('candidate,base,expected', [
    ('/store/new/file', '/store', True),
    ('/store', '/store', True),
    ('/store-other/file', '/store', False),
    ('/Store/file', '/store', False),
    ('/store/file', '/', True),
])
def test_posix_boundaries(monkeypatch, candidate, base, expected):
    _resolved_pair(monkeypatch, PurePosixPath(candidate), PurePosixPath(base), 'posix')
    assert core.path_is_within('candidate', 'base') is expected


@pytest.mark.parametrize('error', [OSError('unreadable'), RuntimeError('link loop'), ValueError('bad path')])
@pytest.mark.parametrize('failed_side', ['candidate', 'base'])
def test_resolution_errors_reject(monkeypatch, tmp_path, error, failed_side):
    def resolve(value):
        if value == failed_side:
            raise error
        return tmp_path

    monkeypatch.setattr(core, 'Path', lambda value: SimpleNamespace(resolve=lambda: resolve(value)))
    assert core.path_is_within('candidate', 'base') is False


def test_real_paths_and_parent_traversal(tmp_path):
    root = tmp_path / 'store'
    root.mkdir()
    assert core.path_is_within(root / 'missing' / 'file', root)
    assert not core.path_is_within(root / '..' / 'outside', root)
    assert not core.path_is_within(tmp_path / 'store-sibling', root)
    assert not core.path_is_within('bad\0path', root)


def test_symlink_escape_and_symlinked_base(tmp_path):
    root = tmp_path / 'store'
    outside = tmp_path / 'outside'
    root.mkdir()
    outside.mkdir()
    link = root / 'link'
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as e:
        pytest.skip(f'symlink creation unavailable: {e}')
    assert not core.path_is_within(link / 'missing-file', root)
    assert core.path_is_within(outside / 'missing-file', link)


@pytest.mark.skipif(os.name != 'nt', reason='Windows resolve() namespace race')
def test_windows_concurrent_directory_creation(tmp_path):
    root = tmp_path / 'store'
    root.mkdir()
    with ThreadPoolExecutor(max_workers=1) as pool:
        for i in range(300):
            candidate = root / str(i) / 'campaign' / 'checklist.json'
            barrier = threading.Barrier(2)

            def create():
                barrier.wait()
                candidate.parent.mkdir(parents=True)

            future = pool.submit(create)
            barrier.wait()
            inside = core.path_is_within(candidate, root)
            outside = core.path_is_within(candidate, root / 'sibling')
            future.result()
            assert inside, f'false outside at mkdir probe {i}'
            assert not outside, f'false inside at mkdir probe {i}'
