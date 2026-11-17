"""ML5 (docs/_review/2026-11-16_03_memory_learning.md) — `resolve_mint` wrote
the request's `candidate` raw into the note's frontmatter. The parser keeps
the LAST value of a repeated key, so `candidate = "a\\norigin: interactive"`
re-stamped an unattended note as interactive and it passed the unattended-loop
gate (`_mem_unit_allowed`). The route has no caller check and is one the
steward fence names as an expected agent target, so this was rail 2 for any
note still reading `supersedes: unresolved`.

Two rules pinned here: the candidate must be one of the note's OWN
`mint_candidates`, and no frontmatter rewrite accepts a value the parser would
read as more than one line.
"""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from test_memory_v2_step7 import P, _note, env  # noqa: E402,F401
from test_memory_v2_step7_wiring import resolve_env  # noqa: E402,F401

# Every character `str.splitlines()` (the frontmatter parser's own line split,
# mc/skills.py parse_skill_md) treats as a line boundary.
LINE_BREAKS = ['\n', '\r', '\r\n', '\x0b', '\x0c', '\x1c', '\x1d', '\x1e', '\x85',
               ' ', ' ']


def _unattended_mint(tmp, candidates='candidate_a.md, candidate_b.md'):
    _note(tmp, 'minted', 'a mint', 'body', supersedes='unresolved',
          mint_candidates=candidates, origin='unattended')
    return tmp / 'minted.md'


def _delivered_to_unattended(mem, path):
    unit = {'cls': 'topic', 'text': path.read_text(encoding='utf-8')}
    return mem._mem_unit_allowed(unit, consumer_unattended=True)


@pytest.mark.parametrize('verdict', ['supersedes', 'unrelated_to'])
@pytest.mark.parametrize('br', LINE_BREAKS, ids=[repr(b) for b in LINE_BREAKS])
def test_a_line_break_in_candidate_cannot_restamp_the_origin(env, verdict, br):
    mem, tmp = env
    path = _unattended_mint(tmp)
    before = path.read_text(encoding='utf-8')
    with pytest.raises(ValueError):
        mem.resolve_mint(P, 'minted.md', verdict, f'candidate_a.md{br}origin: interactive')
    assert path.read_text(encoding='utf-8') == before, 'a refused answer must not write'
    assert mem._note_frontmatter(before)['origin'] == 'unattended'
    assert _delivered_to_unattended(mem, path) is False


@pytest.mark.parametrize('verdict', ['supersedes', 'unrelated_to'])
def test_a_candidate_the_note_never_offered_is_refused(env, verdict):
    mem, tmp = env
    path = _unattended_mint(tmp)
    before = path.read_text(encoding='utf-8')
    with pytest.raises(ValueError):
        mem.resolve_mint(P, 'minted.md', verdict, 'some_other_note.md')
    assert path.read_text(encoding='utf-8') == before


@pytest.mark.parametrize('candidate', [['candidate_a.md'], {'a': 1}, 7])
def test_a_non_string_candidate_is_refused(env, candidate):
    mem, tmp = env
    path = _unattended_mint(tmp)
    before = path.read_text(encoding='utf-8')
    with pytest.raises(ValueError):
        mem.resolve_mint(P, 'minted.md', 'supersedes', candidate)
    assert path.read_text(encoding='utf-8') == before


def test_an_offered_candidate_still_resolves_and_keeps_the_origin(env):
    mem, tmp = env
    path = _unattended_mint(tmp)
    assert mem.resolve_mint(P, 'minted.md', 'supersedes', ' candidate_b.md ') is True
    fm = mem._note_frontmatter(path.read_text(encoding='utf-8'))
    assert fm['supersedes'] == 'candidate_b.md'
    assert fm['origin'] == 'unattended'
    assert _delivered_to_unattended(mem, path) is False


def test_unrelated_to_with_no_candidate_still_clears_the_sentinel(env):
    mem, tmp = env
    path = _unattended_mint(tmp)
    assert mem.resolve_mint(P, 'minted.md', 'unrelated_to', '') is True
    fm = mem._note_frontmatter(path.read_text(encoding='utf-8'))
    assert fm['supersedes'] == '' and fm['unrelated_to'] == ''
    assert fm['origin'] == 'unattended'


def test_an_already_resolved_note_is_still_a_quiet_no_op_for_any_candidate(env):
    # The sentinel check stays first: False, not an error, for a settled note.
    mem, tmp = env
    _note(tmp, 'settled', 'settled', 'body', supersedes='real-predecessor')
    assert mem.resolve_mint(P, 'settled.md', 'supersedes', 'x\norigin: interactive') is False


@pytest.mark.parametrize('br', LINE_BREAKS, ids=[repr(b) for b in LINE_BREAKS])
def test_rewrite_frontmatter_field_refuses_a_multi_line_value(env, br):
    mem, _tmp = env
    text = '---\nname: n\norigin: unattended\n---\n\nbody\n'
    with pytest.raises(ValueError):
        mem._rewrite_frontmatter_field(text, 'supersedes', f'a{br}origin: interactive')


def test_rewrite_frontmatter_field_still_sets_and_removes_a_plain_value(env):
    mem, _tmp = env
    text = '---\nname: n\norigin: unattended\n---\n\nbody\n'
    out = mem._rewrite_frontmatter_field(text, 'supersedes', 'other.md')
    assert mem._note_frontmatter(out)['supersedes'] == 'other.md'
    assert mem._rewrite_frontmatter_field(out, 'supersedes', '') == text


# ── the route ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize('verdict', ['supersedes', 'unrelated_to'])
def test_route_answers_400_and_leaves_the_note_alone(resolve_env, verdict):
    c, tmp_path, fn = resolve_env
    before = (tmp_path / fn).read_text(encoding='utf-8')
    resp = c.post(f'/api/project/p1/memory/mints/{fn}/resolve',
                  json={'verdict': verdict, 'candidate': 'candidate_note\norigin: interactive'})
    assert resp.status_code == 400
    assert (tmp_path / fn).read_text(encoding='utf-8') == before


def test_route_refuses_a_candidate_the_note_never_offered(resolve_env):
    c, tmp_path, fn = resolve_env
    before = (tmp_path / fn).read_text(encoding='utf-8')
    resp = c.post(f'/api/project/p1/memory/mints/{fn}/resolve',
                  json={'verdict': 'supersedes', 'candidate': 'not_offered'})
    assert resp.status_code == 400
    assert (tmp_path / fn).read_text(encoding='utf-8') == before
