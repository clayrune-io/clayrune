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
    assert dua.default_user_agent(chromium, platform_name='freebsd') is None
    os.remove(tmp_path / '151.0.7922.34.manifest')
    dua._cache.clear()
    assert dua.default_user_agent(chromium, platform_name='win32') is None


def test_args_off_when_disabled_or_hints_not_derivable(chromium, monkeypatch):
    assert dua.default_ua_args(chromium, enabled=False) == []
    monkeypatch.setattr(dua, '_windows_platform_version', lambda: None)
    assert dua.default_ua_args(chromium, platform_name='win32') == []


def test_args_carry_the_ua_on_windows(chromium, monkeypatch):
    monkeypatch.setattr(dua, '_windows_platform_version', lambda: '19.0.0')
    monkeypatch.setattr(dua, '_supported', lambda platform_name=None: True)
    args = dua.default_ua_args(chromium, platform_name='win32')
    assert args == [f"--user-agent={dua.default_user_agent(chromium, platform_name='win32')}"]


def test_fill_puts_back_what_the_switch_blanked(chromium, monkeypatch):
    monkeypatch.setattr(dua, '_windows_platform_version', lambda: '19.0.0')
    low = {'brands': [{'brand': 'Chromium', 'version': '151'},
                      {'brand': 'Not=A?Brand', 'version': '99'}]}
    # What Chromium 151 returned natively under --user-agent (measured).
    blank = {'architecture': '', 'bitness': '', 'platformVersion': '',
             'uaFullVersion': '', 'fullVersionList': [], 'model': '', 'wow64': False}
    hi = dua.fill_high_entropy(blank, low, chromium, platform_name='win32')
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


# --- Linux x86_64 (5cb6828e). Expected values are what headed Chromium
# 151.0.7922.34 reported on Ubuntu 24.04 / WSL2 x86_64, measured 2026-10-14.

@pytest.fixture
def linux_chromium(tmp_path, monkeypatch):
    """A Linux build: no manifest, the version comes from `chrome --version`."""
    exe = tmp_path / 'chrome'
    exe.write_text('')
    dua._cache.clear()
    calls = []

    def run(argv, **kw):
        calls.append(argv)
        return dua.subprocess.CompletedProcess(argv, 0, 'Google Chrome for Testing 151.0.7922.34 \n', '')
    monkeypatch.setattr(dua.subprocess, 'run', run)
    monkeypatch.setattr(dua.platform, 'machine', lambda: 'x86_64')
    monkeypatch.setattr(dua, '_windows_platform_version',
                        lambda: pytest.fail('winreg consulted off Windows'))
    return str(exe), calls


def test_linux_ua_is_chromiums_reduced_linux_ua(linux_chromium):
    exe, calls = linux_chromium
    ua = dua.default_user_agent(exe, platform_name='linux')
    assert ua == ('Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36')
    assert dua.default_ua_args(exe, platform_name='linux') == [f'--user-agent={ua}']
    # Read once per binary, not once per launch.
    assert calls == [[exe, '--version']]


def test_version_flag_never_runs_on_windows(tmp_path, monkeypatch):
    exe = tmp_path / 'chrome.exe'
    exe.write_text('')
    dua._cache.clear()
    monkeypatch.setattr(dua.subprocess, 'run', lambda *a, **k: pytest.fail('chrome.exe --version opens a browser'))
    assert dua.default_user_agent(str(exe), platform_name='win32') is None


def test_linux_no_flag_when_version_unreadable_or_not_x86(linux_chromium, monkeypatch):
    exe, _ = linux_chromium
    monkeypatch.setattr(dua.subprocess, 'run', lambda *a, **k: (_ for _ in ()).throw(OSError('no libnss3')))
    assert dua.default_ua_args(exe, platform_name='linux') == []
    dua._cache.clear()
    monkeypatch.setattr(dua.platform, 'machine', lambda: 'aarch64')
    assert dua.default_ua_args(exe, platform_name='linux') == []


def test_linux_fill_matches_headed_chromium(linux_chromium):
    exe, _ = linux_chromium
    low = {'brands': [{'brand': 'Chromium', 'version': '151'},
                      {'brand': 'Not=A?Brand', 'version': '99'}], 'mobile': False}
    # What Chromium 151 returned natively under --user-agent on Linux (measured).
    blank = {'architecture': '', 'bitness': '', 'platformVersion': '', 'uaFullVersion': '',
             'fullVersionList': [], 'formFactors': [], 'model': '', 'wow64': False}
    hi = dua.fill_high_entropy(blank, low, exe, platform_name='linux')
    assert hi == {'architecture': 'x86', 'bitness': '64', 'platformVersion': '',
                  'uaFullVersion': '151.0.7922.34', 'formFactors': ['Desktop'],
                  'fullVersionList': [{'brand': 'Chromium', 'version': '151.0.7922.34'},
                                      {'brand': 'Not=A?Brand', 'version': '99.0.0.0'}],
                  'model': '', 'wow64': False}


def test_form_factors_stay_unfilled_on_windows_and_mobile(chromium, linux_chromium, monkeypatch):
    monkeypatch.setattr(dua, '_windows_platform_version', lambda: '19.0.0')
    assert dua.fill_high_entropy({'formFactors': []}, {'brands': []}, chromium,
                                 platform_name='win32')['formFactors'] == []
    exe, _ = linux_chromium
    assert dua.fill_high_entropy({'formFactors': []}, {'brands': [], 'mobile': True}, exe,
                                 platform_name='linux')['formFactors'] == []


