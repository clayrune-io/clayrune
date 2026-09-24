"""MEMORY_DESIGN_V2_SPEC.md §16 build-sequence step 6 — supersession
(MC-944 step 6, backlog MC-964 item b2d85e51).

Scope, exactly the build-sequence bullet: `supersedes` on the successor
(§6.1 Condition 17), head resolution (`head(x)`, cycle-guarded, depth
capped at 8), substitution with dedupe + backfill (§6.3 Condition 19),
the materialised negation block (§6.4), and priority expansion in one of
the two existing hop slots (§16 step 6's own words — folded into
`_mem_link_graph`'s OUT/IN, not a new tier).

Not in scope, and not tested here: minting (step 7, WRITE/RESOLVE split,
`supersedes: unresolved`), the negation ledger and write-act interrupt
(step 8) — both explicitly deferred to their own build steps.
"""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import server  # noqa: F401
    from mc import memory as mem
    monkeypatch.setattr(mem, '_get_memory_path', lambda p: tmp_path / 'MEMORY.md')
    return mem, tmp_path


P = {'id': 'p1'}


def _note(tmp, slug, description, body, *, supersedes='', origin='', verified=None):
    """Write a minimal topic note directly to disk — bypasses
    `write_topic_note`'s origin/verified stamping so tests can construct
    every combination Condition 20's guard needs to exercise, not just the
    one a live write would naturally produce.
    """
    lines = ['---', f'name: {slug.replace("_", "-")}', f'description: "{description}"',
             'metadata:', '  type: project']
    if supersedes:
        lines.append(f'supersedes: {supersedes}')
    if origin:
        lines.append(f'origin: {origin}')
    if verified is not None:
        lines.append(f'verified: {verified}')
    lines += ['---', '', body]
    (tmp / f'{slug}.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')


# ── write_topic_note carries `supersedes` (Condition 17) ────────────────────

def test_write_topic_note_writes_supersedes_verbatim(env):
    mem, tmp = env
    fn = mem.write_topic_note(P, 'design-v2', 'the v2 design', 'body text v2',
                               supersedes='design-v1')
    assert fn
    text = (tmp / fn).read_text(encoding='utf-8')
    assert 'supersedes: design-v1' in text
    fm = mem._note_frontmatter(text)
    assert fm['supersedes'] == 'design-v1'


def test_write_topic_note_without_supersedes_omits_the_line(env):
    mem, tmp = env
    fn = mem.write_topic_note(P, 'standalone', 'no predecessor', 'body')
    text = (tmp / fn).read_text(encoding='utf-8')
    assert 'supersedes:' not in text


# ── _mem_supersede_graph — forward declared, back edge derived ──────────────

def test_supersede_graph_derives_the_back_edge(env):
    mem, tmp = env
    _note(tmp, 'design_v1', 'old', 'old body')
    _note(tmp, 'design_v2', 'new', 'new body', supersedes='design_v1')
    units = mem._mem_corpus(tmp, 'MEMORY.md', 'MEMORY_ARCHIVE.md')
    edges = mem._mem_supersede_graph(units)
    assert edges['design_v2.md']['supersedes'] == 'design_v1.md'
    assert edges['design_v1.md']['superseded_by'] == ['design_v2.md']
    # the back edge is NEVER written to the predecessor's own file
    assert 'superseded_by' not in (tmp / 'design_v1.md').read_text(encoding='utf-8')


def test_supersede_graph_key_matching_is_slug_insensitive(env):
    """Same canonicalisation as `_mem_link_key` — kebab/snake/dotted all match."""
    mem, tmp = env
    _note(tmp, 'design_v1', 'old', 'old body')
    _note(tmp, 'design_v2', 'new', 'new body', supersedes='design-v1.md')
    units = mem._mem_corpus(tmp, 'MEMORY.md', 'MEMORY_ARCHIVE.md')
    edges = mem._mem_supersede_graph(units)
    assert edges['design_v2.md']['supersedes'] == 'design_v1.md'


