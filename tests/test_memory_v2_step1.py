"""MEMORY_DESIGN_V2_SPEC.md §16 build-sequence step 1 — prerequisites, no
behaviour change: the per-file corpus cache (Condition 53), write_position's
write lock, the R2 canonicaliser fix, and the seventeen config-key
registrations (Condition 9).

Each item here is a no-op on default config — this step's whole point is
that landing it changes nothing measurable until a later step reads one of
the new keys or a race actually occurs.
"""
import ast
import sys
import threading
import time
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

SERVER = PROJECT_ROOT / 'server.py'
SETTINGS = PROJECT_ROOT / 'mc' / 'blueprints' / 'settings_routes.py'

# The four keys Condition 9 found read-but-unregistered, plus the ten new
# keys from §4.6 that are genuinely new (three of the table's thirteen —
# negation_interrupt_mode, negation_interrupt_max_hits, and the concept
# behind memory_cold_probe_enabled — already shipped under other names in
# build steps 5/8 and are asserted separately below, not duplicated here).
FIXED_KEYS = {
    'positions_enabled': True,
    'read_floor_position_reserve': 2,
    'position_trigger_max_df': 0.10,
    'continuity_enabled': True,
}
NEW_KEYS = {
    'memory_index_byte_cap': 24 * 1024,
    'session_log_ring': 20,
    'negation_ledger_max': 20,
    'negation_pin_max': 5,
    'read_floor_negation_reserve': 4,
    'memory_gate_mode': 'report',
    'memory_cold_probe_k': 1,
    'memory_fetch_calls_per_turn': 3,
    'memory_mint_on_close': True,
    'trigger_phrase_bigrams': True,
}
ALL_KEYS = {**FIXED_KEYS, **NEW_KEYS}


def _server_defaults():
    """Parsed with ast so this never imports server (autostarts the tunnel
    supervisor — see test_ranker_constants.py's identical rationale)."""
    tree = ast.parse(SERVER.read_text(encoding='utf-8'))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == '_load_config':
            for stmt in ast.walk(node):
                if (isinstance(stmt, ast.Assign)
                        and any(getattr(t, 'id', '') == 'defaults' for t in stmt.targets)
                        and isinstance(stmt.value, ast.Dict)):
                    out = {}
                    for k, v in zip(stmt.value.keys, stmt.value.values):
                        if isinstance(k, ast.Constant):
                            try:
                                out[k.value] = ast.literal_eval(v)
                            except Exception:
                                pass
                    return out
    raise AssertionError('could not find _load_config defaults in server.py')


# ── Condition 9 — config key registration ────────────────────────────────────

@pytest.mark.parametrize('key', sorted(ALL_KEYS))
def test_key_is_settable(key):
    assert f"'{key}'" in SETTINGS.read_text(encoding='utf-8'), (
        f'{key} is missing from _CONFIG_EDITABLE_KEYS — PUTs will return '
        f'{{"ok": true, "updated": []}} and silently change nothing')


@pytest.mark.parametrize('key,default', sorted(ALL_KEYS.items()))
def test_key_has_the_documented_default(key, default):
    d = _server_defaults()
    assert key in d, f'{key} is missing from server.py _load_config defaults'
    assert d[key] == default, (
        f'{key} defaults to {d[key]!r} in server.py, spec §4.6 / Condition 9 '
        f'says {default!r}')


def test_fixed_keys_defaults_match_the_existing_hardcoded_fallback():
    """These four were ALREADY read live with a fallback (memory.py /
    agent_routes.py) — registering them must be a pure no-op, so the new
    default has to equal the fallback that was already in force."""
    d = _server_defaults()
    assert d['positions_enabled'] is True
    assert d['read_floor_position_reserve'] == 2
    assert d['position_trigger_max_df'] == 0.10
    assert d['continuity_enabled'] is True


def test_already_shipped_step5_step8_keys_not_duplicated():
    """negation_interrupt_mode / negation_interrupt_max_hits (step 8,
    a49d269) and memory_turn_cold_probe_enabled (step 5, ba1e955, which
    serves §4.6's memory_cold_probe_enabled row under a module-scoped name)
    must still be the only entries for their concept — this step registers
    the OTHER ten, it does not re-register or rename what already shipped."""
    d = _server_defaults()
    assert d['negation_interrupt_mode'] == 'report'
    assert d['negation_interrupt_max_hits'] == 2
    assert d['memory_turn_cold_probe_enabled'] is True
    src = SERVER.read_text(encoding='utf-8')
    assert src.count("'memory_cold_probe_enabled'") == 0, (
        'memory_cold_probe_enabled would duplicate memory_turn_cold_probe_enabled')


def test_memory_cold_probe_k_is_read_by_memory_turn():
    src = (PROJECT_ROOT / 'mc' / 'memory_turn.py').read_text(encoding='utf-8')
    assert "'memory_cold_probe_k'" in src
    assert 'limit=1' not in src, \
        'cold_search limit should read the new config key, not stay hardcoded'


# ── R2 — the wikilink canonicaliser strips a trailing .md before keying ──────

def test_r2_strips_extension_before_stripping_punctuation():
    from mc import memory as mem
    assert mem._mem_link_key('arch_mobile_ui.md') == mem._mem_link_key('arch_mobile_ui')
    assert mem._mem_link_key('Arch-Mobile-UI.MD') == mem._mem_link_key('arch_mobile_ui')
    # Unaffected: the existing separator/case tolerance.
    assert mem._mem_link_key('arch-mobile-ui') == mem._mem_link_key('arch_mobile_ui')
    assert mem._mem_link_key('') == mem._mem_link_key(None) == ''


