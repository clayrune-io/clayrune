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
"", measured), and the guard copies the native values into its override. So
`fill_high_entropy` puts back what Chromium would have reported without the
switch, derived from the binary and the OS, and only where the native read
came back blank.

Windows only for now: that is where every derived value was checked against
the native one (Chromium 151, Windows 11 build 26200: 19.0.0 / x86 / 64 /
151.0.7922.34). Elsewhere `default_ua_args` returns no flag, which is the old
behaviour, until macOS and Linux platformVersion are measured the same way.
"""

from __future__ import annotations

import glob
import os
import platform
import re
import sys

_VERSION_RE = re.compile(r'(\d+)\.(\d+)\.(\d+)\.(\d+)')
_WIN_TOKEN = 'Windows NT 10.0; Win64; x64'   # Chromium's reduced UA, fixed since M110
_cache: dict[tuple[str, float], str | None] = {}


def _full_version(chromium: str) -> str | None:
    """Chromium's full version (151.0.7922.34), without starting it. Windows
    builds ship a "<full version>.manifest" next to chrome.exe; chrome.exe
    --version opens a browser on Windows instead of printing."""
    try:
        key = (chromium, os.path.getmtime(chromium))
    except OSError:
        return None
    if key not in _cache:
        found = None
        for path in glob.glob(os.path.join(os.path.dirname(chromium), '*.manifest')):
            if _VERSION_RE.fullmatch(os.path.basename(path)[:-len('.manifest')]):
                found = os.path.basename(path)[:-len('.manifest')]
                break
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


def _supported(platform_name: str | None = None) -> bool:
    return (platform_name or sys.platform) == 'win32'


def default_user_agent(chromium: str, platform_name: str | None = None) -> str | None:
    """The UA Chromium would send if it were not headless, or None."""
    if not _supported(platform_name):
        return None
    full = _full_version(chromium)
    if not full:
        return None
    major = full.split('.')[0]
    return (f'Mozilla/5.0 ({_WIN_TOKEN}) AppleWebKit/537.36 (KHTML, like Gecko) '
            f'Chrome/{major}.0.0.0 Safari/537.36')


def default_ua_args(chromium: str, enabled: bool = True) -> list[str]:
    """``--user-agent=...`` for a pane launch, or [] (off, unsupported OS, or
    the version could not be read, all of which keep the old behaviour). The
    hints have to be derivable too, or the switch would blank them for good."""
    if not enabled or _windows_platform_version() is None:
        return []
    ua = default_user_agent(chromium)
    return [f'--user-agent={ua}'] if ua else []


def fill_high_entropy(high: dict | None, low: dict | None, chromium: str | None) -> dict:
    """Native getHighEntropyValues() result with the fields the --user-agent
    switch blanked put back. Fields the native read did return are kept."""
    out = dict(high or {})
    full = _full_version(chromium) if chromium else None
    if not full:
        return out
    major = full.split('.')[0]
    machine = platform.machine().lower()
    derived = {
        'architecture': 'arm' if machine.startswith(('arm', 'aarch')) else 'x86',
        'bitness': '64' if sys.maxsize > 2 ** 32 else '32',
        'platformVersion': _windows_platform_version() or '',
        'uaFullVersion': full,
        # Chromium's own brand carries the full version; GREASE brands carry
        # "<version>.0.0.0" (measured: Not=A?Brand 99 -> 99.0.0.0).
        'fullVersionList': [
            {'brand': b.get('brand') or '',
             'version': full if str(b.get('version')) == major else f"{b.get('version')}.0.0.0"}
            for b in ((low or {}).get('brands') or [])],
    }
    for k, v in derived.items():
        if not out.get(k) and v:
            out[k] = v
    out.setdefault('model', '')
    out.setdefault('wow64', False)
    return out
