"""Portable refusal/preflight contract; installed-Aider proof lives separately."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from mc import aider_launch as launch, aider_source_pin as pin, cli_install, state, vendor_fence


@pytest.mark.parametrize('value', [None, False, 'true', 1])
def test_flag_requires_explicit_boolean_opt_in(monkeypatch, value):
    monkeypatch.setitem(state.CONFIG, launch.FLAG, value)
    monkeypatch.setitem(state.CONFIG, 'fence_unattended_enabled', True)
    with pytest.raises(vendor_fence.VendorFenceError, match='no pre-execution hook'):
        vendor_fence.check_launch('aider', {'trigger_type': 'schedule'})


def test_preflight_uses_only_selected_interpreter_and_isolated_runner(monkeypatch, tmp_path):
    monkeypatch.setitem(state.CONFIG, launch.FLAG, True)
    monkeypatch.setattr(cli_install, 'sibling_python', lambda path: 'selected-python')
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout=json.dumps(
            {'restricted_aider': '0.86.2', 'guard': 'ready'}), stderr='')

    monkeypatch.setattr(launch.subprocess, 'run', run)
    cmd = launch.prepare(['selected-aider', '--model', 'test'], str(tmp_path), {'TEST': '1'})
    assert cmd[:3] == ['selected-python', '-I', '-B']
    assert Path(cmd[3]).name == 'aider_runner.py'
    assert cmd[4:] == ['--model', 'test']
    assert calls[0][0] == cmd[:4] + ['--self-test']
    assert calls[0][1]['cwd'] == str(tmp_path)
    assert calls[0][1]['env'] == {'TEST': '1'}


@pytest.mark.parametrize('result', [
    SimpleNamespace(returncode=2, stdout='', stderr='Aider source drift'),
    SimpleNamespace(returncode=0, stdout='not JSON', stderr=''),
    SimpleNamespace(returncode=0, stdout='{}', stderr=''),
])
def test_failed_or_invalid_preflight_never_returns_a_launch(monkeypatch, tmp_path, result):
    monkeypatch.setitem(state.CONFIG, launch.FLAG, True)
    monkeypatch.setattr(cli_install, 'sibling_python', lambda path: 'selected-python')
    monkeypatch.setattr(launch.subprocess, 'run', lambda *a, **kw: result)
    with pytest.raises(RuntimeError, match='preflight'):
        launch.prepare(['selected-aider'], str(tmp_path), {})


def test_unknown_install_and_frozen_build_refuse(monkeypatch, tmp_path):
    monkeypatch.setitem(state.CONFIG, launch.FLAG, True)
    monkeypatch.setattr(cli_install, 'sibling_python', lambda path: None)
    with pytest.raises(RuntimeError, match='interpreter'):
        launch.prepare(['aider'], str(tmp_path), {})
    monkeypatch.setattr(launch.sys, 'frozen', True, raising=False)
    with pytest.raises(RuntimeError, match='frozen'):
        launch.prepare(['aider'], str(tmp_path), {})


@pytest.mark.parametrize('name', ['aider-chat', 'GitPython', 'litellm'])
def test_version_drift_refuses_before_importing_aider(monkeypatch, name):
    monkeypatch.setattr(pin, 'sys', SimpleNamespace(
        implementation=SimpleNamespace(name='cpython'), version_info=(3, 12)))
    def version(package):
        if package == name:
            return 'unexpected'
        return pin.VERSION if package == 'aider-chat' else pin.DEPENDENCIES[package]
    monkeypatch.setattr(pin.importlib.metadata, 'version', version)
    with pytest.raises(RuntimeError, match='drift'):
        pin.verify_installed()
