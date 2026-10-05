"""Untrusted display text (spec "Detection and trust"): a registry name or evidence URL
cannot carry control, bidi or zero-width characters to the Desk, and a URL is capped at 300."""
from mc.desk_connect import discovery, display_text, registry_lookup

BIDI = '‮⁦⁩​‏﻿\x00\x1b  '


def test_clean_drops_control_bidi_and_separators_and_collapses_space():
    out = display_text.clean(f'io.exa{BIDI}mple/ser\nver\t  x', 200)
    assert out == 'io.example/ser ver x'
    assert all(ch not in out for ch in BIDI)


def test_clean_caps_and_survives_non_strings():
    assert len(display_text.clean('a' * 500, 200)) == 200
    assert display_text.clean(None, 10) == '' and display_text.clean(7, 10) == ''


def test_clean_url_caps_at_300_and_removes_whitespace_and_format_chars():
    u = 'https://example.com/' + 'a' * 400
    assert len(display_text.clean_url(u)) == 300
    assert display_text.clean_url(f'https://exa{BIDI} mple.com/x y') == 'https://example.com/xy'


def test_registry_entry_name_is_sanitised_but_relation_uses_the_real_namespace():
    item = {'server': {'name': f'com.example{BIDI}/mcp', 'title': f'T{BIDI}', 'description': f'D\n{BIDI}d',
                       'websiteUrl': 'https://example.com/' + 'a' * 400}}
    e = registry_lookup._entry(item, 'example.com')
    assert e['name'] == 'com.example/mcp' and e['title'] == 'T' and e['description'] == 'D d'
    assert len(e['url']) == 300


def test_row_sanitises_the_shown_name_and_caps_evidence_url():
    opt = {'method': 'mcp', 'usage_key': next(iter(discovery.usage_keys.USAGE))}
    ev = {'id': 'r0', 'kind': 'registry', 'name': f'ev{BIDI}il', 'relation': 'name_match', 'url': 'https://x.io/' + 'b' * 500}
    row = discovery._row(opt, ev)
    assert 'evil' in row['evidence'] and all(ch not in row['evidence'] for ch in BIDI)
    assert len(row['evidence_url']) == 300
