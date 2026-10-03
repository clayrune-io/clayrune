"""MC-1022: the fence refuses agent writes to the add-on catalogue, manifest and
installed binaries, and its stdlib-only mirror of the add-ons root cannot drift."""
import importlib.util
from pathlib import Path

import pytest

from steward import fence


def test_addons_root_matches_the_real_implementation(tmp_path, monkeypatch):
    from mc.addons import manifest
    monkeypatch.setenv('CLAYRUNE_HOME', str(tmp_path / 'h'))
    monkeypatch.delenv('CLAYRUNE_ADDONS_DIR', raising=False)
    assert fence._addons_root() == manifest.addons_root()
    monkeypatch.setenv('CLAYRUNE_ADDONS_DIR', str(tmp_path / 'a'))
    assert fence._addons_root() == manifest.addons_root()


@pytest.mark.parametrize('tool', ['Write', 'Edit', 'MultiEdit'])
def test_manifest_and_binary_writes_are_blocked(tmp_path, monkeypatch, tool):
    monkeypatch.setenv('CLAYRUNE_ADDONS_DIR', str(tmp_path / 'addons'))
    for rel in ('installed.json', 'ffmpeg/8.1/bin/ffmpeg.exe'):
        d = fence.classify_action(tool, {'file_path': str(tmp_path / 'addons' / rel)})
        assert d.blocked and not d.overridable


def test_catalogue_writes_are_blocked():
    d = fence.classify_action('Edit', {'file_path': 'mc/addons/catalogue.json'})
    assert d.blocked and not d.overridable


def test_unrelated_paths_stay_allowed(tmp_path, monkeypatch):
    monkeypatch.setenv('CLAYRUNE_ADDONS_DIR', str(tmp_path / 'addons'))
    assert not fence.classify_action('Write', {'file_path': str(tmp_path / 'notes.md')}).blocked
