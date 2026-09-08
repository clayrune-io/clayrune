"""Source-level guard for mc/behavior_tail.py's three call sites, following
the precedent in tests/test_memory_turn_wiring.py ("a full behavioural
harness ... is out of proportion"). Behavioural coverage of the block's
CONTENT and its position in `_build_agent_context`'s output lives in
tests/test_behavior_tail.py; this file only pins that the wiring does not
drift, get dropped, or get duplicated onto a respawn site that already
rebuilds the whole context (which would re-inject the same block twice on
one turn).
"""
import re
from pathlib import Path

ROUTES = Path(__file__).resolve().parent.parent / 'mc' / 'blueprints' / 'agent_routes.py'


def _source():
    return ROUTES.read_text(encoding='utf-8')


def test_exactly_three_call_sites_exist():
    """1 inside _build_agent_context (every provider, every dispatch/respawn)
    + 2 at the Claude Mode-B live direct-stdin-write sites that also carry
    _memory_turn.refresh_for_turn (the ones with no context rebuild at all)."""
    src = _source()
    calls = len(re.findall(r'_behavior_tail\.render\(', src))
    assert calls == 3, f'expected exactly 3 call sites, found {calls}'


def test_build_agent_context_appends_it_last():
    src = _source()
    idx = src.find('def _build_agent_context(')
    assert idx != -1, '_build_agent_context moved — update this test'
    end = src.find('\n# ── Agent → backlog sync', idx)
    assert end != -1, 'end-of-function marker moved — update this test'
    body = src[idx:end]
    render_idx = body.rfind('_behavior_tail.render(')
    return_idx = body.rfind('return "\\n\\n".join(parts)')
    assert render_idx != -1, '_build_agent_context no longer calls behavior_tail.render()'
    assert return_idx != -1, 'the join-and-return line moved — update this test'
    assert render_idx < return_idx, 'render() must run before the final join+return'
    # Nothing else appends to `parts` between the render() call and the
    # return — i.e. it really is the LAST part, not just A part.
    between = body[render_idx:return_idx]
    assert between.count('parts.append(') == 1, (
        'something appends to parts AFTER behavior_tail.render() — it is no '
        'longer the last block in the system prompt')


def test_router_off_site_bakes_tail_ahead_of_the_mobile_brief():
    """The router-off direct-stdin-write site: tail goes into `claude_content`
    BEFORE the mobile-brief directive, so the memory_turn assembly
    (`_refresh['block'] + '\\n\\n' + _content`) stays untouched and the final
    order is memory-block, tail, mobile-brief-directive, message."""
    src = _source()
    fn_start = src.find("# Router off — write stdin directly (original path)")
    assert fn_start != -1
    fn_end = src.find('threading.Thread(target=_write_stdin,', fn_start)
    assert fn_end != -1
    body = src[fn_start:fn_end]
    brief_idx = body.find('claude_content = _apply_mobile_brief(message, data)')
    tail_idx = body.find('_behavior_tail.render()')
    bake_idx = body.find("claude_content = _tail_text + '\\n\\n' + claude_content")
    assert -1 not in (brief_idx, tail_idx, bake_idx), body
    assert brief_idx < tail_idx < bake_idx, (
        'tail must be computed and baked in AFTER the mobile-brief line so '
        'it ends up ahead of (further from the message than) the directive')
    assert '_memory_turn.refresh_for_turn(' in body, (
        'this site must still carry the memory_turn per-turn refresh too')


def test_same_tier_site_bakes_tail_ahead_of_the_mobile_brief():
    src = _source()
    fn_start = src.find('# Same tier — write stdin directly')
    assert fn_start != -1
    fn_end = src.find('threading.Thread(target=_write_stdin_routed,', fn_start)
    assert fn_end != -1
    body = src[fn_start:fn_end]
    brief_idx = body.find('claude_content = _apply_mobile_brief(message, data)')
    tail_idx = body.find('_behavior_tail.render()')
    bake_idx = body.find("claude_content = _tail_text + '\\n\\n' + claude_content")
    assert -1 not in (brief_idx, tail_idx, bake_idx), body
    assert brief_idx < tail_idx < bake_idx
    assert '_memory_turn.refresh_for_turn(' in body


def test_the_two_respawn_sites_are_not_touched():
    """Same rationale as memory_turn's own version of this test: the
    model-tier-switch respawn and the interrupt-respawn spawn a brand new
    process via _fresh_context_for (which already calls _build_agent_context,
    i.e. already gets the tail) — calling behavior_tail here too would
    double-inject it on that turn."""
    src = _source()
    for marker, label in (
        ("_do_respawn_b():", "model-switch respawn (_do_respawn_b)"),
        ("# Send the new message", "interrupt-respawn"),
    ):
        idx = src.find(marker)
        assert idx != -1, f'{label} marker moved — update this test'
        window = src[idx:idx + 1200]
        assert '_behavior_tail' not in window, (
            f'{label} must not call behavior_tail — it already gets a fresh '
            f'context rebuild (which appends the tail itself), so this '
            f'would double-inject the block')
