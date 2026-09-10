"""The Clayrune API pointer card replaces full-file injection in agent contexts.

Measured 2026-09-10: `data/agent_reference/CLAYRUNE_API.md` (19.9 KB, ~5k
tokens) was injected verbatim into EVERY agent's system prompt via
`_clayrune_api_reference()` — the largest slice of a 53.5 KB fixed floor per
dispatch, paid even by an agent (a code-reviewer, say) that never calls an
endpoint. `_build_agent_context` and `_hm_build_worker_context` (hivemind
workers) now splice in `_CLAYRUNE_API_POINTER_CARD` instead: a short, uniform
card naming the handful of endpoints agents actually reach for, plus a
directive to Read the full reference before guessing or curl-probing a name.

`_clayrune_api_reference()` itself is untouched — it still returns the full
file text — it is just no longer called from either context builder.
"""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

API_REF_PATH = PROJECT_ROOT / 'data' / 'agent_reference' / 'CLAYRUNE_API.md'

_CARD_HEADER = '--- CLAYRUNE API (pointer card) ---'


def _extract_card(ctx):
    """Isolate the card's own section (parts are joined on '\\n\\n').

    The header string also appears quoted, mid-sentence, inside the "API
    discovery" universal-capability line ("...your system prompt has a short
    '--- CLAYRUNE API (pointer card) ---' block..."). A bare `ctx.index(...)`
    finds THAT occurrence first, not the real section — so this anchors on
    the header starting its own '\\n\\n'-joined part.
    """
    import re
    m = re.search(re.escape('\n\n' + _CARD_HEADER) + r'(.*?)(?=\n\n--- |\Z)',
                  ctx, re.S)
    assert m, 'pointer-card section not found as its own context part'
    return _CARD_HEADER + m.group(1)


@pytest.fixture()
def env(tmp_path):
    import server  # noqa: F401 — wires blueprint deps (PORT, memory helpers, …)
    from mc.blueprints import agent_routes as ar

    proj_path = tmp_path / 'proj'
    proj_path.mkdir(parents=True)
    project = {'id': 'tc', 'name': 'TC', 'project_path': str(proj_path),
               'provider': 'claude'}
    return {'ar': ar, 'project': project}


def test_full_reference_file_is_at_least_measured_size():
    # Sanity check on the premise: if this file shrinks well below the
    # measured 19.9 KB, the "largest slice of the floor" framing above (and
    # the fixed-floor numbers reported elsewhere) needs re-measuring, not
    # this test silently passing against a different file.
    assert API_REF_PATH.exists()
    assert len(API_REF_PATH.read_bytes()) > 15_000


def test_pointer_card_is_a_module_level_constant(env):
    ar = env['ar']
    assert isinstance(ar._CLAYRUNE_API_POINTER_CARD, str)
    assert callable(ar._clayrune_api_pointer_card)


def test_card_present_and_under_2kb_in_built_context(env):
    ctx = env['ar']._build_agent_context(env['project'])
    assert 'CLAYRUNE API (pointer card)' in ctx

    # Isolate just the card's own text so a large surrounding context (recent
    # activity, rules, etc.) can't mask the card itself ballooning back up.
    card_text = _extract_card(ctx)
    assert len(card_text.encode('utf-8')) < 2048


def test_full_reference_text_not_in_built_context(env):
    ctx = env['ar']._build_agent_context(env['project'])
    full_text = API_REF_PATH.read_text(encoding='utf-8')
    assert full_text not in ctx
    # The old block header (verbatim full-file dump) must be gone; only the
    # pointer-card header (checked elsewhere) may remain.
    assert '--- CLAYRUNE API REFERENCE ---\n' not in ctx


def test_card_names_the_full_reference_path_for_discoverability(env):
    ctx = env['ar']._build_agent_context(env['project'])
    assert 'data/agent_reference/CLAYRUNE_API.md' in ctx


def test_process_registration_requirement_survives_in_card(env):
    ctx = env['ar']._build_agent_context(env['project'])
    card_text = _extract_card(ctx)
    assert 'MANDATORY' in card_text
    assert '/api/processes/register' in card_text


def test_clayrune_api_reference_accessor_is_untouched(env):
    # The full-text accessor is kept for anything that still wants it — it
    # just isn't called from the context builders anymore.
    text = env['ar']._clayrune_api_reference()
    assert len(text.encode('utf-8')) > 15_000
    assert 'CLAYRUNE API Reference' in text or 'Clayrune API Reference' in text


def test_hivemind_worker_context_uses_the_same_card(tmp_path, monkeypatch):
    import server  # noqa: F401
    from mc.blueprints import hivemind_routes as hm

    hm_id = 'hm_test'
    ws_id = 'ws_test'
    manifest = {'title': 'T', 'goal': 'G', 'project_id': 'tc'}
    ws = {'title': 'WS', 'description': 'do the thing'}

    monkeypatch.setattr(hm, '_hm_load_manifest', lambda h: manifest)
    monkeypatch.setattr(hm, '_hm_load_workstream', lambda h, w: ws)
    monkeypatch.setattr(hm, '_hm_read_handoff', lambda h, w: '')
    monkeypatch.setattr(hm, '_hm_read_context', lambda h, w: '')
    monkeypatch.setattr(hm, '_hm_read_findings', lambda h, w, last_n=20: [])
    monkeypatch.setattr(hm, '_hm_read_bus_messages', lambda h, last_n=50, ws_filter=None: [])
    monkeypatch.setattr(hm, '_hm_read_decisions', lambda h, last_n=20: [])

    ctx = hm._hm_build_worker_context(hm_id, ws_id)
    full_text = API_REF_PATH.read_text(encoding='utf-8')

    assert 'CLAYRUNE API (pointer card)' in ctx
    assert full_text not in ctx
    assert '/api/processes/register' in ctx
    assert 'data/agent_reference/CLAYRUNE_API.md' in ctx
