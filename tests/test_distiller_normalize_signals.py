"""`_normalize_signals` takes whatever the extraction model returned.

MC-1179 pass 3, ML32. The function assumed `signals` was an object and every
item of `topics`, `preferences` and `explorations` was an object. A model that
answered with a list for `signals`, or put a bare string inside `topics`,
raised AttributeError; the daemon thread's handler counted it and every signal
of that session was dropped, the well-formed ones included. `paths_tried` and
`tools_used` were stored as given and later joined into a prompt.
"""
import pytest

import mc.distiller as d

PHRASE = 'fix-route'   # in the closed vocabulary; pinned by the first test


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    monkeypatch.setattr(d, '_now_iso', lambda: '2026-11-19T12:00:00Z')
    monkeypatch.setattr(d, '_record_vocab_miss', lambda *a, **k: None)


def _norm(parsed):
    return d._normalize_signals('p1', 's1', parsed)


def test_the_probe_phrase_is_in_the_vocabulary():
    assert d.fingerprint(PHRASE) is not None


def test_well_formed_output_is_unchanged():
    out = _norm({'scope_tag': 'project-specific', 'signals': {
        'topics': [{'phrase': PHRASE, 'summary': 's', 'problem': 'p',
                    'resolution': 'r'}],
        'preferences': [{'phrase': PHRASE, 'summary': 's', 'evidence_quote': 'q'}],
        'explorations': [{'phrase': PHRASE, 'question': 'q',
                          'paths_tried': ['a.py', 'b.py'], 'outcome': 'o',
                          'tools_used': ['Grep']}]}})
    assert [s['kind'] for s in out] == ['topic', 'preference', 'exploration']
    assert out[2]['paths_tried'] == ['a.py', 'b.py']
    assert out[2]['tools_used'] == ['Grep']
    assert all(s['scope_tag'] == 'project-specific' for s in out)


@pytest.mark.parametrize('signals', [[], 'topics', 7, None, [{'phrase': PHRASE}]])
def test_signals_that_is_not_an_object_yields_nothing_and_does_not_raise(signals):
    assert _norm({'signals': signals}) == []


@pytest.mark.parametrize('section', ['topics', 'preferences', 'explorations'])
def test_one_malformed_item_does_not_lose_the_others(section):
    good = {'phrase': PHRASE, 'summary': 'kept'}
    out = _norm({'signals': {section: ['a bare string', None, 3, ['x'], good]}})
    assert len(out) == 1 and out[0]['phrase'] == PHRASE


@pytest.mark.parametrize('section', ['topics', 'preferences', 'explorations'])
@pytest.mark.parametrize('value', ['not a list', 5, {'phrase': PHRASE}])
def test_a_section_that_is_not_a_list_is_skipped(section, value):
    out = _norm({'signals': {section: value,
                             'topics' if section != 'topics' else 'preferences':
                                 [{'phrase': PHRASE}]}})
    assert len(out) == 1


def test_a_phrase_that_is_not_a_string_is_dropped_not_raised():
    out = _norm({'signals': {'topics': [{'phrase': 12}, {'phrase': ['x']},
                                        {'phrase': PHRASE}]}})
    assert [s['phrase'] for s in out] == [PHRASE]


def test_paths_and_tools_are_stored_as_capped_strings():
    long = 'x' * 5000
    out = _norm({'signals': {'explorations': [
        {'phrase': PHRASE, 'paths_tried': [long, 7, {'a': 1}, None, 'ok.py'],
         'tools_used': 'Grep'},
        {'phrase': PHRASE, 'paths_tried': 42, 'tools_used': [1, 'Read']}]}})
    first, second = out
    assert all(isinstance(p, str) for p in first['paths_tried'])
    assert max(len(p) for p in first['paths_tried']) <= d._SIGNAL_LIST_ITEM_CHARS
    assert 'ok.py' in first['paths_tried']
    assert first['tools_used'] == []          # a string is not a list of tools
    assert second['paths_tried'] == []
    assert second['tools_used'] == ['1', 'Read']
    # The renderer joins these; it must not meet a non-string.
    ', '.join(second['tools_used'])
