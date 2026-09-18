"""The per-turn refresh (MC-944) must sit at the FOUR stdin-write sites named
in MEMORY_DESIGN_V2_SPEC.md §9.6 / Condition 45 — no more, no fewer, and in the
right role at each.

Source-level guard, following the precedent in
tests/test_read_floor_injection.py ("This test is a source-level guard rather
than a behavioural one on purpose"): `agent_followup`/`_revive_from_agent_log`/
the dispatch route are thousands of lines deep with a live subprocess.Popen, a
Flask request context and threaded stdin writers — a full behavioural
harness for all four sites is out of proportion to what step 5 needs. The
actual retrieval/suppression/budget/cold-probe BEHAVIOUR is proven directly
against mc/memory_turn.py in tests/test_memory_turn.py; this file only pins
that the wiring itself does not drift or silently get dropped from one of the
four sites (or duplicated onto a fifth, e.g. one of the two model-switch
RESPAWN sites, which already get a fresh `_build_agent_context`/
`_fresh_context_for` call and would double up the block if this ever changed).
"""
import re
from pathlib import Path

ROUTES = Path(__file__).resolve().parent.parent / 'mc' / 'blueprints' / 'agent_routes.py'


def _source():
    return ROUTES.read_text(encoding='utf-8')


def _helper_body(src):
    """The shared live-process writer both direct-write sites call."""
    start = src.find('    def _write_mode_b_stdin(')
    assert start != -1, 'the shared Mode-B stdin writer moved — update this test'
    end = src.find('\n    # Pre-check:', start)
    assert end != -1
    return src[start:end]


def test_exactly_four_call_sites_exist():
    """Spec §9.6 names four stdin write sites: 2 seed_delivered (dispatch,
    revival) and 2 per-turn refreshes at the live direct writes. The two live
    writes now share `_write_mode_b_stdin`, so the refresh call lives once, in
    that helper, and BOTH direct-write branches must call the helper. A
    branch that stops calling it drops the user's message on the floor (the
    c14c2fd same-tier regression)."""
    src = _source()
    calls = re.findall(r'_memory_turn\.(seed_delivered|refresh_for_turn)\(', src)
    assert calls.count('seed_delivered') == 2, calls
    assert calls.count('refresh_for_turn') == 1, calls
    assert '_memory_turn.refresh_for_turn(' in _helper_body(src)
    writes = re.findall(r'_write_mode_b_stdin\(claude_content, (\w+), p, message\)', src)
    assert writes == ['existing', '_rs_existing'], writes


def test_revival_seeds_before_its_stdin_write():
    """agent_routes.py:4021 (the citation's first site) — _revive_from_agent_log
    writes the FIRST turn of a revived conversation; the read floor already ran
    via _build_agent_context(task=message), so this site only needs to SEED the
    delivered-set, not inject a block (there is nothing new to inject — turn 1's
    facts are already in the system prompt)."""
    src = _source()
    fn_start = src.find('def _revive_from_agent_log(')
    assert fn_start != -1
    fn_end = src.find('\ndef ', fn_start + 10)
    body = src[fn_start:fn_end]
    seed_idx = body.find('_memory_turn.seed_delivered(')
    write_idx = body.find('proc.stdin.write(stdin_msg)')
    assert seed_idx != -1, 'revival no longer seeds the delivered-set'
    assert write_idx != -1, 'revival stdin write site moved — update this test'
    assert seed_idx < write_idx, 'seed_delivered must run BEFORE the stdin write'


def test_dispatch_seeds_before_its_deferred_stdin_write():
    """agent_routes.py:5615 (the citation's second site) — the initial Mode-B
    dispatch write, deferred to a daemon thread so a stalled claude startup
    can't pin mgr.lock. Same rationale as revival: turn 1 already has the
    read floor via _build_agent_context(task=task); seed only."""
    src = _source()
    fn_start = src.find('def _write_initial(')
    assert fn_start != -1, 'the deferred initial-write closure moved — update this test'
    fn_end = src.find('\n            threading.Thread(target=_write_initial', fn_start)
    assert fn_end != -1
    body = src[fn_start:fn_end]
    seed_idx = body.find('_memory_turn.seed_delivered(')
    write_idx = body.find('_proc.stdin.write(_msg)')
    assert seed_idx != -1, 'dispatch no longer seeds the delivered-set'
    assert write_idx != -1, 'dispatch stdin write site moved — update this test'
    assert seed_idx < write_idx, 'seed_delivered must run BEFORE the stdin write'


def test_router_off_direct_write_refreshes_and_prepends_the_block():
    """The router-off direct write to an ALREADY-LIVE process. No context
    rebuild happens on this path, which is exactly the B5 break; the shared
    helper must refresh and prepend its block ahead of the message content
    (mobile-brief directive stays closest to the user's own words)."""
    src = _source()
    fn_start = src.find("# Router off — write stdin directly (original path)")
    assert fn_start != -1
    fn_end = src.find('_write_mode_b_stdin(claude_content, existing, p, message)', fn_start)
    assert fn_end != -1, 'router-off branch no longer writes through the shared helper'
    helper = _helper_body(src)
    assert re.search(r"refresh\['block'\]\s*\+\s*'\\n\\n'\s*\+\s*content", helper), (
        'the refreshed block must be prepended ahead of the mobile-brief-'
        'augmented message content, not appended after or dropped')


def test_same_tier_direct_write_refreshes_and_prepends_the_block():
    """The same-tier direct write (router or manual pin chose the running
    model, so no respawn and no context rebuild)."""
    src = _source()
    fn_start = src.find('# Same tier — write stdin directly')
    assert fn_start != -1
    fn_end = src.find('_write_mode_b_stdin(claude_content, _rs_existing, p, message)', fn_start)
    assert fn_end != -1, 'same-tier branch no longer writes through the shared helper'
    assert '_memory_turn.refresh_for_turn(' in _helper_body(src)


def test_the_two_respawn_sites_are_not_touched():
    """The model-tier-switch respawn and the interrupt-respawn spawn a BRAND
    NEW process via _fresh_context_for/_respawn_sysprompt_args, which already
    rebuilds the read floor for that turn — wiring memory_turn onto them too
    would double-inject. Pin that neither respawn's stdin-write block calls
    memory_turn."""
    src = _source()
    for marker, label in (
        ("_do_respawn_b():", "model-switch respawn (_do_respawn_b)"),
        ("# Send the new message", "interrupt-respawn"),
    ):
        idx = src.find(marker)
        assert idx != -1, f'{label} marker moved — update this test'
        window = src[idx:idx + 1200]
        assert '_memory_turn' not in window, (
            f'{label} must not call memory_turn — it already gets a fresh '
            f'context rebuild, so this would double-inject the block')
