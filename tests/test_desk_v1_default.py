"""Run the real config loader without importing the server's background systems."""
import ast
import json
import os
import sys
from pathlib import Path

import pytest

from mc import desk_v1_default as rollout


@pytest.fixture()
def loader(tmp_path):
    source = Path(__file__).resolve().parents[1] / 'server.py'
    tree = ast.parse(source.read_text(encoding='utf-8'))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                and n.name == '_load_config')
    namespace = {'CONFIG_PATH': tmp_path / 'config.json', '_DATA_ROOT': tmp_path,
                 'Path': Path, 'os': os, 'json': json, 'sys': sys}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), 'exec'), namespace)
    return namespace['_load_config'], namespace['CONFIG_PATH']


@pytest.mark.parametrize('saved,expected', [
    (None, True),
    ({}, True),
    ({'desk_v1': False}, True),
    ({'desk_v1': False, 'desk_v1_default_migrated': True}, False),
    ({'desk_v1': True}, True),
    ({'desk_v1': True, 'desk_v1_default_migrated': True}, True),
    ({'desk_v1': False, 'desk_v1_default_migrated': False}, False),
])
def test_load_config_migrates_only_unmarked_configs(loader, saved, expected):
    load, path = loader
    if saved is not None:
        saved['user_timezone'] = 'Europe/Paris'
        saved['custom_setting'] = {'keep': 'café'}
        path.write_text(json.dumps(saved), encoding='utf-8')
    config = load()
    persisted = json.loads(path.read_text(encoding='utf-8'))
    assert config['desk_v1'] is persisted['desk_v1'] is expected
    assert config['desk_v1_default_migrated'] is persisted['desk_v1_default_migrated']
    if saved is None or 'desk_v1_default_migrated' not in saved:
        assert persisted['desk_v1_default_migrated'] is True
    if saved is not None:
        assert config['user_timezone'] == persisted['user_timezone'] == 'Europe/Paris'
        assert config['custom_setting'] == persisted['custom_setting'] == {'keep': 'café'}


def test_migrated_user_opt_out_survives_reload_without_rewriting(loader, monkeypatch):
    load, path = loader
    config = load()
    config['desk_v1'] = False
    path.write_text(json.dumps(config), encoding='utf-8')

    def unexpected_write(*args, **kwargs):
        pytest.fail('marked config must never be rewritten by the rollout')

    monkeypatch.setattr(rollout, 'write_json_atomic', unexpected_write)
    assert load()['desk_v1'] is False
    assert json.loads(path.read_text(encoding='utf-8'))['desk_v1'] is False


def test_failed_atomic_replace_keeps_old_config_and_retries(loader, monkeypatch, capsys):
    from mc import atomic_json
    load, path = loader
    original = '{"desk_v1": false, "custom_setting": 42}'
    path.write_text(original, encoding='utf-8')

    def locked(*args):
        raise PermissionError('config locked for test')

    with monkeypatch.context() as patch:
        patch.setattr(atomic_json, '_replace_with_retry', locked)
        config = load()
    assert config['desk_v1'] is False
    assert 'desk_v1_default_migrated' not in config
    assert path.read_text(encoding='utf-8') == original
    assert list(path.parent.glob('.config.json.*')) == []
    error = capsys.readouterr().err
    assert 'failed to persist Desk v1 default migration' in error
    assert 'config locked for test' in error
    assert load()['desk_v1_default_migrated'] is True
    assert json.loads(path.read_text(encoding='utf-8'))['custom_setting'] == 42


@pytest.mark.parametrize('original', ['{broken', '[]'])
def test_unreadable_config_is_not_overwritten_or_marked(loader, capsys, original):
    load, path = loader
    path.write_text(original, encoding='utf-8')
    config = load()
    assert config['desk_v1'] is True
    assert 'desk_v1_default_migrated' not in config
    assert path.read_text(encoding='utf-8') == original
    assert 'failed to read' in capsys.readouterr().err
