"""ML8 (docs/_review/2026-11-16_03_memory_learning.md) — rail 3, "no must be
durable", when the stats sidecar cannot be read.

`_read_skill_stats` returned the empty schema for a file that failed to parse
(and, silently, for any other read error). Every writer is a read-modify-write,
so the first of them, a counter bump that runs on every dispatch, replaced the
file with the empty schema: every recorded "no", the outbox and the signal
window were gone, with nothing kept. `_is_suppressed` and the evaluator's
global read treated any failure as "not suppressed".

Pinned here: an unreadable store is never read as empty; a writer moves a
CORRUPT file aside byte for byte before it starts a fresh one; a file that
merely could not be READ is never written over; and the two "no" lookups fail
closed.
"""
import json
from pathlib import Path

import pytest

import mc.distiller as d

GOOD = {
    '_updated': '2026-11-01T00:00:00Z',
    'signals': [{'sid': 's1', 'ts': '2026-11-01T00:00:00Z', 'kind': 'topic',
                 'exact': 'aaaa', 'coarse': 'bbbb'}],
    'suppressions': {'deadbeef:skill': {'decided_at': '2026-11-01T00:00:00Z',
                                        'decision': 'no'}},
    'outbox': {'deadbeef:skill': {'last_proposed_at': '2026-11-01T00:00:00Z',
                                  'last_proposed_path': 'x'}},
    'counters': {'proposed:skill': 4},
    'vocab_misses': [], 'cost': {}, 'cap_hits': 0, 'cap': 0,
    'last_walk_ts': None, 'last_walk_session_count': 0,
}
GOOD_TEXT = json.dumps(GOOD, indent=2)

PHRASE = 'fix-route'   # in the closed vocabulary; pinned by the test below

