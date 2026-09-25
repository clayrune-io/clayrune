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

import datetime
import json
import os
import subprocess
import tempfile

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
# Hook entry (MC-975): vendor CLIs run safety hooks as
# `Clayrune --clayrune-hook <name>` because a frozen build has no python to
# run steward/fence.py or mc/process_guard.py with. app.py imports these
# inside an `if`; naming them here keeps a missing module a build-time fact,
# not a fence that silently cannot start (tests/test_frozen_hook_entry.py).
hidden += ['mc.hook_entry', 'steward.fence', 'mc.process_guard']

# Bundle templates / static / data scaffolding next to app.py so the frozen
# binary sees the same layout as `python app.py`.
datas = [
    (R('static'), 'static'),
    # Claydo mascot webp/icons live in assets/ and are served by the
    # /assets/<file> Flask route. Bundle them or the UI shows broken images
    # (the FAB + the agent avatar) in the frozen app.
    (R('assets'), 'assets'),
    (R('installer', 'clayrune.png'), 'installer'),
    # Keep the safe uninstaller available inside the signed app bundle. The
    # public download page links the same source for users who want a standalone
    # copy; bundling it also makes every frozen release self-contained.
    (R('installer', 'uninstall-macos.command'), 'installer'),
    # Injected into every agent's system prompt by _clayrune_api_reference().
    # Unlike SHARED_RULES.md below this is NOT user data — it's the curated,
    # operator-neutral Clayrune API doc. Leave it out and the frozen app's
    # agents curl-probe endpoints every session.
    (R('data', 'agent_reference'), 'data/agent_reference'),
    # Built-in agent characters (Claydo) installed into ~/.claude/agents/ at
    # startup (character_routes._install_builtin_characters). Leave it out
    # and a fresh frozen install never gets the base agent.
    (R('data', 'agents', 'builtin'), 'data/agents/builtin'),
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

# Bake this build's commit identity into the bundle so a frozen install (no
# .git, see mc/blueprints/system_routes.py _APP_DIR checks) can still tell
# whether it's current. Read at runtime via _load_bundled_build_info as
# `<_APP_DIR>/build_info.json`. tools/notarize-macos.sh copies this exact
# file into the published Clayrune-macOS.build.json release asset so the two
# can never drift apart — see docs/MACOS_NOTARIZATION.md.
#
# Written to a tempfile rather than into the repo tree: this is build output,
# not source, and must never land in a commit (the "nothing operator-specific
# in the repo" rule applies to build artifacts too — a stray build_info.json
# from a personal checkout would ship the builder's local commit as if it
# were canonical).
def _git_out(*args):
    try:
        r = subprocess.run(['git', *args], cwd=REPO_ROOT, capture_output=True,
                            text=True, timeout=10)
        return r.stdout.strip() if r.returncode == 0 else ''
    except Exception:
        return ''


_build_info = {
    'commit': _git_out('rev-parse', '--short', 'HEAD'),
    'commit_full': _git_out('rev-parse', 'HEAD'),
    'branch': _git_out('rev-parse', '--abbrev-ref', 'HEAD'),
    'built_at': datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds'),
}
# The bundled basename MUST be exactly `build_info.json`: PyInstaller keeps the
# source file's name, and both _load_bundled_build_info and
# tools/notarize-macos.sh look it up by that name. A unique tempdir (not a
# prefixed filename) keeps it out of the repo without renaming it. Pinned by
# tests/test_build_info_bundle_name.py.
_build_info_path = os.path.join(tempfile.mkdtemp(prefix='clayrune_build_'), 'build_info.json')
with open(_build_info_path, 'w', encoding='utf-8') as _f:
    json.dump(_build_info, _f)
datas.append((_build_info_path, '.'))

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