def test_supersede_graph_drops_unresolved_sentinel(env):
    """`supersedes: unresolved` (step 7's mint placeholder) must never
    resolve to a real edge in this step — nothing mints it yet, but a
    hand-written value must not silently misbehave either."""
    mem, tmp = env
    _note(tmp, 'design_v2', 'new', 'new body', supersedes='unresolved')
    units = mem._mem_corpus(tmp, 'MEMORY.md', 'MEMORY_ARCHIVE.md')
    edges = mem._mem_supersede_graph(units)
    assert edges['design_v2.md']['supersedes'] is None


def test_supersede_graph_drops_dangling_target(env):
    mem, tmp = env
    _note(tmp, 'design_v2', 'new', 'new body', supersedes='nonexistent-note')
    units = mem._mem_corpus(tmp, 'MEMORY.md', 'MEMORY_ARCHIVE.md')
    edges = mem._mem_supersede_graph(units)
    assert edges['design_v2.md']['supersedes'] is None


# ── _mem_supersede_head — chain walk, cycle guard, depth cap ────────────────

def test_head_of_a_standing_note_is_itself(env):
    mem, tmp = env
    _note(tmp, 'design_v1', 'old', 'old body')
    units = mem._mem_corpus(tmp, 'MEMORY.md', 'MEMORY_ARCHIVE.md')
    edges = mem._mem_supersede_graph(units)
    assert mem._mem_supersede_head(edges, 'design_v1.md') == ('design_v1.md', 0)


def test_head_walks_a_multi_hop_chain(env):
    mem, tmp = env
    _note(tmp, 'v1', 'one', 'body')
    _note(tmp, 'v2', 'two', 'body', supersedes='v1')
    _note(tmp, 'v3', 'three', 'body', supersedes='v2')
    units = mem._mem_corpus(tmp, 'MEMORY.md', 'MEMORY_ARCHIVE.md')
    edges = mem._mem_supersede_graph(units)
    assert mem._mem_supersede_head(edges, 'v1.md') == ('v3.md', 2)
    assert mem._mem_supersede_head(edges, 'v2.md') == ('v3.md', 1)
    assert mem._mem_supersede_head(edges, 'v3.md') == ('v3.md', 0)


def test_head_depth_cap_stops_at_8(env):
    mem, tmp = env
    # a chain of 11 notes / 10 hops — the cap must stop the walk at 8, not
    # run away to the true terminal head.
    for i in range(11):
        kw = {'supersedes': f'n{i - 1}'} if i else {}
        _note(tmp, f'n{i}', f'note {i}', 'body', **kw)
    units = mem._mem_corpus(tmp, 'MEMORY.md', 'MEMORY_ARCHIVE.md')
    edges = mem._mem_supersede_graph(units)
    head, hops = mem._mem_supersede_head(edges, 'n0.md')
    assert hops == mem._SUPERSEDE_HEAD_DEPTH_CAP
    assert head != 'n10.md'  # did not reach the true terminal head


def test_head_cycle_guard_terminates(env):
    """A hand-edited pair of notes each declaring `supersedes` on the other
    must not infinite-loop — the graph itself can express a cycle even
    though no single write created one on purpose."""
    mem, tmp = env
    _note(tmp, 'a', 'a', 'body', supersedes='b')
    _note(tmp, 'b', 'b', 'body', supersedes='a')
    units = mem._mem_corpus(tmp, 'MEMORY.md', 'MEMORY_ARCHIVE.md')
    edges = mem._mem_supersede_graph(units)
    head, hops = mem._mem_supersede_head(edges, 'a.md')
    assert head in ('a.md', 'b.md')
    assert hops < mem._SUPERSEDE_HEAD_DEPTH_CAP


# ── _mem_link_graph — supersede edges reuse the OUT/IN hop slots ────────────

