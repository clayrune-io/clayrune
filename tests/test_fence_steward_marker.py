"""The steward marker arms the fence only when it opens a line, never when quoted."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'steward'))
import fence  # noqa: E402


def _transcript(tmp_path, text):
    p = tmp_path / 't.jsonl'
    p.write_text(json.dumps({'type': 'user', 'message': {'role': 'user', 'content': text}}) + '\n', encoding='utf-8')
    return {'transcript_path': str(p)}


def test_marker_at_start_is_steward(tmp_path):
    assert fence._session_is_steward(_transcript(tmp_path, '[Steward cycle] project x')) is True


def test_marker_after_time_header_is_steward(tmp_path):
    assert fence._session_is_steward(_transcript(tmp_path, '[Local time 09:00]\n[Steward cycle] go')) is True


def test_quoted_marker_is_not_steward(tmp_path):
    text = "=== Prior conversation ===\nfence.py arms only on the literal '[Steward cycle]' marker"
    assert fence._session_is_steward(_transcript(tmp_path, text)) is False


def test_code_dump_marker_is_not_steward(tmp_path):
    assert fence._session_is_steward(_transcript(tmp_path, "2046:STEWARD_MARKER = '[Steward cycle]'")) is False
