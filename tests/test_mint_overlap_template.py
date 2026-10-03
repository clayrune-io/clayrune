"""MC-964 pre-step-10 fix A — `_mint_overlap_terms` must not count the words
every mint of one kind shares by template.

Step-9 measure (2026-09-29): M4 flagged 552 mint-mint pairs, and 494 survived
removing filename-stem tokens — the driver was "Backlog item closed:" /
"Docs artifact minted:" / `mint_backlog_done_<hash>`, not subject overlap.
Live case: mint_backlog_done_8f1540794b (Brainstorm without a project) was
offered three unrelated notes as supersede candidates.

Every mint here is produced by the real writer (`mint_topic_node`,
`scan_docs_artifacts_for_mint`), so a change to a template string breaks these
tests instead of silently leaving a stale stoplist.
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
    monkeypatch.setitem(mem.state.CONFIG, 'memory_mint_triggers_enabled', True)
    return mem, tmp_path


P = {'id': 'p1'}


def _terms(mem, tmp, fn):
    fm = mem._note_frontmatter((tmp / fn).read_text(encoding='utf-8'))
    return mem._mint_overlap_terms(fn.rsplit('.', 1)[0],
                                   fm.get('description', ''), fm.get('triggers', ''))


def _pair_overlap(mem, tmp, fa, fb):
    return len(_terms(mem, tmp, fa) & _terms(mem, tmp, fb))


def _flagged(mem, fa, fb):
    return any({p['a'], p['b']} == {fa, fb} for p in mem.mint_overlap_report(P))


# ── (1) unrelated mints of one kind no longer overlap ──────────────────────

def test_unrelated_backlog_done_mints_do_not_overlap(env):
    mem, tmp = env
    fa = mem.mint_topic_node(P, trigger_kind='backlog_done',
                             subject='Brainstorm without a project',
                             artifact_path='backlog:aaa')
    fb = mem.mint_topic_node(P, trigger_kind='backlog_done',
                             subject='Stop button missing on the first turn after a break',
                             artifact_path='backlog:bbb')
    assert fa and fb
    assert _pair_overlap(mem, tmp, fa, fb) == 0
    assert not _flagged(mem, *sorted([fa, fb]))


def test_unrelated_docs_artifact_mints_do_not_overlap(env):
    """Real producer: scan_docs_artifacts_for_mint builds the subject
    `<rel> (<size> bytes)`, so 'docs' and 'bytes' are shared by every one."""
    mem, tmp = env
    pp = tmp / 'proj'
    docs = pp / 'docs'
    docs.mkdir(parents=True)
    (docs / 'alpha_pricing.md').write_text('x' * 5000, encoding='utf-8')
    (docs / 'omega_install.md').write_text('y' * 6100, encoding='utf-8')
    minted = mem.scan_docs_artifacts_for_mint(
        {'id': 'p1', 'project_path': str(pp)}, 0, 10 ** 12, docs_dir=docs)
    assert len(minted) == 2
    # Sizes differ on purpose: a size is per-file data, not template.
    assert _pair_overlap(mem, tmp, *minted) == 0


def test_unrelated_hivemind_close_mints_do_not_overlap(env):
    """hivemind_routes builds `<title> (<outcome>)`."""
    mem, tmp = env
    fa = mem.mint_topic_node(P, trigger_kind='hivemind_close',
                             subject='Audit the scheduler (completed)',
                             artifact_path='hivemind:h1')
    fb = mem.mint_topic_node(P, trigger_kind='hivemind_close',
                             subject='Port the installer (completed)',
                             artifact_path='hivemind:h2')
    assert _pair_overlap(mem, tmp, fa, fb) == 0


# ── (2) same-subject mints still overlap ───────────────────────────────────

def test_same_subject_backlog_done_mints_still_overlap(env):
    mem, tmp = env
    fa = mem.mint_topic_node(P, trigger_kind='backlog_done',
                             subject='Stop button missing on the first turn after recovery',
                             artifact_path='backlog:aaa')
    fb = mem.mint_topic_node(P, trigger_kind='backlog_done',
                             subject='Stop button missing on first turn after a restart',
                             artifact_path='backlog:bbb')
    assert _pair_overlap(mem, tmp, fa, fb) >= 4
    assert _flagged(mem, *sorted([fa, fb]))


def test_mint_about_the_backlog_keeps_the_word_backlog(env):
    """Only the leading label is template. A backlog item genuinely about the
    backlog must still share that word with a sibling about the same thing."""
    mem, tmp = env
    fa = mem.mint_topic_node(P, trigger_kind='backlog_done',
                             subject='Backlog journal export drops notes',
                             artifact_path='backlog:aaa')
    fb = mem.mint_topic_node(P, trigger_kind='backlog_done',
                             subject='Backlog journal export is slow',
                             artifact_path='backlog:bbb')
    shared = _terms(mem, tmp, fa) & _terms(mem, tmp, fb)
    assert {'backlog', 'journal', 'export'} <= shared


def test_new_mint_is_still_flagged_against_a_same_subject_mint(env):
    """The inline per-mint WRITE check goes through detect_mint_overlap with
    the kebab-cased slug as `name`; it must strip the template too."""
    mem, tmp = env
    fa = mem.mint_topic_node(P, trigger_kind='backlog_done',
                             subject='Stop button missing on the first turn after recovery',
                             artifact_path='backlog:aaa')
    fb = mem.mint_topic_node(P, trigger_kind='backlog_done',
                             subject='Stop button missing on first turn after a restart',
                             artifact_path='backlog:bbb')
    fm = mem._note_frontmatter((tmp / fb).read_text(encoding='utf-8'))
    assert fm['supersedes'] == 'unresolved'
    assert fa in fm['mint_candidates']


def test_new_unrelated_mint_is_not_flagged_by_template_words(env):
    mem, tmp = env
    mem.mint_topic_node(P, trigger_kind='backlog_done',
                        subject='Stop button missing on the first turn',
                        artifact_path='backlog:aaa')
    fb = mem.mint_topic_node(P, trigger_kind='backlog_done',
                             subject='Brainstorm without a project',
                             artifact_path='backlog:bbb')
    fm = mem._note_frontmatter((tmp / fb).read_text(encoding='utf-8'))
    assert fm['supersedes'] == ''
    assert fm['mint_candidates'] == ''


# ── (3) hand-written notes are unaffected ──────────────────────────────────

def test_hand_written_note_terms_are_unchanged(env):
    mem, _tmp = env
    # Name and description deliberately carry the template words: a note that
    # is not a mint (name does not match mint_<kind>_<hash>) keeps all of them.
    terms = mem._mint_overlap_terms(
        'project_backlog_done_policy',
        'Backlog item closed: docs artifact minted bytes completed', '')
    assert {'backlog', 'done', 'item', 'closed', 'docs', 'artifact', 'minted',
            'bytes', 'completed', 'policy', 'project'} <= terms


def test_hand_note_named_like_a_mint_without_a_hash_is_unaffected(env):
    mem, _tmp = env
    terms = mem._mint_overlap_terms(
        'mint_backlog_done_notes', 'Backlog item closed: docs', '')
    assert {'mint', 'backlog', 'done', 'notes', 'item', 'closed', 'docs'} <= terms


def test_hand_hand_overlap_is_identical_to_the_old_tokenisation(env):
    mem, _tmp = env
    for name, desc, trig in [
        ('arch_overview', 'SPA + Flask shape; UI grid; backlog docs', 'spa, flask'),
        ('feedback_x', 'Backlog item closed: keep docs bytes', ''),
    ]:
        old = ({t for t in (set(mem._mem_tokens(name)) | set(mem._mem_tokens(desc))
                            | set(mem._mem_tokens(trig.replace(',', ' '))))
                if t not in mem._TRIGGER_STOPWORDS})
        assert mem._mint_overlap_terms(name, desc, trig) == old


def test_mint_vs_hand_note_no_longer_matches_on_template_words_alone(env):
    mem, tmp = env
    (tmp / 'arch_backlog_notes.md').write_text(
        '---\nname: arch-backlog-notes\ndescription: "Backlog item done status closed items"\n'
        'metadata:\n  type: project\n---\nbody\n', encoding='utf-8')
    fa = mem.mint_topic_node(P, trigger_kind='backlog_done',
                             subject='Brainstorm without a project',
                             artifact_path='backlog:aaa')
    assert _pair_overlap(mem, tmp, fa, 'arch_backlog_notes.md') == 0
