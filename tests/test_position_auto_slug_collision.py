"""Two subjects that share an automatic slug no longer supersede each other.

MC-1179 pass 03, ML14 (rest). `write_position` names the file after the first
48 characters of the subject key when the caller gives no slug. Two different
subjects with the same 48-character prefix landed on one file, and the second
"superseded" the first: an unrelated ruling was folded under `## Previously`
and stopped being the current position on its own subject.

An AUTO slug whose file already holds a DIFFERENT subject now gets a short
stable hash suffix. A caller-supplied slug, and the same subject, behave as
before. No stored file is renamed.
"""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc.memory_note_slug import subject_suffixed_slug  # noqa: E402

P = {'id': 'p1'}
PREFIX = ('whether the nightly memory snapshot job should, '
          'in every project, also ')                       # 58 key characters
SUBJ_A = PREFIX + 'copy the archive'
SUBJ_B = PREFIX + 'prune old snapshots'


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import server  # noqa: F401
    from mc import memory as mem
    mem_dir = tmp_path / 'memory'
    mem_dir.mkdir()
    monkeypatch.setattr(mem, '_get_memory_path', lambda p: mem_dir / 'MEMORY.md')
    (mem_dir / 'MEMORY.md').write_text('# index\n', encoding='utf-8')
    return mem, mem_dir


def _positions(mem_dir):
    return sorted(f.name for f in mem_dir.glob('position_*.md'))


def _read(mem, mem_dir, name):
    return mem._parse_position((mem_dir / name).read_text(encoding='utf-8'))


def test_the_two_subjects_really_share_an_auto_slug(env):
    mem, _ = env
    ka, kb = mem._mem_link_key(SUBJ_A), mem._mem_link_key(SUBJ_B)
    assert ka != kb and ka[:48] == kb[:48]


def test_a_different_subject_gets_its_own_file(env):
    mem, mem_dir = env
    fa = mem.write_position(P, SUBJ_A, 'declined', 'reason A')
    before = (mem_dir / fa).read_bytes()
    fb = mem.write_position(P, SUBJ_B, 'adopted', 'reason B')
    assert fb != fa
    assert _positions(mem_dir) == sorted([fa, fb])
    assert (mem_dir / fa).read_bytes() == before          # A untouched, not renamed
    a, b = _read(mem, mem_dir, fa), _read(mem, mem_dir, fb)
    assert a['subject'] == SUBJ_A and a['reason'] == 'reason A'
    assert b['subject'] == SUBJ_B and b['reason'] == 'reason B'
    assert '## Previously' not in b['body']
    assert b['name'] == fb[len('position_'):-len('.md')]


def test_the_suffix_is_stable_so_the_second_subject_supersedes_itself(env):
    mem, mem_dir = env
    fa = mem.write_position(P, SUBJ_A, 'declined', 'reason A')
    fb1 = mem.write_position(P, SUBJ_B, 'adopted', 'reason B')
    fb2 = mem.write_position(P, SUBJ_B, 'declined', 'reason B, reversed')
    assert fb2 == fb1
    assert _positions(mem_dir) == sorted([fa, fb1])
    b = _read(mem, mem_dir, fb1)
    assert b['verdict'] == 'declined'
    assert '## Previously' in b['body'] and 'reason B' in b['body']
    assert _read(mem, mem_dir, fa)['reason'] == 'reason A'


def test_the_first_subject_still_supersedes_its_own_file(env):
    mem, mem_dir = env
    fa = mem.write_position(P, SUBJ_A, 'declined', 'reason A')
    mem.write_position(P, SUBJ_B, 'adopted', 'reason B')
    fa2 = mem.write_position(P, SUBJ_A, 'adopted', 'reason A, reversed')
    assert fa2 == fa
    a = _read(mem, mem_dir, fa)
    assert a['verdict'] == 'adopted' and '## Previously' in a['body']


def test_the_same_subject_respelled_supersedes_in_place_as_before(env):
    mem, mem_dir = env
    fa = mem.write_position(P, SUBJ_A, 'declined', 'reason A')
    fa2 = mem.write_position(P, SUBJ_A.upper().replace(' ', '-'), 'adopted', 'again')
    assert fa2 == fa
    assert _positions(mem_dir) == [fa]
    assert '## Previously' in _read(mem, mem_dir, fa)['body']


def test_a_caller_supplied_slug_supersedes_a_different_subject_as_before(env):
    mem, mem_dir = env
    fa = mem.write_position(P, SUBJ_A, 'declined', 'reason A', slug='my-slug')
    fb = mem.write_position(P, SUBJ_B, 'adopted', 'reason B', slug='my-slug')
    assert fa == fb == 'position_my-slug.md'
    assert _positions(mem_dir) == [fa]
    b = _read(mem, mem_dir, fa)
    assert b['subject'] == SUBJ_B and '## Previously' in b['body']


def test_a_file_that_is_not_a_position_is_replaced_as_before(env):
    mem, mem_dir = env
    auto = f"position_{mem._mem_link_key(SUBJ_A)[:48]}.md"
    (mem_dir / auto).write_text('not a position at all\n', encoding='utf-8')
    assert mem.write_position(P, SUBJ_A, 'declined', 'reason A') == auto
    assert _positions(mem_dir) == [auto]


def test_the_suffix_function_is_deterministic_and_a_plain_name():
    one = subject_suffixed_slug('abc', 'subjectkeyone')
    assert one == subject_suffixed_slug('abc', 'subjectkeyone')
    assert one != subject_suffixed_slug('abc', 'subjectkeytwo')
    assert one.startswith('abc-') and len(one) == len('abc-') + 8
    assert all(c.isalnum() or c == '-' for c in one)
