"""Opt-in installed-CLI loader/runner proof. Never starts a model turn.

Set CLAYRUNE_TEST_INSTALLED_FENCES=1 on the measured Windows install.
These are installation evidence, separate from portable boundary tests.
"""
import os

import pytest

from mc import agent_runtime as ar, guardrail_hooks as gh, state, vendor_fence as vf

pytestmark = pytest.mark.skipif(
    os.environ.get('CLAYRUNE_TEST_INSTALLED_FENCES') != '1',
    reason='opt-in installed vendor loader/runner checks')


@pytest.fixture
def native_env(tmp_path, monkeypatch):
    monkeypatch.setitem(state.CONFIG, 'fence_unattended_enabled', True)
    monkeypatch.setattr(gh, 'hooks_dir', lambda: tmp_path / 'hooks')
    env = dict(os.environ)
    for key in vf.SETTINGS_ENV.values():
        env.pop(key, None)
    env.pop('QWEN_CODE_SAFE_MODE', None)
    # Match the runtime's real shell pinning before probing native loading.
    ar._pin_qwen_windows_shell(env)
    return tmp_path, env


def test_installed_qwen_loads_and_blocks(native_env):
    cwd, env = native_env
    binary = ar.get_runtime('qwen').resolve_binary()
    if binary is None:
        pytest.skip('Qwen CLI is not installed')
    vf.prepare_env('qwen', {'trigger_type': 'schedule'}, env, binary=binary, cwd=str(cwd))
    assert '-fence-' in env[vf.SETTINGS_ENV['qwen']]


@pytest.mark.skipif(os.name != 'nt', reason='measured Gemini 0.66 Windows ownership gate')
def test_installed_gemini_refuses_ignored_system_settings(native_env):
    cwd, env = native_env
    binary = ar.get_runtime('gemini').resolve_binary()
    if binary is None:
        pytest.skip('Gemini CLI is not installed')
    session = {'trigger_type': 'schedule'}
    with pytest.raises(vf.VendorFenceError, match='ownership/permissions'):
        vf.prepare_env('gemini', session, env, binary=binary, cwd=str(cwd))
    assert session['status'] == 'error'
    assert vf.SETTINGS_ENV['gemini'] not in env


def test_installed_qwen_safe_mode_refuses(native_env):
    cwd, env = native_env
    binary = ar.get_runtime('qwen').resolve_binary()
    if binary is None:
        pytest.skip('Qwen CLI is not installed')
    env['QWEN_CODE_SAFE_MODE'] = '1'
    with pytest.raises(vf.VendorFenceError, match='safe-mode hook suppression'):
        vf.prepare_env('qwen', {'trigger_type': 'schedule'}, env, binary=binary, cwd=str(cwd))
