"""The negation block of a redirect stub obeys the unattended-loop rail.

MC-1179 pass 03, S3.4. When a hit on a superseded note is redirected to its
head, the stub lists every node whose chain resolves to that head, with each
node's description (`_mem_negation_entries`). `_memory_search` already drops
an `origin: unattended` unit for an unattended consumer (`_mem_unit_allowed`),
but this list was built from the whole graph with no such test: a steward
cycle that matched an attended predecessor was handed the description of
every unattended node in the same chain. Autonomous output must not become
autonomous input by riding along in a stub.
"""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

P = {'id': 'p1'}
SECRET = 'steward-wrote-this-zanzibar'


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import server  # noqa: F401
    from mc import memory as mem
    monkeypatch.setattr(mem, '_get_memory_path', lambda p: tmp_path / 'MEMORY.md')
    return mem, tmp_path


def _note(tmp, slug, description, body, *, supersedes='', origin=''):
    lines = ['---', f'name: {slug.replace("_", "-")}', f'description: "{description}"',
             'metadata:', '  type: project']
    if supersedes:
        lines.append(f'supersedes: {supersedes}')
    if origin:
        lines.append(f'origin: {origin}')
    lines += ['---', '', body]
    (tmp / f'{slug}.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')


def _chain(tmp):
    """a_old (attended, the note the query matches) <- b_old (unattended)
    <- head_note (interactive). Only a_old carries the query words."""
    _note(tmp, 'a_old', 'first attended draft', 'quokka marzipan content')
    _note(tmp, 'b_old', SECRET, 'unrelated words entirely',
          supersedes='a_old', origin='unattended')
    _note(tmp, 'head_note', 'the settled design', 'different vocabulary here',
          supersedes='b_old', origin='interactive')


def _stub(mem, consumer_unattended):
    hits = mem._memory_search(P, 'quokka marzipan', topk=5, keep_internal=True,
                              consumer_unattended=consumer_unattended)
    hit = next(h for h in hits if h.get('substituted_from') == 'a_old.md')
    assert hit['file'] == 'head_note.md'
    return hit['snippet']


def test_an_attended_consumer_still_sees_every_node(env):
    mem, tmp = env
    _chain(tmp)
    stub = _stub(mem, consumer_unattended=False)
    assert 'a_old.md: first attended draft' in stub
    assert f'b_old.md: {SECRET}' in stub
    assert 'NEGATED en route (2)' in stub


def test_an_unattended_consumer_is_not_shown_an_unattended_node(env):
    mem, tmp = env
    _chain(tmp)
    stub = _stub(mem, consumer_unattended=True)
    assert SECRET not in stub
    assert 'b_old' not in stub
    assert 'a_old.md: first attended draft' in stub
    assert 'NEGATED en route (1)' in stub


def test_withheld_nodes_are_not_counted_in_the_overflow_line(env):
    mem, tmp = env
    # Eight nodes resolve to the head: one attended, seven unattended. Unfiltered
    # that is over the cap of seven and prints "(+1 more"; filtered it is one.
    _note(tmp, 'p0', 'attended start', 'quokka marzipan content')
    for i in range(1, 8):
        _note(tmp, f'p{i}', f'{SECRET}-{i}', 'other words',
              supersedes=f'p{i - 1}', origin='unattended')
    _note(tmp, 'head_final', 'final', 'still other words',
          supersedes='p7', origin='interactive')
    hits = mem._memory_search(P, 'quokka marzipan', topk=15, keep_internal=True,
                              consumer_unattended=True)
    hit = next(h for h in hits if h.get('substituted_from') == 'p0.md')
    assert hit['file'] == 'head_final.md'
    assert SECRET not in hit['snippet']
    assert '(+' not in hit['snippet']
    assert 'NEGATED en route (1)' in hit['snippet']


def test_the_entries_function_defaults_to_unfiltered(env):
    """Callers that pass no consumer flag keep the behaviour they had."""
    mem, tmp = env
    _chain(tmp)
    units = mem._mem_corpus(tmp, 'MEMORY.md', 'MEMORY_ARCHIVE.md')
    by = {u['file']: u for u in units if u.get('cls') == 'topic'}
    edges = mem._mem_supersede_graph(units)
    assert len(mem._mem_negation_entries('head_note.md', edges, by)) == 2
    kept = mem._mem_negation_entries('head_note.md', edges, by,
                                     consumer_unattended=True)
    assert kept == ['a_old.md: first attended draft']
