# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for the macOS Clayrune build.
#
# Differs from the (unchecked-in) Windows build.spec in three ways:
#   1. No pythonnet / .NET / WebView2 — macOS pywebview uses Cocoa/WKWebView.
#   2. Bundles the Cocoa platform module via collect_submodules('webview'),
#      same dynamic-import gotcha as Windows' winforms module (BUILD_INSTRUCTIONS.md
#      §Critical: build.spec Hidden Imports).
#   3. BUNDLE step at the end produces a real .app for double-click launch.
#
# Build locally on a Mac (or via .github/workflows/build-macos.yml):
#   pyinstaller build-macos.spec --noconfirm
#
# Output: dist/Clayrune.app  →  zip into MissionControl-macOS.zip for release.

import os

from PyInstaller.utils.hooks import collect_submodules, collect_data_files

# This spec lives in installer/, one level below the repo root, but every path
# below names a repo-root-relative file. PyInstaller resolves a spec's relative
# paths against the SPEC'S OWN directory, NOT the invoking shell's CWD — so
# after the spec moved out of the root, `['app.py']` resolved to
# installer/app.py and the build died with "script not found" (caught on a real
# Mac build 2026-09-02, after a source reading wrongly concluded CWD applied).
# SPECPATH is PyInstaller's own injected variable for exactly this; R() makes
# every path absolute so the build is CWD-independent.
REPO_ROOT = os.path.dirname(SPECPATH)  # noqa: F821 — injected by PyInstaller


def R(*parts):
    return os.path.join(REPO_ROOT, *parts)


block_cipher = None

# pywebview imports its Cocoa backend dynamically inside guilib.py — static
# analysis misses it, so the native window silently fails to open and the
# app falls back to opening Safari. collect_submodules('webview') is the
# same fix Windows uses for winforms (see BUILD_INSTRUCTIONS.md).
hidden = []
hidden += collect_submodules('webview')
hidden += collect_submodules('flask')
# firebase-admin pulls grpc dynamically; missing submodules surface as
# silent push-failure at runtime, not a build error.
hidden += collect_submodules('firebase_admin')
hidden += collect_submodules('google')

# Bundle templates / static / data scaffolding next to app.py so the frozen
# binary sees the same layout as `python app.py`.
datas = [
    (R('static'), 'static'),
    # Claydo mascot webp/icons live in assets/ and are served by the
    # /assets/<file> Flask route. Bundle them or the UI shows broken images
    # (the FAB + the agent avatar) in the frozen app.
    (R('assets'), 'assets'),
    (R('installer', 'clayrune.png'), 'installer'),
    # Injected into every agent's system prompt by _clayrune_api_reference().
    # Unlike SHARED_RULES.md below this is NOT user data — it's the curated,
    # operator-neutral Clayrune API doc. Leave it out and the frozen app's
    # agents curl-probe endpoints every session.
    (R('data', 'agent_reference'), 'data/agent_reference'),
]

# SHARED_RULES.md is deliberately NOT bundled. It is user data — read verbatim
# into every agent's system prompt on every project — and it stays on disk in
# the builder's checkout even though it is gitignored. Bundling it "if present"
# baked the builder's personal working preferences into the shipped .app for
# every downstream user. A fresh install starts with no shared rules; the user
# writes their own via the Rules editor.

# Claydo reads these from _SERVER_DIR at runtime: USER_GUIDE + CHANGELOG feed
# ask-mode context; docs/claydo/ holds the builder-mode briefs
# (PROMPT_BUILDER_DESIGN.md §5). Without them the frozen app's Claydo 500s.
if os.path.exists(R('docs', 'USER_GUIDE.md')):
    datas.append((R('docs', 'USER_GUIDE.md'), 'docs'))
if os.path.exists(R('CHANGELOG.md')):
    datas.append((R('CHANGELOG.md'), '.'))
if os.path.isdir(R('docs', 'claydo')):
    datas.append((R('docs', 'claydo'), 'docs/claydo'))

# Include any extra Python modules the app loads from the repo root.
# server.py is implicitly bundled because app.py imports it.
datas += collect_data_files('webview')

a = Analysis(
    [R('app.py')],
    pathex=[REPO_ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Windows-only — fails to import on macOS and pulls nothing useful.
        'pythonnet',
        'clr',
        'clr_loader',
        'winreg',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Clayrune',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # GUI app — no Terminal window
    disable_windowed_traceback=False,
    target_arch=None,  # let host arch decide (CI runners are arm64)
    codesign_identity=None,  # unsigned per project policy
    entitlements_file=None,
    icon=R('installer', 'clayrune.png'),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='Clayrune',
)

app = BUNDLE(
    coll,
    name='Clayrune.app',
    icon=R('installer', 'clayrune.png'),
    bundle_identifier='io.clayrune.app',
    info_plist={
        'CFBundleName': 'Clayrune',
        'CFBundleDisplayName': 'Clayrune',
        'CFBundleShortVersionString': '1.5.1',
        'CFBundleVersion': '1.5.1',
        'LSMinimumSystemVersion': '11.0',
        'NSHighResolutionCapable': True,
        # Network access — server binds to localhost:5199 inside the app.
        'NSAppTransportSecurity': {'NSAllowsLocalNetworking': True},
    },
)