def test_link_graph_folds_supersede_into_out_and_in(env):
    mem, tmp = env
    _note(tmp, 'design_v1', 'old', 'old body')
    _note(tmp, 'design_v2', 'new', 'new body', supersedes='design_v1')
    units = mem._mem_corpus(tmp, 'MEMORY.md', 'MEMORY_ARCHIVE.md')
    graph = mem._mem_link_graph(units)
    assert 'design_v1.md' in graph['design_v2.md']['out']
    assert 'design_v2.md' in graph['design_v1.md']['in']


# ── _memory_search — substitution, attribution, dedupe+backfill, guard ──────

def test_bm25_hit_on_an_outpaced_note_is_redirected_to_its_head(env):
    mem, tmp = env
    _note(tmp, 'design_v1', 'old', 'zephyr gadget widget content about frobnication')
    _note(tmp, 'design_v2', 'new conclusion', 'the new frobnication design',
          supersedes='design_v1')
    hits = mem._memory_search(P, 'frobnication', topk=5, keep_internal=True)
    v1 = next(h for h in hits if h['file'] in ('design_v1.md', 'design_v2.md'))
    assert v1['file'] == 'design_v2.md'
    assert v1['substituted_from'] == 'design_v1.md'
    assert 'SUPERSEDED by design_v2.md' in v1['snippet']
    assert 'FULL: design_v2.md' in v1['snippet']


def test_substitution_is_attributed_in_the_public_result_too(env):
    """`substituted_from` must survive the internal-key strip — the agent
    has to be told which note it actually matched (spec: "attributed,
    never silent"), and `keep_internal=False` is the shape every ordinary
    read-floor caller gets."""
    mem, tmp = env
    _note(tmp, 'design_v1', 'old', 'zzyzx unique token here')
    _note(tmp, 'design_v2', 'new', 'the successor', supersedes='design_v1')
    hits = mem._memory_search(P, 'zzyzx', topk=5)
    hit = hits[0]
    assert hit['file'] == 'design_v2.md'
    assert hit['substituted_from'] == 'design_v1.md'
    assert 'cls' not in hit and 'uid' not in hit


def test_standing_note_is_never_substituted(env):
    mem, tmp = env
    _note(tmp, 'lonely_note', 'no chain', 'qwertyzzz unique body')
    hits = mem._memory_search(P, 'qwertyzzz', topk=5, keep_internal=True)
    assert hits[0]['file'] == 'lonely_note.md'
    assert 'substituted_from' not in hits[0]


def test_dedupe_collapses_two_predecessors_of_the_same_head_and_backfills(env):
    """A direct predecessor and a two-hop predecessor of the SAME head must
    not occupy two delivered slots — Condition 19. The freed slot goes to
    the next real candidate (an unrelated standing note), not to a phantom
    empty slot."""
    mem, tmp = env
    _note(tmp, 'pred_direct', 'a', 'bazquux shared token predecessor direct')
    _note(tmp, 'pred_mid', 'b', 'bazquux shared token predecessor mid')
    _note(tmp, 'pred_root', 'c', 'bazquux shared token predecessor root',
          supersedes='pred_mid')
    _note(tmp, 'head_note', 'the conclusion', 'bazquux shared token the head',
          supersedes='pred_root')
    # head_note also directly supersedes pred_direct in the same corpus is
    # not representable (one `supersedes:` per note) — instead chain
    # pred_direct through a second successor that head_note absorbs, so
    # BOTH pred_mid (via pred_root, 2 hops) and pred_direct (via mid2,
    # 2 hops) converge on head_note.
    (tmp / 'head_note.md').unlink()
    _note(tmp, 'mid2', 'd', 'bazquux shared token mid2', supersedes='pred_direct')
    _note(tmp, 'head_note', 'the conclusion', 'bazquux shared token the head',
          supersedes='pred_root')
    units = mem._mem_corpus(tmp, 'MEMORY.md', 'MEMORY_ARCHIVE.md')
    edges = mem._mem_supersede_graph(units)
    assert mem._mem_supersede_head(edges, 'pred_mid.md') == ('head_note.md', 2)
    # mid2/pred_direct's chain does NOT reach head_note (head_note only
    # names pred_root) — mid2 is its own, separate head. Verify that, then
    # assert the real dedupe case: pred_mid and pred_root BOTH resolve to
    # head_note (1 and 2 hops) and must collapse to one delivered slot.
    assert mem._mem_supersede_head(edges, 'pred_direct.md') == ('mid2.md', 1)
    hits = mem._memory_search(P, 'bazquux', topk=10, keep_internal=True)
    files = [h['file'] for h in hits]
    assert files.count('head_note.md') == 1
    # mid2 (pred_direct's own head) stands on its own and is not dropped.
    assert 'mid2.md' in files


