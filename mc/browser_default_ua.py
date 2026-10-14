"""The pane's process-wide default User-Agent, set at launch (MC-1090).

`_start_ua_guard` strips "HeadlessChrome" from every target it holds, but a
shared or service worker's ``navigator.userAgent`` is fixed when the worker is
created, from Chromium's DEFAULT UA, and no CDP override reaches it (measured
2026-10-13 on Chromium 151 --headless=new: Network.setUserAgentOverride and
Emulation.setUserAgentOverride on a held worker both return ``{}`` and the
worker still reports HeadlessChrome; only its request header changes).

That mattered for X: x.com's login page loads Castle (abs.twimg.com
``castle.umd-*.js``), whose blob: SharedWorker posts ``navigator.userAgent``
back to the page, and X's Jetfuel forms send the resulting ``$castle_token``
with every jf.x.com submit, the Sign in with Google one included. In the pane
that worker said ``HeadlessChrome/151.0.0.0``; in Chrome it says
``Chrome/154.0.0.0``.

``--user-agent`` changes the default itself, so every context agrees. But
with that switch Chromium reports EMPTY high-entropy client hints natively
(architecture, bitness, platformVersion, uaFullVersion, fullVersionList all
"", formFactors [], measured), and the guard copies the native values into
its override. So `fill_high_entropy` puts back what Chromium would have
reported without the switch, derived from the binary and the OS, and only
where the native read came back blank.

Each supported OS was checked against headed Chromium 151.0.7922.34 on that OS:

- Windows 11 build 26200: 19.0.0 / x86 / 64 / 151.0.7922.34.
- Linux x86_64 (Ubuntu 24.04 on WSL2, kernel 6.6.87, 2026-10-14):
  platformVersion is "" natively, headed or not (Chromium does not report a
  Linux version), x86 / 64 / 151.0.7922.34, formFactors ["Desktop"]. Linux
  builds ship no version manifest; ``chrome --version`` prints it there.

- macOS 26.5.1 arm64 (Darwin 25.5, 2026-10-14): the UA token is the frozen
  "Macintosh; Intel Mac OS X 10_15_7" even on Apple Silicon, navigator.platform
  MacIntel, architecture "arm", bitness 64, platformVersion "26.5.1" (what
  ``sw_vers -productVersion`` prints), 151.0.7922.34, formFactors ["Desktop"].
  Headless without the switch reports HeadlessChrome in all four contexts.

Linux on ARM is not measured yet, so `default_ua_args` returns no flag there,
which is the old behaviour.
"""

from __future__ import annotations

import glob
import os
import platform
import re
import subprocess
import sys

_VERSION_RE = re.compile(r'(\d+)\.(\d+)\.(\d+)\.(\d+)')
# Chromium's reduced UA platform token, fixed since M110 (measured per OS).
_UA_TOKENS = {
    'win32': 'Windows NT 10.0; Win64; x64',
    'linux': 'X11; Linux x86_64',
    # Frozen at 10_15_7 and "Intel" on Apple Silicon too (measured on arm64).
    'darwin': 'Macintosh; Intel Mac OS X 10_15_7',
}
# GUI launchd hands apps a minimal PATH, so sw_vers is called by absolute path.
_SW_VERS = '/usr/bin/sw_vers'
_MAC_VERSION_RE = re.compile(r'\d+(\.\d+){0,2}')
_cache: dict[tuple[str, float], str | None] = {}


def _platform(platform_name: str | None = None) -> str:
    return platform_name or sys.platform


def _manifest_version(chromium: str) -> str | None:
    """Windows builds ship a "<full version>.manifest" next to chrome.exe."""
    for path in glob.glob(os.path.join(os.path.dirname(chromium), '*.manifest')):
        name = os.path.basename(path)[:-len('.manifest')]
        if _VERSION_RE.fullmatch(name):
            return name
    return None


def _version_flag(chromium: str) -> str | None:
    """``chrome --version`` ("Google Chrome for Testing 151.0.7922.34"). Never
    on Windows: there chrome.exe --version opens a browser instead of printing."""
    try:
        out = subprocess.run([chromium, '--version'], stdin=subprocess.DEVNULL,
                             capture_output=True, text=True, timeout=10).stdout
    except Exception as e:
        print(f'[browser] chromium --version failed: {e}', flush=True)
        return None
    m = _VERSION_RE.search(out or '')
    return m.group(0) if m else None


def _full_version(chromium: str, platform_name: str | None = None) -> str | None:
    """Chromium's full version (151.0.7922.34), read once per binary."""
    try:
        key = (chromium, os.path.getmtime(chromium))
    except OSError:
        return None
    if key not in _cache:
        found = _manifest_version(chromium)
        if not found and _platform(platform_name) != 'win32':
            found = _version_flag(chromium)
        _cache[key] = found
    return _cache[key]


