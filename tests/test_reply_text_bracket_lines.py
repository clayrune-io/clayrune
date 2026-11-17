"""A1 (MC-1179): `_collect_trailing_reply_text` kept only lines that did not
start with '['. A reply line that merely begins with '[' (a Markdown link, a
`[file:...]` marker followed by text) was taken for one of MC's own status
lines, so the saved summary and a workflow step's result became an earlier
message or a fragment. MC's own labelled lines (`[tool: Read] path`,
`[hint] ...`, `[qwen error] ...`) still end the reply."""
import pytest

from mc import agent_runtime as ar
from mc import reply_line_kind as rlk

collect = ar._collect_trailing_reply_text


def test_markdown_link_line_is_the_reply_not_the_previous_message():
    assert collect(['Earlier message.', '[tool: Read]',
                    '[docs](http://x) are here.']) == '[docs](http://x) are here.'


def test_delta_chunks_with_a_link_in_the_last_chunk_join_whole():
    assert collect(['See ', '[docs](x) for more']) == 'See [docs](x) for more'


def test_delta_chunks_with_a_link_in_the_middle_join_whole():
    assert collect(['See ', '[docs](x)', ' for more']) == 'See [docs](x) for more'


@pytest.mark.parametrize('status_line', [
    '[tool: Read] C:/a/b.py',
    '[tool: Bash] ls -la',
    '[tool: AskUserQuestion] which one?',
    '[hint] not logged in',
    '[qwen error] [API Error: quota exceeded]',
    '[codex warn] rmcp AuthRequired',
    '[gemini warning] something',
    '[mcp-sync] failed (best-effort): x',
    '[interrupted]',
    '[gemini exited with code 1]',
    '[Waiting for your answer \u2014 choose above to continue]',
])
def test_mc_status_lines_still_end_the_reply(status_line):
    assert collect(['Old answer', status_line]) == 'Old answer'
    assert collect(['Old answer', status_line, 'New ', 'answer']) == 'New answer'
    assert collect(['Old answer', 'New answer', status_line]) == 'Old answerNew answer'


def test_the_marker_only_pattern_matches_the_one_used_by_the_error_scanner():
    assert rlk.MARKER_ONLY_RE.pattern == ar._MARKER_ONLY_RE.pattern