CORRUPT = {
    'truncated': GOOD_TEXT[: len(GOOD_TEXT) // 2].encode('utf-8'),
    'nul_filled': b'\x00' * 512,
    'not_utf8': b'{"suppressions": {"\xff\xfe": 1}}',
    'a_list': b'[]',
    'null': b'null',
    'a_string': b'"suppressions"',
}


@pytest.fixture()
def store(tmp_path, monkeypatch):
    logs = []

    def atomic_write(path, text):
        Path(path).write_text(text, encoding='utf-8')

    monkeypatch.setattr(d, '_data_root', tmp_path)
    monkeypatch.setattr(d, '_atomic_write_text', atomic_write)
    monkeypatch.setattr(d, '_now_iso', lambda: '2026-11-17T12:00:00Z')
    monkeypatch.setattr(d, '_log', lambda msg, *a, **k: logs.append(str(msg)))
    monkeypatch.setattr(d, '_cfg', lambda key, default=None: default)
    monkeypatch.setattr(d, '_pcfg', lambda project, key, default=None: default)
    monkeypatch.setattr(d, '_distiller_should_proceed', lambda *a, **k: True)

    class Store:
        root = tmp_path

        def path(self, pid='p1'):
            return d._skill_stats_path(pid)

        def put(self, raw, pid='p1'):
            self.path(pid).write_bytes(raw)

        def quarantined(self, pid='p1'):
            return sorted(tmp_path.glob(f'{pid}_skill_stats.json.corrupt-*'))

    s = Store()
    s.logs = logs
    return s


WRITERS = {
    'increment_counter': lambda: d._increment_counter('p1', 'read_floor_calls'),
    'record_vocab_miss': lambda: d._record_vocab_miss('p1', 'zzz-qqq'),
    'commit_signals': lambda: d._commit_signals('p1', [
        {'sid': 's2', 'ts': '2026-11-17T12:00:00Z', 'kind': 'topic',
         'exact': 'cccc', 'coarse': 'dddd'}]),
    'walk_debounce': lambda: d._cross_project_walk_debounced('p1'),
    'suppress_artifact': lambda: d._suppress_artifact(
        {'project_id': 'p1', 'exact': 'feedface', 'kind': 'skill'}, 'ui_reject'),
    'record_push': lambda: d.record_push(
        'p1', {'phrase': PHRASE, 'kind': 'skill', 'decision': 'no'}),
}


# ── a corrupt file is moved aside, never overwritten ────────────────────────

@pytest.mark.parametrize('writer', sorted(WRITERS))
@pytest.mark.parametrize('shape', sorted(CORRUPT))
def test_a_writer_keeps_the_corrupt_file_before_it_starts_a_fresh_one(store, shape, writer):
    raw = CORRUPT[shape]
    store.put(raw)
    WRITERS[writer]()
    kept = store.quarantined()
    assert len(kept) == 1, f'the unparseable file was not kept: {kept}'
    assert kept[0].read_bytes() == raw, 'the kept copy must be byte for byte'
    assert any('corrupt' in m and kept[0].name in m for m in store.logs), store.logs
    live = json.loads(store.path().read_text(encoding='utf-8'))
    assert isinstance(live, dict), 'the writer still lands on a fresh store'


def test_a_second_corruption_does_not_replace_the_first_kept_copy(store):
    store.put(CORRUPT['truncated'])
    d._increment_counter('p1', 'k')
    store.put(CORRUPT['nul_filled'])
    d._increment_counter('p1', 'k')
    kept = store.quarantined()
    assert sorted(f.read_bytes() for f in kept) == sorted(
        [CORRUPT['truncated'], CORRUPT['nul_filled']])


def test_the_kept_copy_is_not_a_project_record(store):
    # DATA_DIR rule (CLAUDE.md): load_projects() parses every *.json there.
    store.put(CORRUPT['truncated'])
    d._increment_counter('p1', 'k')
    assert [f.name for f in store.root.glob('*.json')] == ['p1_skill_stats.json']


# ── the "no" lookups fail closed ────────────────────────────────────────────

@pytest.mark.parametrize('shape', sorted(CORRUPT))
@pytest.mark.parametrize('pid', ['p1', d._GLOBAL_SUPPRESSION_PID])
def test_is_suppressed_fails_closed_on_a_corrupt_store(store, shape, pid):
    store.put(CORRUPT[shape], pid=pid)
    assert d._is_suppressed('p1', 'never-decided', 'skill') is True
    assert store.path(pid).read_bytes() == CORRUPT[shape], 'a lookup must not touch the file'
    assert store.quarantined(pid) == []
    assert any('skill_stats' in m for m in store.logs), 'a failed lookup must be logged'


def test_is_suppressed_still_answers_from_a_healthy_store(store):
    store.put(GOOD_TEXT.encode('utf-8'))
    assert d._is_suppressed('p1', 'deadbeef', 'skill') is True
    assert d._is_suppressed('p1', 'cafe1234', 'skill') is False
    assert d._is_suppressed('never-written', 'cafe1234', 'skill') is False


def _exploration_signal():
    return {'sid': 's9', 'ts': '2026-11-17T12:00:00Z', 'kind': 'exploration',
            'exact': 'eeee', 'coarse': 'ffff', 'scope_tag': 'x', 'question': 'q'}


def test_the_evaluator_proposes_from_a_healthy_pair_of_stores(store):
    sig = _exploration_signal()
    d._commit_signals('p1', [sig])
    assert len(d._aggregate_per_project('p1', {'id': 'p1'}, [sig])) == 1


@pytest.mark.parametrize('shape', sorted(CORRUPT))
def test_the_evaluator_proposes_nothing_while_the_global_store_is_unreadable(store, shape):
    sig = _exploration_signal()
    d._commit_signals('p1', [sig])
    store.put(CORRUPT[shape], pid=d._GLOBAL_SUPPRESSION_PID)
    assert d._aggregate_per_project('p1', {'id': 'p1'}, [sig]) == []
    assert store.path(d._GLOBAL_SUPPRESSION_PID).read_bytes() == CORRUPT[shape]
    assert any('skill_stats' in m for m in store.logs)


# ── a file that could not be READ is not corrupt, and is never written over ──

@pytest.fixture()
def locked(store, monkeypatch):
    """A healthy store whose read fails with an OS error (a lock, a permission)."""
    store.put(GOOD_TEXT.encode('utf-8'))
    target = store.path()
    real = Path.read_text

    def read_text(self, *a, **k):
        if self == target:
            raise PermissionError(13, 'locked by another process')
        return real(self, *a, **k)

    monkeypatch.setattr(Path, 'read_text', read_text)
    return store


@pytest.mark.parametrize('writer', sorted(WRITERS))
def test_a_writer_does_not_replace_a_file_it_could_not_read(locked, writer):
    try:
        WRITERS[writer]()
    except Exception:
        pass   # a refused write may surface; what matters is the file
    assert locked.path().read_bytes() == GOOD_TEXT.encode('utf-8')
    assert locked.quarantined() == [], 'an unread file is not known to be corrupt'


def test_is_suppressed_fails_closed_on_a_file_it_could_not_read(locked):
    assert d._is_suppressed('p1', 'never-decided', 'skill') is True


def test_record_push_says_so_when_the_decision_was_not_recorded(locked):
    body, status = d.record_push('p1', {'phrase': PHRASE, 'kind': 'skill', 'decision': 'no'})
    assert body.get('accepted') is False and status >= 500, body
    assert body.get('reason') == 'stats_unreadable'


# ── unchanged behaviour ─────────────────────────────────────────────────────

def test_the_phrase_these_tests_push_is_in_the_vocabulary():
    # Otherwise record_push answers oov_phrase and never reaches the store.
    assert d.fingerprint(d._normalize_phrase(PHRASE)) is not None


def test_a_missing_store_is_still_the_empty_schema(store):
    assert d._read_skill_stats('p1') == d._empty_stats()
    d._increment_counter('p1', 'k')
    assert json.loads(store.path().read_text(encoding='utf-8'))['counters'] == {'k': 1}
    assert store.quarantined() == []


def test_a_healthy_store_round_trips_through_every_writer(store):
    store.put(GOOD_TEXT.encode('utf-8'))
    for name in sorted(WRITERS):
        WRITERS[name]()
    live = json.loads(store.path().read_text(encoding='utf-8'))
    assert live['suppressions']['deadbeef:skill']['decision'] == 'no'
    assert live['outbox'] == GOOD['outbox']
    assert store.quarantined() == []


@pytest.mark.parametrize('shape', sorted(CORRUPT))
def test_the_stats_view_does_not_raise_on_a_corrupt_store(store, shape):
    store.put(CORRUPT[shape])
    view = d.get_distiller_stats('p1')
    assert view['stats_unreadable'], 'zeros from an unread store must be labelled'
    assert store.path().read_bytes() == CORRUPT[shape]
    assert store.quarantined() == [], 'a view never moves the file'


def test_the_stats_view_of_a_healthy_store_is_not_labelled_unreadable(store):
    store.put(GOOD_TEXT.encode('utf-8'))
    view = d.get_distiller_stats('p1')
    assert view['stats_unreadable'] == '' and view['suppressions'] == 1