def test_verified_predecessor_blocks_substitution_by_a_generated_head(env):
    """Condition 20 — a generated successor must not silently outrank a
    verified predecessor. Substitution is refused; the predecessor's own
    hit is delivered unredirected."""
    mem, tmp = env
    _note(tmp, 'verified_pred', 'human confirmed', 'plugh xyzzy verified content',
          verified="[ {by: 'ron', at: '2026-09-01'} ]")
    _note(tmp, 'generated_head', 'model guess', 'plugh xyzzy generated content',
          supersedes='verified_pred', origin='generated')
    hits = mem._memory_search(P, 'plugh xyzzy', topk=5, keep_internal=True)
    matched = [h for h in hits if h['file'] == 'verified_pred.md']
    assert matched, f'verified predecessor must not be redirected away: {hits}'
    assert 'substituted_from' not in matched[0]


def test_generated_head_still_substitutes_an_unverified_predecessor(env):
    """The Condition 20 guard is specifically verified-vs-generated — an
    UNVERIFIED predecessor is fair game for substitution regardless of the
    head's origin."""
    mem, tmp = env
    _note(tmp, 'unverified_pred', 'no human witness', 'corge grault content')
    _note(tmp, 'generated_head', 'model conclusion', 'corge grault successor',
          supersedes='unverified_pred', origin='generated')
    hits = mem._memory_search(P, 'corge grault', topk=5, keep_internal=True)
    hit = next(h for h in hits if h.get('substituted_from') == 'unverified_pred.md')
    assert hit['file'] == 'generated_head.md'


# ── materialised negation block (§6.4) ───────────────────────────────────────

def test_negation_block_lists_the_predecessor_with_its_description(env):
    mem, tmp = env
    _note(tmp, 'old_design', 'two-level lazy index, rejected', 'thud waldo content')
    _note(tmp, 'new_design', 'the wikilink layer', 'thud waldo successor',
          supersedes='old_design')
    hits = mem._memory_search(P, 'thud waldo', topk=5, keep_internal=True)
    hit = next(h for h in hits if h.get('substituted_from') == 'old_design.md')
    assert 'old_design.md' in hit['snippet']
    assert 'two-level lazy index, rejected' in hit['snippet']


def test_negation_block_caps_at_seven_with_overflow_marker(env):
    mem, tmp = env
    # An 8-predecessor chain (within the depth-8 head-resolution cap) so
    # the negation list itself — capped at 7 — overflows by exactly one.
    _note(tmp, 'p0', 'r0', 'garply fred body')
    for i in range(1, 8):
        _note(tmp, f'p{i}', f'r{i}', 'garply fred body', supersedes=f'p{i - 1}')
    _note(tmp, 'head_final', 'final', 'garply fred body', supersedes='p7')
    hits = mem._memory_search(P, 'garply fred', topk=15, keep_internal=True)
    hit = next(h for h in hits if h.get('substituted_from') == 'p0.md')
    assert hit['file'] == 'head_final.md'
    assert '(+' in hit['snippet'] and 'more, see head_final.md' in hit['snippet']
