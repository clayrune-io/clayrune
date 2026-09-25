"""Tests for mc/vendor_context_sync.py (2026-09-25).

Codex/Gemini/Qwen each load their OWN natively-read project-context file
(AGENTS.md/GEMINI.md/QWEN.md) instead of CLAUDE.md. Nothing kept those
mirrors in sync -- this repo's AGENTS.md drifted stale for weeks until Dave
hand-copied it again. `sync_vendor_context_file` regenerates the mirror from
CLAUDE.md, hash-gated so an unchanged source is a no-op, and refuses to
overwrite a hand-authored file that has no generated-from marker.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import mc.vendor_context_sync as vcs  # noqa: E402

CLAUDE_BODY = (
    "# Clayrune — Claude Code project notes\n\n"
    "## Some binding rule\n\nDo the thing.\n"
)


def _write_claude(tmp_path, body=CLAUDE_BODY):
    (tmp_path / 'CLAUDE.md').write_text(body, encoding='utf-8')


def test_fresh_write_creates_marked_mirror(tmp_path):
    _write_claude(tmp_path)
    vcs.sync_vendor_context_file(str(tmp_path), 'AGENTS.md')
    target = tmp_path / 'AGENTS.md'
    assert target.is_file()
    text = target.read_text(encoding='utf-8')
    assert text.startswith(vcs.MARKER_PREFIX)
    assert text.endswith(vcs.MARKER_SUFFIX + '\n' + '# Clayrune — Codex project notes\n\n'
                          '## Some binding rule\n\nDo the thing.\n')
    assert '# Clayrune — Claude Code project notes' not in text


def test_hash_unchanged_is_noop(tmp_path):
    _write_claude(tmp_path)
    vcs.sync_vendor_context_file(str(tmp_path), 'GEMINI.md')
    target = tmp_path / 'GEMINI.md'
    first_mtime = target.stat().st_mtime_ns
    first_text = target.read_text(encoding='utf-8')

    vcs.sync_vendor_context_file(str(tmp_path), 'GEMINI.md')
    assert target.stat().st_mtime_ns == first_mtime
    assert target.read_text(encoding='utf-8') == first_text


def test_source_changed_regenerates(tmp_path):
    _write_claude(tmp_path)
    vcs.sync_vendor_context_file(str(tmp_path), 'QWEN.md')
    target = tmp_path / 'QWEN.md'
    old_text = target.read_text(encoding='utf-8')

    _write_claude(tmp_path, CLAUDE_BODY + '\n## New rule\n\nAdded later.\n')
    vcs.sync_vendor_context_file(str(tmp_path), 'QWEN.md')
    new_text = target.read_text(encoding='utf-8')
    assert new_text != old_text
    assert 'Added later.' in new_text
    assert '# Clayrune — Qwen project notes' in new_text


def test_unmarked_hand_authored_file_untouched(tmp_path):
    _write_claude(tmp_path)
    target = tmp_path / 'AGENTS.md'
    target.write_text('# My own notes\n\nNothing to do with CLAUDE.md.\n', encoding='utf-8')

    vcs.sync_vendor_context_file(str(tmp_path), 'AGENTS.md')
    assert target.read_text(encoding='utf-8') == '# My own notes\n\nNothing to do with CLAUDE.md.\n'


def test_unmarked_file_identical_to_claude_body_is_adopted(tmp_path):
    _write_claude(tmp_path)
    target = tmp_path / 'AGENTS.md'
    # Dave's one-time hand mirror: title rewritten, body otherwise identical,
    # no marker line yet.
    target.write_text(vcs._rewritten_body(CLAUDE_BODY, 'Codex'), encoding='utf-8')

    vcs.sync_vendor_context_file(str(tmp_path), 'AGENTS.md')
    text = target.read_text(encoding='utf-8')
    assert text.startswith(vcs.MARKER_PREFIX)
    assert '## Some binding rule' in text


def test_non_md_context_file_skipped(tmp_path):
    _write_claude(tmp_path)
    vcs.sync_vendor_context_file(str(tmp_path), '.aider.conf.yml')
    assert not (tmp_path / '.aider.conf.yml').is_file()


def test_none_context_file_skipped(tmp_path):
    _write_claude(tmp_path)
    vcs.sync_vendor_context_file(str(tmp_path), None)
    assert list(tmp_path.iterdir()) == [tmp_path / 'CLAUDE.md']


def test_claude_md_itself_skipped(tmp_path):
    _write_claude(tmp_path)
    before = (tmp_path / 'CLAUDE.md').read_text(encoding='utf-8')
    vcs.sync_vendor_context_file(str(tmp_path), 'CLAUDE.md')
    assert (tmp_path / 'CLAUDE.md').read_text(encoding='utf-8') == before


def test_no_source_claude_md_is_noop(tmp_path):
    vcs.sync_vendor_context_file(str(tmp_path), 'AGENTS.md')
    assert not (tmp_path / 'AGENTS.md').exists()


def test_flag_off_dispatch_hook_skips():
    """The dispatch-time hook in agent_routes._dispatch_via_runtime gates on
    state.CONFIG['vendor_context_sync_enabled'] before calling this module at
    all -- this module has no flag of its own by design, the CALLER gates
    it. Pinned here: the flag defaults ON in server.py's `_load_config`
    defaults dict, and is in settings_routes's live-editable allowlist, so
    the gate is a real per-install switch a human can flip off, not dead
    code. A regex read of server.py's source avoids invoking `_load_config`
    (module-level CONFIG_PATH, real config.json side effects).
    """
    server_src = (PROJECT_ROOT / 'server.py').read_text(encoding='utf-8')
    assert "'vendor_context_sync_enabled': True," in server_src
    import mc.blueprints.settings_routes as _settings_routes
    assert 'vendor_context_sync_enabled' in _settings_routes._CONFIG_EDITABLE_KEYS
