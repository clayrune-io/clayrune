"""A note slug is the one request string that becomes a file path.

MC-1179 pass 3, ML14. `write_position` built `mem_dir / f'position_{slug}.md'`
and `write_topic_note` built `mem_dir / f'{slug}.md'` from a slug nobody
checked. Measured on Windows: slug `/../../../victim/SKILL` wrote
`victim/SKILL.md` three levels above the memory directory. Both writers now
refuse a slug that is not a single path component under the memory dir.
"""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc.memory_note_slug import is_safe_note_slug  # noqa: E402

P = {'id': 'p1'}

BS = chr(92)  # a backslash, spelled so no shell or editor can halve it
BAD_SLUGS = ['../escaped', '..' + BS + 'escaped', '/../../../victim/SKILL',
             'a/b', 'a' + BS + 'b', '..', 'x/../y', 'nul' + chr(0) + 'byte']


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import server  # noqa: F401
    from mc import memory as mem
    mem_dir = tmp_path / 'deep' / 'er' / 'memory'
    mem_dir.mkdir(parents=True)
    monkeypatch.setattr(mem, '_get_memory_path', lambda p: mem_dir / 'MEMORY.md')
    (mem_dir / 'MEMORY.md').write_text('# index\n', encoding='utf-8')
    return mem, tmp_path, mem_dir


def _files(root):
    return sorted(str(f.relative_to(root)) for f in root.rglob('*') if f.is_file())


@pytest.mark.parametrize('slug', BAD_SLUGS)
def test_the_checker_refuses_a_slug_that_leaves_the_directory(tmp_path, slug):
    assert is_safe_note_slug(tmp_path, slug) is False
    assert is_safe_note_slug(tmp_path, slug, prefix='position_') is False


@pytest.mark.parametrize('slug', ['adopt-obsidian', 'mint_hivemind_0123456789',
                                  'arch_mobile_ui', 'v2.1 notes', 'a..b'])
def test_the_checker_accepts_an_ordinary_slug(tmp_path, slug):
    assert is_safe_note_slug(tmp_path, slug) is True
    assert is_safe_note_slug(tmp_path, slug, prefix='position_') is True


@pytest.mark.parametrize('slug', BAD_SLUGS)
def test_write_position_refuses_a_traversing_slug(env, slug):
    mem, root, _ = env
    before = _files(root)
    with pytest.raises(ValueError):
        mem.write_position(P, 'some subject', 'declined', 'a reason', slug=slug)
    assert _files(root) == before


@pytest.mark.parametrize('slug', BAD_SLUGS)
def test_write_topic_note_skips_a_traversing_slug(env, slug):
    mem, root, _ = env
    before = _files(root)
    assert mem.write_topic_note(P, slug, 'a description', 'a body') == ''
    assert _files(root) == before


def test_an_ordinary_slug_still_writes(env):
    mem, _, mem_dir = env
    fn = mem.write_position(P, 'some subject', 'declined', 'a reason',
                            slug='plain-slug')
    assert fn == 'position_plain-slug.md' and (mem_dir / fn).is_file()
    fn2 = mem.write_topic_note(P, 'plain_note', 'a description', 'a body')
    assert fn2 == 'plain_note.md' and (mem_dir / fn2).is_file()
