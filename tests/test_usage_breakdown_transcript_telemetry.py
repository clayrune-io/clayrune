"""mc/memory.py `_extract_transcript_telemetry` — MC-998 follow-up 4 Bug A.

CC writes one JSONL line per content block of an assistant message, and every
one of those lines repeats the SAME `usage` snapshot for the whole message
(verified against the real transcript
`~/.claude/projects/.../811172b0-338c-4ef9-9902-df7b934148f7.jsonl` named in
backlog 4668eafc follow-up 4: `msg_011CfY81vRzZ4W3yXYDeEB7y` appears on two
consecutive lines with byte-identical `usage`). Summing raw lines instead of
deduping by `message.id` double(+)-counts every multi-block message, which
was the dominant contributor to totals reading ~25x too high. The fixture
below is trimmed from that real shape: two content-block lines for the same
message, plus one line for a second message.
"""
from mc.memory import _extract_transcript_telemetry


def _write(tmp_path, lines):
    p = tmp_path / 'transcript.jsonl'
    p.write_text('\n'.join(lines), encoding='utf-8')
    return str(p)


def test_repeated_content_block_lines_for_one_message_are_not_double_counted(tmp_path):
    msg1_usage = ('{"input_tokens": 2, "cache_creation_input_tokens": 39710, '
                  '"cache_read_input_tokens": 15437, "output_tokens": 534}')
    line = ('{"timestamp": "2026-09-29T17:25:54.641Z", "message": '
            '{"id": "msg_A", "model": "claude-sonnet-5", "usage": %s}}' % msg1_usage)
    path = _write(tmp_path, [line, line])  # same content-block message, 2 lines

    tel = _extract_transcript_telemetry(path)

    assert tel['input_tokens'] == 2
    assert tel['cache_read_tokens'] == 15437
    assert tel['cache_write_tokens'] == 39710
    assert tel['output_tokens'] == 534


def test_two_distinct_messages_sum_and_cache_write_is_exposed(tmp_path):
    lines = [
        '{"timestamp": "t1", "message": {"id": "msg_A", "model": "claude-sonnet-5", '
        '"usage": {"input_tokens": 2, "cache_creation_input_tokens": 100, '
        '"cache_read_input_tokens": 1000, "output_tokens": 50}}}',
        # msg_A repeated (second content block) -- must not add again
        '{"timestamp": "t1b", "message": {"id": "msg_A", "model": "claude-sonnet-5", '
        '"usage": {"input_tokens": 2, "cache_creation_input_tokens": 100, '
        '"cache_read_input_tokens": 1000, "output_tokens": 50}}}',
        '{"timestamp": "t2", "message": {"id": "msg_B", "model": "claude-sonnet-5", '
        '"usage": {"input_tokens": 3, "cache_creation_input_tokens": 200, '
        '"cache_read_input_tokens": 2000, "output_tokens": 60}}}',
    ]
    path = _write(tmp_path, lines)

    tel = _extract_transcript_telemetry(path)

    assert tel['input_tokens'] == 5           # 2 + 3, msg_A counted once
    assert tel['cache_write_tokens'] == 300   # 100 + 200
    assert tel['cache_read_tokens'] == 3000   # 1000 + 2000
    assert tel['output_tokens'] == 110        # 50 + 60
    assert tel['model'] == 'claude-sonnet-5'


def test_lines_without_message_id_still_counted_no_dedup_key(tmp_path):
    """A line with no `message.id` (older transcript shape) can't be deduped
    by id -- it is counted as-is rather than silently dropped, matching the
    pre-fix behaviour for that shape."""
    line = ('{"timestamp": "t", "message": {"model": "claude-sonnet-5", '
            '"usage": {"input_tokens": 5, "output_tokens": 1}}}')
    path = _write(tmp_path, [line])

    tel = _extract_transcript_telemetry(path)

    assert tel['input_tokens'] == 5
    assert tel['output_tokens'] == 1


def test_empty_or_missing_file_returns_empty_dict(tmp_path):
    assert _extract_transcript_telemetry(None) == {}
    assert _extract_transcript_telemetry(str(tmp_path / 'nope.jsonl')) == {}
