"""mc/browser_default_ua.py: the pane's launch-time default UA (MC-1090)."""

import os

import pytest

from mc import browser_default_ua as dua


@pytest.fixture
def chromium(tmp_path):
    exe = tmp_path / 'chrome.exe'
    exe.write_text('')
    (tmp_path / '151.0.7922.34.manifest').write_text('')
    dua._cache.clear()
    return str(exe)


def test_default_ua_is_chromes_reduced_ua_without_headless(chromium):
    ua = dua.default_user_agent(chromium, platform_name='win32')
    assert ua == ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36')
    assert 'Headless' not in ua


def test_no_flag_off_windows_or_without_a_version(chromium, tmp_path):
    assert dua.default_user_agent(chromium, platform_name='darwin') is None
    assert dua.default_user_agent(chromium, platform_name='linux') is None
    os.remove(tmp_path / '151.0.7922.34.manifest')
    dua._cache.clear()
    assert dua.default_user_agent(chromium, platform_name='win32') is None


def test_args_off_when_disabled_or_hints_not_derivable(chromium, monkeypatch):
    assert dua.default_ua_args(chromium, enabled=False) == []
    monkeypatch.setattr(dua, '_windows_platform_version', lambda: None)
    assert dua.default_ua_args(chromium) == []


def test_args_carry_the_ua_on_windows(chromium, monkeypatch):
    monkeypatch.setattr(dua, '_windows_platform_version', lambda: '19.0.0')
    monkeypatch.setattr(dua, '_supported', lambda platform_name=None: True)
    args = dua.default_ua_args(chromium)
    assert args == [f"--user-agent={dua.default_user_agent(chromium, platform_name='win32')}"]


def test_fill_puts_back_what_the_switch_blanked(chromium, monkeypatch):
    monkeypatch.setattr(dua, '_windows_platform_version', lambda: '19.0.0')
    low = {'brands': [{'brand': 'Chromium', 'version': '151'},
                      {'brand': 'Not=A?Brand', 'version': '99'}]}
    # What Chromium 151 returned natively under --user-agent (measured).
    blank = {'architecture': '', 'bitness': '', 'platformVersion': '',
             'uaFullVersion': '', 'fullVersionList': [], 'model': '', 'wow64': False}
    hi = dua.fill_high_entropy(blank, low, chromium)
    assert hi['platformVersion'] == '19.0.0'
    assert hi['uaFullVersion'] == '151.0.7922.34'
    assert hi['fullVersionList'] == [{'brand': 'Chromium', 'version': '151.0.7922.34'},
                                     {'brand': 'Not=A?Brand', 'version': '99.0.0.0'}]
    assert hi['architecture'] in ('x86', 'arm') and hi['bitness'] in ('32', '64')
    assert hi['model'] == '' and hi['wow64'] is False


def test_fill_keeps_native_values(chromium):
    native = {'architecture': 'arm', 'bitness': '64', 'platformVersion': '15.0.0',
              'uaFullVersion': '151.0.1.2', 'fullVersionList': [{'brand': 'X', 'version': '1'}]}
    assert dua.fill_high_entropy(native, {'brands': []}, chromium)['uaFullVersion'] == '151.0.1.2'
    out = dua.fill_high_entropy(native, {'brands': []}, chromium)
    assert {k: out[k] for k in native} == native


def test_fill_is_a_no_op_without_a_version():
    assert dua.fill_high_entropy({'a': 1}, {}, None) == {'a': 1}
