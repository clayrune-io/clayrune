"""The macOS build must bundle its commit identity as exactly `build_info.json`.

Regression, 2026-09-14: installer/build-macos.spec wrote the file as
`clayrune_build_info.json`, and PyInstaller keeps the source basename, so the
.app shipped `clayrune_build_info.json` while the runtime update check
(`_load_bundled_build_info`) and tools/notarize-macos.sh both look for
`build_info.json`. The frozen app could never tell it was out of date, and the
release script would have died writing the manifest. The frozen-update tests
write `build_info.json` by hand, so they could not catch the mismatch.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_spec_bundles_build_info_under_the_name_the_runtime_reads():
    spec = (ROOT / 'installer' / 'build-macos.spec').read_text(encoding='utf-8')
    m = re.search(r"^_build_info_path\s*=\s*(.+)$", spec, re.MULTILINE)
    assert m, "build-macos.spec no longer defines _build_info_path"
    literals = re.findall(r"['\"]([^'\"]+)['\"]", m.group(1))
    assert literals and literals[-1] == 'build_info.json', (
        f"bundled basename must be build_info.json, got {literals[-1] if literals else None!r}")
    assert "datas.append((_build_info_path, '.'))" in spec


def test_runtime_and_release_script_agree_on_the_name():
    routes = (ROOT / 'mc' / 'blueprints' / 'system_routes.py').read_text(encoding='utf-8')
    assert "'build_info.json'" in routes
    script = (ROOT / 'tools' / 'notarize-macos.sh').read_text(encoding='utf-8')
    assert '-name build_info.json' in script
