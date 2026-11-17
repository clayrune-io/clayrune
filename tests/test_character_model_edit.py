"""MC-1179 pass 09 / OS45: the unattended model auto-upgrade must change the one
`model:` value in a character file and leave every other byte alone.

It used to round-trip through characters.write_character(overwrite=True), which
rebuilds the frontmatter from a fixed list of keys and drops everything else
(a subagent `tools:` line, `color:`, any key Clayrune does not model), and which
also made a built-in character look user-edited (hash != marker) so it never
received built-in updates again.
"""
import hashlib
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc import characters as ch  # noqa: E402
from mc import model_upgrade as mu  # noqa: E402

SUFFIX = ch._BUILTIN_MARKER_SUFFIX

ORIGINAL = (
    '---\n'
    'name: marlow\n'
    'description: Hands-on builder.\n'
    'tools: Read, Grep\n'
    'color: blue\n'
    'x-custom-key: keep me exactly\n'
    'provider: codex\n'
    'model: gpt-5.6-sol\n'
    'effort: high\n'
    '---\n'
    'You write code.\n'
    'model: this line is in the body and must not move\n'
)


@pytest.fixture()
def agents(tmp_path, monkeypatch):
    d = tmp_path / 'agents-global'
    d.mkdir()
    monkeypatch.setattr(ch, 'GLOBAL_AGENTS_DIR', d)
    return d


def _put(agents, text=ORIGINAL, name='marlow'):
    p = agents / f'{name}.md'
    p.write_bytes(text.encode('utf-8'))
    return p


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _upgrade(name='marlow', new='gpt-6-sol'):
    mu._character_apply('global', name, None)(new)


def test_upgrade_changes_only_the_model_value_byte_for_byte(agents):
    p = _put(agents)
    _upgrade()
    assert p.read_bytes() == ORIGINAL.replace(
        'model: gpt-5.6-sol\n', 'model: gpt-6-sol\n', 1).encode('utf-8')


def test_unmodelled_keys_survive_and_model_reads_back(agents):
    p = _put(agents)
    _upgrade()
    text = p.read_text(encoding='utf-8')
    for kept in ('tools: Read, Grep', 'color: blue', 'x-custom-key: keep me exactly'):
        assert kept in text
    rec = ch.read_character('global', 'marlow')
    assert rec and rec['engine']['model'] == 'gpt-6-sol'


def test_crlf_and_quoted_value_are_preserved(agents):
    src = ORIGINAL.replace('model: gpt-5.6-sol', 'model: "gpt-5.6-sol"').replace('\n', '\r\n')
    p = _put(agents, src)
    _upgrade()
    assert p.read_bytes() == src.replace('"gpt-5.6-sol"', '"gpt-6-sol"').encode('utf-8')


def test_no_model_line_refuses_and_leaves_file_untouched(agents):
    src = ORIGINAL.replace('model: gpt-5.6-sol\n', '')
    p = _put(agents, src)
    with pytest.raises(RuntimeError, match='no model'):
        _upgrade()
    assert p.read_bytes() == src.encode('utf-8')


def test_block_scalar_model_refuses(agents):
    src = ORIGINAL.replace('model: gpt-5.6-sol\n', 'model: |\n  gpt-5.6-sol\n')
    p = _put(agents, src)
    with pytest.raises(RuntimeError):
        _upgrade()
    assert p.read_bytes() == src.encode('utf-8')


def test_no_temp_files_left_behind(agents):
    _put(agents)
    _upgrade()
    assert sorted(x.name for x in agents.iterdir()) == ['marlow.md']


def test_unsafe_new_value_refused(agents):
    p = _put(agents)
    with pytest.raises(ValueError):
        _upgrade(new='bad\nvalue')
    assert p.read_bytes() == ORIGINAL.encode('utf-8')


# ── built-in hash marker ─────────────────────────────────────────────────────

def _builtin_src(tmp_path, text):
    root = tmp_path / 'builtin'
    root.mkdir(exist_ok=True)
    (root / 'marlow.md').write_bytes(text.encode('utf-8'))
    return root


def test_in_sync_builtin_marker_is_rewritten_and_keeps_receiving_updates(agents, tmp_path):
    root = _builtin_src(tmp_path, ORIGINAL)
    assert ch.install_builtin_characters(root)['installed'] == ['marlow']
    marker = agents / f'marlow.md{SUFFIX}'
    assert marker.read_text(encoding='utf-8') == _sha(ORIGINAL.encode('utf-8'))

    _upgrade()

    p = agents / 'marlow.md'
    assert marker.read_text(encoding='utf-8') == _sha(p.read_bytes())
    # A later release of the built-in is applied, not "preserved as user-edited".
    _builtin_src(tmp_path, ORIGINAL + 'New release text.\n')
    res = ch.install_builtin_characters(root)
    assert res['updated'] == ['marlow'] and res['preserved'] == []


def test_user_edited_builtin_marker_is_left_alone(agents, tmp_path):
    root = _builtin_src(tmp_path, ORIGINAL)
    ch.install_builtin_characters(root)
    p = agents / 'marlow.md'
    marker = agents / f'marlow.md{SUFFIX}'
    marker_before = marker.read_text(encoding='utf-8')
    p.write_bytes(p.read_bytes() + b'user added a line\n')   # user edit

    _upgrade()

    assert marker.read_text(encoding='utf-8') == marker_before
    _builtin_src(tmp_path, ORIGINAL + 'New release text.\n')
    assert ch.install_builtin_characters(root)['preserved'] == ['marlow']


def test_character_without_marker_gets_no_marker(agents):
    _put(agents)
    _upgrade()
    assert not (agents / f'marlow.md{SUFFIX}').exists()