def test_ua_metadata_carries_form_factors_only_when_set():
    from mc.blueprints import browser_routes as br
    low = {'brands': [{'brand': 'Chromium', 'version': '151'}], 'platform': 'Linux', 'mobile': False}
    assert br._ua_metadata(low, {'formFactors': ['Desktop']})['formFactors'] == ['Desktop']
    assert 'formFactors' not in br._ua_metadata(low, {'formFactors': []})
    session = {'ua_override': {'userAgent': 'Mozilla/5.0 (X11; Linux x86_64) Chrome/151.0.0.0',
                               'userAgentMetadata': br._ua_metadata(low, {'formFactors': ['Desktop']})}}
    assert 'formFactors' not in br._mobile_ua_override(session)['userAgentMetadata']


# --- macOS (5cb6828e). Expected values are what headed Chromium 151.0.7922.34
# reported on macOS 26.5.1 arm64, measured 2026-10-14.

@pytest.fixture
def mac_chromium(tmp_path, monkeypatch):
    """A macOS build: the version comes from `chrome --version`, the OS version
    from /usr/bin/sw_vers. `sw` is the sw_vers output (a str, or an exception)."""
    exe = tmp_path / 'Google Chrome for Testing'
    exe.write_text('')
    dua._cache.clear()
    state = {'sw': '26.5.1\n', 'calls': []}

    def run(argv, **kw):
        state['calls'].append(argv)
        if argv[0] == dua._SW_VERS:
            if isinstance(state['sw'], Exception):
                raise state['sw']
            return dua.subprocess.CompletedProcess(argv, 0, state['sw'], '')
        return dua.subprocess.CompletedProcess(argv, 0, 'Google Chrome for Testing 151.0.7922.34 \n', '')
    monkeypatch.setattr(dua.subprocess, 'run', run)
    monkeypatch.setattr(dua.platform, 'machine', lambda: 'arm64')
    monkeypatch.setattr(dua, '_windows_platform_version',
                        lambda: pytest.fail('winreg consulted off Windows'))
    return str(exe), state


@pytest.mark.parametrize('machine', ['arm64', 'x86_64'])
def test_mac_ua_is_the_frozen_intel_token_on_both_arches(mac_chromium, monkeypatch, machine):
    exe, _ = mac_chromium
    monkeypatch.setattr(dua.platform, 'machine', lambda: machine)
    ua = dua.default_user_agent(exe, platform_name='darwin')
    assert ua == ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36')
    assert dua.default_ua_args(exe, platform_name='darwin') == [f'--user-agent={ua}']


def test_mac_platform_version_is_sw_vers_by_absolute_path_padded_to_three_parts(mac_chromium):
    _, state = mac_chromium
    assert dua._platform_version('darwin') == '26.5.1'
    assert state['calls'] == [['/usr/bin/sw_vers', '-productVersion']]
    state['sw'] = '15.0\n'
    assert dua._platform_version('darwin') == '15.0.0'
    state['sw'] = '15\n'
    assert dua._platform_version('darwin') == '15.0.0'


def test_mac_platform_version_falls_back_to_mac_ver_then_gives_up(mac_chromium, monkeypatch):
    exe, state = mac_chromium
    state['sw'] = OSError('no /usr/bin/sw_vers')
    monkeypatch.setattr(dua.platform, 'mac_ver', lambda: ('14.4', ('', '', ''), 'arm64'))
    assert dua._platform_version('darwin') == '14.4.0'
    state['sw'] = 'garbage\n'
    assert dua._platform_version('darwin') == '14.4.0'
    monkeypatch.setattr(dua.platform, 'mac_ver', lambda: ('', ('', '', ''), ''))
    assert dua._platform_version('darwin') is None
    # No derivable hints, so no flag: the switch would blank them for good.
    assert dua.default_ua_args(exe, platform_name='darwin') == []


def test_mac_fill_matches_headed_chromium(mac_chromium):
    exe, _ = mac_chromium
    low = {'brands': [{'brand': 'Chromium', 'version': '151'},
                      {'brand': 'Not=A?Brand', 'version': '99'}], 'mobile': False}
    blank = {'architecture': '', 'bitness': '', 'platformVersion': '', 'uaFullVersion': '',
             'fullVersionList': [], 'formFactors': [], 'model': '', 'wow64': False}
    assert dua.fill_high_entropy(blank, low, exe, platform_name='darwin') == {
        'architecture': 'arm', 'bitness': '64', 'platformVersion': '26.5.1',
        'uaFullVersion': '151.0.7922.34',
        'fullVersionList': [{'brand': 'Chromium', 'version': '151.0.7922.34'},
                            {'brand': 'Not=A?Brand', 'version': '99.0.0.0'}],
        'formFactors': ['Desktop'], 'model': '', 'wow64': False}


def test_mac_fill_reports_x86_on_intel(mac_chromium, monkeypatch):
    exe, _ = mac_chromium
    monkeypatch.setattr(dua.platform, 'machine', lambda: 'x86_64')
    assert dua.fill_high_entropy({}, {'brands': []}, exe, platform_name='darwin')['architecture'] == 'x86'
