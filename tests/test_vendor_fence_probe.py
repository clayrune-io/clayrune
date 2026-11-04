"""Native preflight discovery rejects stale/unknown packages and lost guards."""
import json
from pathlib import Path

import pytest

from mc import vendor_fence_probe as probe


def package(tmp_path, vendor='gemini'):
    name = '@google/gemini-cli' if vendor == 'gemini' else '@qwen-code/qwen-code'
    root = tmp_path / 'node_modules' / name
    root.mkdir(parents=True)
    manifest = root / 'package.json'
    manifest.write_text(json.dumps({'name': name, 'bin': {vendor: 'cli.js'}}))
    (root / 'cli.js').write_text('import "./active.js";')
    (root / 'active.js').write_text('export {HookRunner, HookRegistry}; import "./settings.js";')
    (root / 'settings.js').write_text('function loadSettings() {} export {loadSettings};')
    (root / 'stale.js').write_text('export {HookRunner, HookRegistry};')
    return root, manifest


@pytest.mark.parametrize('vendor', ('gemini', 'qwen'))
def test_only_active_import_graph_counts(tmp_path, vendor):
    root, manifest = package(tmp_path, vendor)
    core, loader = probe._modules(str(manifest), vendor)
    assert core == (root / 'active.js').as_uri()
    assert loader == (root / 'settings.js').as_uri()
    (root / 'cli.js').write_text('export {};')
    with pytest.raises(RuntimeError, match='could not be verified'):
        probe._modules(str(manifest), vendor)


def test_unknown_package_refuses(tmp_path):
    binary = tmp_path / 'fake.exe'
    binary.write_text('fake')
    with pytest.raises(RuntimeError, match='package could not be verified'):
        probe.self_test_argv('gemini', binary, str(tmp_path), tmp_path / 'settings.json', 'test')


def test_npm_shim_resolves_manifest(tmp_path):
    root, manifest = package(tmp_path)
    shim = tmp_path / 'gemini.cmd'
    shim.write_text('@echo off')
    assert probe._package('gemini', shim) == manifest


def test_probe_argv_is_native_and_not_a_model_turn(tmp_path, monkeypatch):
    root, manifest = package(tmp_path)
    (root / 'node.exe').write_text('fake node')
    settings = tmp_path / 'settings.json'
    settings.write_text('{}')
    monkeypatch.setattr(probe, '_package', lambda *args: manifest)
    argv = probe.self_test_argv('gemini', tmp_path / 'gemini', str(tmp_path), settings, 'selftest')
    assert argv[0] == str(root / 'node.exe')
    assert argv[2] == 'gemini'
    assert argv[3] == (root / 'active.js').as_uri()
    assert Path(argv[1]).read_text() == probe.NODE_PROBE
    assert '--prompt' not in argv