def _windows_platform_version() -> str | None:
    """What Chromium reports as Sec-CH-UA-Platform-Version on Windows: the
    UniversalApiContract version as "major.minor.0"."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r'SOFTWARE\Microsoft\WindowsRuntime\WellKnownContracts') as k:
            v, _ = winreg.QueryValueEx(k, 'Windows.Foundation.UniversalApiContract')
        return f'{int(v) >> 16}.{int(v) & 0xffff}.0'
    except Exception as e:
        print(f'[browser] platformVersion lookup failed: {e}', flush=True)
        return None


def _macos_platform_version() -> str | None:
    """What Chromium reports as Sec-CH-UA-Platform-Version on macOS: the
    product version padded to three parts ("15.0" -> "15.0.0", "26.5.1")."""
    raw = ''
    try:
        raw = subprocess.run([_SW_VERS, '-productVersion'], stdin=subprocess.DEVNULL,
                             capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception as e:
        print(f'[browser] sw_vers failed: {e}', flush=True)
    if not _MAC_VERSION_RE.fullmatch(raw):
        raw = platform.mac_ver()[0]
    if not _MAC_VERSION_RE.fullmatch(raw):
        print('[browser] macOS platformVersion lookup failed', flush=True)
        return None
    parts = raw.split('.')
    return '.'.join(parts + ['0'] * (3 - len(parts)))


def _platform_version(platform_name: str | None = None) -> str | None:
    """platformVersion as Chromium reports it natively, or None when it cannot
    be derived. "" is a real value: Chromium reports no Linux version."""
    name = _platform(platform_name)
    if name == 'win32':
        return _windows_platform_version()
    if name == 'darwin':
        return _macos_platform_version()
    return ''


def _supported(platform_name: str | None = None) -> bool:
    name = _platform(platform_name)
    if name == 'linux':
        # Only x86_64 was measured; an ARM build may carry another token.
        return platform.machine().lower() in ('x86_64', 'amd64')
    # darwin: the token is the same on both arches; the hints come from the machine.
    return name in ('win32', 'darwin')


def default_user_agent(chromium: str, platform_name: str | None = None) -> str | None:
    """The UA Chromium would send if it were not headless, or None."""
    if not _supported(platform_name):
        return None
    full = _full_version(chromium, platform_name)
    if not full:
        return None
    major = full.split('.')[0]
    return (f'Mozilla/5.0 ({_UA_TOKENS[_platform(platform_name)]}) AppleWebKit/537.36 '
            f'(KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36')


def default_ua_args(chromium: str, enabled: bool = True,
                    platform_name: str | None = None) -> list[str]:
    """``--user-agent=...`` for a pane launch, or [] (off, unsupported OS, or
    the version could not be read, all of which keep the old behaviour). The
    hints have to be derivable too, or the switch would blank them for good."""
    if not enabled or not _supported(platform_name) or _platform_version(platform_name) is None:
        return []
    ua = default_user_agent(chromium, platform_name)
    return [f'--user-agent={ua}'] if ua else []


def fill_high_entropy(high: dict | None, low: dict | None, chromium: str | None,
                      platform_name: str | None = None) -> dict:
    """Native getHighEntropyValues() result with the fields the --user-agent
    switch blanked put back. Fields the native read did return are kept."""
    out = dict(high or {})
    full = _full_version(chromium, platform_name) if chromium else None
    if not full:
        return out
    major = full.split('.')[0]
    machine = platform.machine().lower()
    derived = {
        'architecture': 'arm' if machine.startswith(('arm', 'aarch')) else 'x86',
        'bitness': '64' if sys.maxsize > 2 ** 32 else '32',
        'platformVersion': _platform_version(platform_name) or '',
        'uaFullVersion': full,
        # Chromium's own brand carries the full version; GREASE brands carry
        # "<version>.0.0.0" (measured: Not=A?Brand 99 -> 99.0.0.0).
        'fullVersionList': [
            {'brand': b.get('brand') or '',
             'version': full if str(b.get('version')) == major else f"{b.get('version')}.0.0.0"}
            for b in ((low or {}).get('brands') or [])],
    }
    if _platform(platform_name) != 'win32' and not (low or {}).get('mobile'):
        # Measured ["Desktop"] on headed Linux. Not filled on Windows, where
        # the pane has reported [] since 362f4d2d (left as it is there).
        derived['formFactors'] = ['Desktop']
    for k, v in derived.items():
        if not out.get(k) and v:
            out[k] = v
    out.setdefault('model', '')
    out.setdefault('wow64', False)
    return out
