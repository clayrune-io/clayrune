"""Tests for the CLI slash-command inventory (mc/slash_commands.py).

The extractor reads object literals out of the real 265 MB claude binary, so
these tests build a tiny synthetic "binary" with the same shapes instead —
fast, hermetic, and it pins the parsing rules that actually bit us:

  * a command ships BOTH an interactive and a headless variant under one name
  * `supportsNonInteractive` gates what the composer may offer
  * fields must not bleed between adjacent literals in the minified bundle
"""
import json

from mc import slash_commands as sc


def _bin(tmp_path, body: bytes, name='claude'):
    p = tmp_path / name
    p.write_bytes(b'\x00' * 64 + body + b'\x00' * 64)
    return p


# Mirrors the real bundle: /goal really does appear twice, once as local-jsx
# (interactive) and once as local with supportsNonInteractive.
GOAL_PAIR = (
    b'x={type:"local-jsx",name:"goal",description:"Set a goal Claude checks before stopping",'
    b'argumentHint:"[<condition> | clear]",immediate:!0},y={type:"local",name:"goal",'
    b'supportsNonInteractive:!0,description:"Set a goal \\u2014 keep working until met"};'
)


def test_extracts_name_description_and_hint(tmp_path):
    b = _bin(tmp_path, b'a={type:"local",name:"compact",description:"Free up context",'
                       b'supportsNonInteractive:!0,argumentHint:"<instructions>"};')
    cmds = sc.extract_commands(b)
    assert len(cmds) == 1
    assert cmds[0] == {'name': 'compact', 'description': 'Free up context',
                       'nonInteractive': True, 'argumentHint': '<instructions>'}


def test_headless_variant_wins_over_interactive_duplicate(tmp_path):
    """One name, two literals — the record keeping supportsNonInteractive wins.

    If the interactive entry won, /goal would be filtered out of the picker
    even though it dispatches fine headless (which is what actually happens).
    """
    cmds = sc.extract_commands(_bin(tmp_path, GOAL_PAIR))
    assert len(cmds) == 1
    goal = cmds[0]
    assert goal['name'] == 'goal'
    assert goal['nonInteractive'] is True
    # — must survive decoding, and the hint from the sibling is retained.
    assert '—' in goal['description']
    assert goal['argumentHint'] == '[<condition> | clear]'


def test_fields_do_not_bleed_between_adjacent_literals(tmp_path):
    """A neighbour's flags must not be donated to the entry before it."""
    b = _bin(tmp_path,
             b'a={type:"local",name:"alpha",description:"First"},'
             b'b={type:"local",name:"beta",description:"Second",supportsNonInteractive:!0,'
             b'argumentHint:"[x]"};')
    by = {c['name']: c for c in sc.extract_commands(b)}
    assert by['alpha']['nonInteractive'] is False
    assert 'argumentHint' not in by['alpha']
    assert by['beta']['nonInteractive'] is True
    assert by['beta']['argumentHint'] == '[x]'


def test_prompt_type_entries_are_ignored(tmp_path):
    """`type:"prompt"` is a skill, not a built-in — skills are a separate surface."""
    b = _bin(tmp_path, b'a={type:"prompt",name:"mc-distill",description:"a skill"};')
    assert sc.extract_commands(b) == []


def test_entry_spanning_a_chunk_boundary_is_found(tmp_path, monkeypatch):
    """Chunked reads must overlap, or a literal on the seam vanishes silently."""
    monkeypatch.setattr(sc, '_CHUNK', 128)
    filler = b'z' * 200
    b = _bin(tmp_path, filler + b'a={type:"local",name:"seam",description:"On the boundary",'
                                b'supportsNonInteractive:!0};' + filler)
    assert [c['name'] for c in sc.extract_commands(b)] == ['seam']


def test_headless_commands_filters_interactive_internal_and_removed():
    inv = {'commands': [
        {'name': 'good', 'description': 'Keep me', 'nonInteractive': True},
        {'name': 'tui', 'description': 'Interactive only', 'nonInteractive': False},
        {'name': '__remote-workflow', 'description': 'internal', 'nonInteractive': True},
        {'name': 'workflow-launch-exec', 'description': 'internal', 'nonInteractive': True},
        {'name': 'heapdump', 'description': 'debug', 'nonInteractive': True},
        {'name': 'agents', 'description': '(removed) do not offer', 'nonInteractive': True},
        {'name': 'extra-usage', 'description': 'Renamed to /usage-credits', 'nonInteractive': True},
    ]}
    assert [c['name'] for c in sc.headless_commands(inv)] == ['good']


def test_inventory_caches_and_reuses_until_the_binary_changes(tmp_path):
    b = _bin(tmp_path, b'a={type:"local",name:"one",description:"D",supportsNonInteractive:!0};')
    cache = tmp_path / 'cache.json'

    first = sc.load_inventory(b, cache)
    assert first['source'] == 'extracted'
    assert cache.exists()

    # Unchanged binary -> served from cache, no re-scan.
    assert sc.load_inventory(b, cache)['source'] == 'cache'

    # force=True re-scans even when the fingerprint matches.
    assert sc.load_inventory(b, cache, force=True)['source'] == 'extracted'


def test_inventory_reextracts_when_the_binary_changes(tmp_path):
    """A CLI upgrade must invalidate the cache — that's the whole point of
    extracting per-install instead of baking a list in."""
    b = _bin(tmp_path, b'a={type:"local",name:"one",description:"D",supportsNonInteractive:!0};')
    cache = tmp_path / 'cache.json'
    sc.load_inventory(b, cache)

    b.write_bytes(b'\x00' * 64 + b'a={type:"local",name:"two",description:"D2",'
                                 b'supportsNonInteractive:!0};' + b'\x00' * 999)
    again = sc.load_inventory(b, cache)
    assert again['source'] == 'extracted'
    assert [c['name'] for c in again['commands']] == ['two']


def test_falls_back_when_binary_is_missing(tmp_path):
    inv = sc.load_inventory(tmp_path / 'nope', tmp_path / 'c.json')
    assert inv['source'] == 'fallback'
    names = [c['name'] for c in inv['commands']]
    assert 'goal' in names and 'compact' in names
    # Fallback entries must be usable by the picker, i.e. pass the same filter.
    assert sc.headless_commands(inv)


def test_stale_cache_is_preferred_over_fallback_when_extraction_yields_nothing(tmp_path):
    """A CLI whose bundle format changed shouldn't wipe a good list."""
    b = _bin(tmp_path, b'a={type:"local",name:"one",description:"D",supportsNonInteractive:!0};')
    cache = tmp_path / 'cache.json'
    sc.load_inventory(b, cache)
    b.write_bytes(b'nothing parseable here at all')
    inv = sc.load_inventory(b, cache)
    assert inv['source'] == 'cache'
    assert [c['name'] for c in inv['commands']] == ['one']


def test_cache_file_is_valid_json(tmp_path):
    b = _bin(tmp_path, b'a={type:"local",name:"one",description:"D",supportsNonInteractive:!0};')
    cache = tmp_path / 'cache.json'
    sc.load_inventory(b, cache)
    data = json.loads(cache.read_text(encoding='utf-8'))
    assert data['commands'][0]['name'] == 'one'
    assert set(data['cli']) == {'path', 'size', 'mtime'}