def test_r2_repairs_a_class_a_link_end_to_end():
    """A `[[note.md]]` link (class A in §4.4's table) now resolves to the
    same note a bare `[[note]]` link would, with no author-side change."""
    from mc import memory as mem
    units = [
        {'file': 'arch_overview.md', 'cls': 'topic',
         'links': ['arch_overview.md']},  # self-link with extension, dropped anyway
        {'file': 'a.md', 'cls': 'topic', 'links': ['b.md']},
        {'file': 'b.md', 'cls': 'topic', 'links': []},
    ]
    graph = mem._mem_link_graph(units)
    assert graph['b.md']['in'] == ['a.md'], \
        'the explicit .md extension in the link target must resolve to b.md'


# ── Condition 53 — the corpus cache invalidates per file, not per vault ─────

def test_corpus_cache_reuses_unchanged_files(tmp_path, monkeypatch):
    import server  # noqa: F401
    from mc import memory as mem

    mem_dir = tmp_path
    (mem_dir / 'MEMORY.md').write_text('# index\n', encoding='utf-8')
    (mem_dir / 'note_a.md').write_text('alpha content here', encoding='utf-8')
    (mem_dir / 'note_b.md').write_text('beta content here', encoding='utf-8')

    mem._mem_corpus(mem_dir, 'MEMORY.md', 'MEMORY_ARCHIVE.md')
    key = str(mem_dir)
    cached_after_first = mem._memsearch_cache[key]
    entry_a_1 = cached_after_first['note_a.md']
    entry_b_1 = cached_after_first['note_b.md']

    # Touch only note_b.md (content + mtime change).
    time.sleep(0.01)
    (mem_dir / 'note_b.md').write_text('beta content CHANGED', encoding='utf-8')

    mem._mem_corpus(mem_dir, 'MEMORY.md', 'MEMORY_ARCHIVE.md')
    cached_after_second = mem._memsearch_cache[key]
    entry_a_2 = cached_after_second['note_a.md']
    entry_b_2 = cached_after_second['note_b.md']

    assert entry_a_2 is entry_a_1, \
        'an untouched file must reuse its cached (stat, units) tuple verbatim'
    assert entry_b_2 is not entry_b_1, \
        'the touched file must be re-tokenized'
    assert entry_b_2[1][0]['text'] == 'beta content CHANGED'


def test_corpus_cache_still_reflects_disk_content(tmp_path):
    """Behavioural guarantee the refactor must preserve: whatever is on disk
    right now is what a fresh call returns, cache or no cache."""
    import server  # noqa: F401
    from mc import memory as mem

    mem_dir = tmp_path
    (mem_dir / 'MEMORY.md').write_text('# index\n', encoding='utf-8')
    (mem_dir / 'note_a.md').write_text('alpha content here', encoding='utf-8')

    units = mem._mem_corpus(mem_dir, 'MEMORY.md', 'MEMORY_ARCHIVE.md')
    assert any(u['file'] == 'note_a.md' for u in units)

    (mem_dir / 'note_c.md').write_text('gamma content here', encoding='utf-8')
    units2 = mem._mem_corpus(mem_dir, 'MEMORY.md', 'MEMORY_ARCHIVE.md')
    files = {u['file'] for u in units2}
    assert 'note_a.md' in files and 'note_c.md' in files


# ── write_position takes the per-project write lock ─────────────────────────

def test_write_position_uses_the_mem_write_lock():
    src = (PROJECT_ROOT / 'mc' / 'memory.py').read_text(encoding='utf-8')
    start = src.index('def write_position(')
    end = src.index('\ndef ', start + 1)
    body = src[start:end]
    assert '_get_mem_write_lock(' in body, \
        'write_position must take the leaf lock (§16 step 1 / §10.3 G4)'


def test_write_position_serializes_concurrent_supersession(tmp_path, monkeypatch):
    """Two threads racing to supersede the SAME subject must not lose either
    write: the lock forces them to serialize, so the second writer always
    reads the first writer's result as `prior`, and both verdicts survive
    somewhere in the final file (current verdict + one '## Previously' row)."""
    import server  # noqa: F401
    from mc import memory as mem

    monkeypatch.setattr(mem, '_get_memory_path', lambda p: tmp_path / 'MEMORY.md')
    (tmp_path / 'MEMORY.md').write_text('# index\n', encoding='utf-8')
    P = {'id': 'race-project'}

    real_lock_fn = mem._get_mem_write_lock
    barrier = threading.Barrier(2)

    def instrumented(key):
        lk = real_lock_fn(key)
        if key == f'position:{P["id"]}':
            class _Wrapped:
                def __enter__(self_):
                    lk.acquire()
                    return self_

                def __exit__(self_, *a):
                    # Hold the lock briefly so a non-serialized second caller
                    # would provably observe the pre-write state.
                    time.sleep(0.03)
                    lk.release()
            return _Wrapped()
        return lk

    monkeypatch.setattr(mem, '_get_mem_write_lock', instrumented)

    results = []

    def writer(verdict):
        try:
            barrier.wait(timeout=5)
        except threading.BrokenBarrierError:
            pass
        fn = mem.write_position(
            P, subject='shared subject under race', verdict=verdict,
            reason=f'reason for {verdict}', slug='race-subject')
        results.append(fn)

    t1 = threading.Thread(target=writer, args=('declined',))
    t2 = threading.Thread(target=writer, args=('adopted',))
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)

    assert len(results) == 2
    positions = mem.list_positions(P)
    assert len(positions) == 1, 'supersede-in-place must still leave exactly one file'
    final_text = (tmp_path / 'position_race-subject.md').read_text(encoding='utf-8')
    # One of the two verdicts is current; the other survives in the body as
    # a '## Previously' row. Neither write is silently dropped.
    assert '## Previously' in final_text, \
        'the loser of the race must still be recorded, not overwritten silently'
