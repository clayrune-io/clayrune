"""MC-1102: temporary Qwen layouts; no real installer or network calls."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from mc import cli_install, cli_update, qwen_cli_install as qwen


@pytest.fixture
def layout(tmp_path, monkeypatch):
    for key in tuple(qwen.os.environ):
        if key.startswith('QWEN_INSTALL_'):
            monkeypatch.delenv(key)
    home = tmp_path / 'home'
    monkeypatch.setattr(Path, 'home', lambda: home)
    monkeypatch.setenv('LOCALAPPDATA', str(home / 'AppData' / 'Local'))

    def create(platform, *, manifest=None):
        root = home / 'AppData' / 'Local' / 'qwen-code' if platform == 'win32' else home / '.local'
        lib = root / 'qwen-code' if platform == 'win32' else root / 'lib' / 'qwen-code'
        filename = 'qwen.cmd' if platform == 'win32' else 'qwen'
        wrapper, inner = root / 'bin' / filename, lib / 'bin' / filename
        wrapper.parent.mkdir(parents=True, exist_ok=True)
        inner.parent.mkdir(parents=True, exist_ok=True)
        wrapper.write_text('test wrapper', encoding='utf-8')
        inner.write_text('test runtime launcher', encoding='utf-8')
        (lib / 'manifest.json').write_text(json.dumps(manifest if manifest is not None else {
            'name': '@qwen-code/qwen-code', 'version': '0.23.4', 'runtime': 'node',
        }), encoding='utf-8')
        return wrapper, inner
    return create


@pytest.mark.parametrize('platform', ['win32', 'darwin', 'linux'])
@pytest.mark.parametrize('inner', [False, True])
def test_shared_detector_and_command_recognise_official_layout(layout, platform, inner):
    binary = layout(platform)[int(inner)]
    method = 'standalone-windows' if platform == 'win32' else 'standalone'
    assert cli_install.install_method(binary, platform, 'qwen') == method
    assert cli_install.update_command('qwen', binary, platform) == qwen.UPDATE_COMMANDS[method]


@pytest.mark.parametrize('content', ['{}', '[]', 'null', '{broken',
                                      '{"name":"another-package"}', None])
def test_missing_wrong_or_corrupt_manifest_stays_unknown(layout, content):
    wrapper, inner = layout('linux')
    manifest = inner.parent.parent / 'manifest.json'
    if content is None:
        manifest.unlink()
    else:
        manifest.write_text(content, encoding='utf-8')
    assert cli_install.install_method(wrapper, 'linux', 'qwen') == ''
    assert cli_install.update_command('qwen', wrapper, 'linux') is None


def test_npm_install_keeps_npm_and_codex_layout_is_not_qwen(layout):
    layout('linux')
    assert cli_install.update_command('qwen', '/x/node_modules/@qwen-code/qwen-code/bin/qwen', 'linux') == (
        'npm install -g @qwen-code/qwen-code@latest')
    assert cli_install.update_command('qwen', '/x/.codex/packages/standalone/bin/qwen', 'linux') is None
    assert cli_install.update_command('qwen', '/x/bin/qwen', 'linux') is None


def test_configured_custom_root_is_recognised_but_other_copy_is_not(layout, tmp_path, monkeypatch):
    wrapper, inner = layout('linux')
    custom = tmp_path / 'custom'
    custom_lib = custom / 'lib' / 'qwen-code'
    custom_lib.mkdir(parents=True)
    (custom_lib / 'manifest.json').write_text('{"name":"@qwen-code/qwen-code"}')
    assert cli_install.update_command('qwen', custom / 'bin' / 'qwen', 'linux') is None
    monkeypatch.setenv('QWEN_INSTALL_ROOT', str(custom))
    assert cli_install.install_method(custom / 'bin' / 'qwen', 'linux', 'qwen') == 'standalone'
    assert cli_install.update_command('qwen', wrapper, 'linux') is None


@pytest.fixture
def cvc():
    path = Path(__file__).resolve().parent.parent / 'tools' / 'cli-version-check.py'
    spec = importlib.util.spec_from_file_location('cvc_qwen', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize('platform', ['win32', 'darwin', 'linux'])
def test_tool_and_server_select_same_noninteractive_standalone_argv(layout, cvc, platform, monkeypatch):
    binary = layout(platform)[0]
    monkeypatch.setattr(cvc, '_install_method',
                        lambda p, name: cli_install.install_method(p, platform, name))
    cli = next(c for c in cvc.CLIS if c.name == 'qwen')
    argv, method = cvc.update_argv(cli, binary)
    assert argv == qwen.update_argv(method)
    command = cli_install.update_command('qwen', binary, platform)
    monkeypatch.setattr(cli_update.shutil, 'which', lambda n: n)
    server_argv, _, reason = cli_update.build_argv('qwen', binary, command, platform)
    assert not reason and server_argv == argv
    assert 'standalone' in argv[-1] and 'npm' not in argv[-1]
    if platform == 'win32':
        assert '-NonInteractive' in argv and '-NoProfile' in argv
    else:
        assert 'pipefail' in argv[-1] and '--method standalone' in argv[-1]


def test_tool_qwen_npm_and_unknown(cvc):
    cli = next(c for c in cvc.CLIS if c.name == 'qwen')
    assert cvc.update_argv(cli, '/x/node_modules/@qwen-code/qwen-code/bin/qwen') == (cli.update_cmd, 'npm')
    assert cvc.update_argv(cli, '/x/unknown/qwen') == (None, '')


@pytest.mark.parametrize('known', [True, False])
def test_tool_mocked_apply_never_falls_back_to_npm(layout, cvc, monkeypatch, known):
    path = layout('win32')[0] if known else Path('/unknown/qwen')
    monkeypatch.setattr(cvc, '_install_method',
                        lambda p, name: cli_install.install_method(p, 'win32', name))
    versions = iter(['0.23.4', '0.25.2'])
    monkeypatch.setattr(cvc, 'installed_version', lambda n: (next(versions), str(path)))
    monkeypatch.setattr(cvc, 'latest_version', lambda pkg: '0.25.2')
    monkeypatch.setattr(cvc, 'shadow_check', lambda *a: [])
    monkeypatch.setattr(cvc, '_npm_prefix', lambda: pytest.fail('standalone must not probe npm prefix'))
    monkeypatch.setattr(cvc.shutil, 'which', lambda n: n)
    calls = []
    monkeypatch.setattr(cvc, '_run', lambda argv, **kw: (calls.append(argv) or 0, 'stubbed'))
    cli = next(c for c in cvc.CLIS if c.name == 'qwen')
    row = cvc.check_one(cli, apply_updates=True)
    if known:
        assert row['updated'] and calls == [qwen.update_argv('standalone-windows')]
    else:
        assert not row['updated'] and calls == []
        assert 'not guessing' in row['update_skipped']


@pytest.mark.parametrize('current,latest,expected', [
    ('0.23.4', '0.25.2', True), ('0.25.2', '0.25.2', False),
    ('0.26.0', '0.25.2', False), ('bad', '0.25.2', False), ('0.23.4', None, False),
])
def test_health_exposes_button_only_for_known_newer_registry_version(layout, monkeypatch, current, latest, expected):
    from mc import agent_runtime
    binary = layout(cli_install.sys.platform)[0]
    rt = agent_runtime.QwenRuntime()
    monkeypatch.setattr(rt, 'resolve_binary', lambda: binary)
    monkeypatch.setattr(rt, '_qwen_auth_state', lambda: ('ok', 'test'))
    monkeypatch.setattr(agent_runtime.subprocess, 'run',
                        lambda *a, **kw: SimpleNamespace(stdout=current, stderr='', returncode=0))
    monkeypatch.setattr(cli_update, 'provider_summary', lambda n: {'latest': latest})
    health = rt.health_check()
    assert bool(health.update_hint) == expected
    assert bool(health.update_reason) == expected


def test_unknown_install_reports_newer_version_without_button(monkeypatch):
    monkeypatch.setattr(cli_update, 'provider_summary', lambda n: {'latest': '0.25.2'})
    hint, reason = qwen.update_advice('0.23.4', '/unknown/qwen')
    assert hint == '' and '0.25.2' in reason


def test_server_in_use_probe_covers_bundled_node_not_shared_wrapper_dir(layout):
    wrapper, inner = layout(cli_install.sys.platform)
    node = inner.parent.parent / 'node' / 'node.exe'
    processes = [{'pid': 1234, 'name': 'node.exe', 'exe': str(node)},
                 {'pid': 4567, 'name': 'unrelated.exe', 'exe': str(wrapper.parent / 'unrelated.exe')}]
    users, error = cli_update.os_users('qwen', wrapper, lambda: processes)
    assert not error and users == ['pid 1234 (node.exe)']
