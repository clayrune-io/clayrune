"""Codex read-only history keeps the tool line between narration and answer.

Backlog 30e6a946 part 2. A Codex rollout holds, per turn:
  user message -> assistant `commentary` -> tool calls -> assistant `final_answer`
`CodexRuntime.extract_chat_turns_with_ts` keeps only messages, so a reopened
(dead-session) chat got the commentary and the final answer ADJACENT and showed
two near-identical replies. The chat already renders narration followed by a
`[tool: ...]` line as subordinate (the live-stream signal), so the reconstruct
needs to carry that line. Fixture = the real row shapes, invented text.
"""
import json

from mc import agent_runtime


def _rec(ts, payload):
    return json.dumps({'timestamp': ts, 'type': 'response_item', 'payload': payload})


def _msg(role, text, phase=None):
    p = {'type': 'message', 'role': role,
         'content': [{'type': 'input_text' if role == 'user' else 'output_text', 'text': text}]}
    if phase:
        p['phase'] = phase
    return p


_CALL = {'type': 'custom_tool_call', 'status': 'completed', 'call_id': 'c1', 'name': 'exec', 'input': 'x'}
_OUT = {'type': 'custom_tool_call_output', 'call_id': 'c1', 'output': []}


def _rollout(tmp_path):
    rows = [
        _rec('2026-10-02T14:22:41.779Z', _msg('user', 'Can we raise the number?')),
        _rec('2026-10-02T14:23:10.188Z', {'type': 'reasoning', 'summary': []}),
        _rec('2026-10-02T14:23:11.925Z', _msg('assistant', 'Checking the earlier periods first.', 'commentary')),
        _rec('2026-10-02T14:23:24.047Z', _CALL),
        _rec('2026-10-02T14:23:26.575Z', _OUT),
        _rec('2026-10-02T14:23:27.000Z', dict(_CALL, call_id='c2')),
        _rec('2026-10-02T14:23:28.000Z', dict(_OUT, call_id='c2')),
        _rec('2026-10-02T14:23:37.069Z', _msg('assistant', 'Possibly, but only on the selected period.', 'final_answer')),
        _rec('2026-10-02T14:29:13.244Z', _msg('user', 'Do these')),
        _rec('2026-10-02T14:29:20.000Z', dict(_CALL, call_id='c3')),
        _rec('2026-10-02T14:29:30.000Z', _msg('assistant', 'Done.', 'final_answer')),
        _rec('2026-10-02T14:30:00.000Z', {'type': 'function_call', 'name': 'shell', 'call_id': 'c4'}),
        _rec('2026-10-02T14:31:00.000Z', _msg('user', 'Thanks')),
    ]
    f = tmp_path / 'rollout.jsonl'
    f.write_text('\n'.join(rows) + '\n', encoding='utf-8')
    return f


def test_reconstruct_marks_the_tool_run_between_preamble_and_answer(tmp_path):
    rt = agent_runtime.CodexRuntime()
    turns = rt.extract_chat_turns_with_ts(_rollout(tmp_path), tool_markers=True)
    assert [(r, t) for r, t, _ in turns] == [
        ('user', 'Can we raise the number?'),
        ('assistant', 'Checking the earlier periods first.'),
        ('tool', '[tool: 2 tool calls]'),
        ('assistant', 'Possibly, but only on the selected period.'),
        ('user', 'Do these'),
        # a tool run straight after a user message is not between two replies
        ('assistant', 'Done.'),
        ('user', 'Thanks'),
    ]
    # the marker takes the timestamp of the FIRST call in its run
    assert turns[2][2] == '2026-10-02T14:23:24.047Z'


def test_default_call_is_unchanged(tmp_path):
    """Every other caller (handoff brief, native resume seed, Runs viewer)
    keeps the messages-only contract."""
    rt = agent_runtime.CodexRuntime()
    turns = rt.extract_chat_turns_with_ts(_rollout(tmp_path))
    assert [r for r, _t, _ in turns] == ['user', 'assistant', 'assistant', 'user', 'assistant', 'user']
    assert all(r in ('user', 'assistant') for r, _ in rt.extract_chat_turns(_rollout(tmp_path)))


def test_both_replies_are_kept_not_deduped(tmp_path):
    """Distinct items stay distinct: nothing is dropped on text similarity."""
    rt = agent_runtime.CodexRuntime()
    texts = [t for r, t, _ in rt.extract_chat_turns_with_ts(_rollout(tmp_path), tool_markers=True)
             if r == 'assistant']
    assert 'Checking the earlier periods first.' in texts
    assert 'Possibly, but only on the selected period.' in texts
