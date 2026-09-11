"""The agent log must never be destroyed by a failure to READ it (MC-946).

Measured 2026-09-11: `data/projects/mission_control_agent_log.json` held 200
rows, every one of them `synthesized: true` with no `character` key at all,
and every conversation in the app rendered as the project's default agent.
The chain was three links long and each one was silent:

  1. `_save_agent_log` wrote with `write_text` — a truncate-then-write. The
     restart endpoint deliberately runs two servers at once for up to 10s and
     then `os._exit`s the old one, so a kill landed mid-write.
  2. `_load_agent_log` swallowed the resulting parse error and returned `[]`,
     which is also what it returns for a project with no history.
  3. The startup transcript backfill read that `[]` as "MC knows about none of
     these transcripts", synthesized 200 rows, and SAVED — overwriting the
     damaged file and taking every real row's persona, cost and status with it.

These tests pin each link: the write is atomic, the read is loud and moves the
file out of harm's way, and the backfill refuses to run at all against a log it
could not read. Plus the persona discriminator that puts the faces back.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc import characters as _characters      # noqa: E402
from mc import memory as _memory              # noqa: E402
from mc.blueprints import agent_routes as A   # noqa: E402

TRUNCATED = '[\n  {\n    "ts": "2026-09-11T00:00:00Z",\n    "task": "real wo'

LONG_FORM = ('Your name is Dave. Use it when you introduce yourself or sign '
             'off; do not call yourself an assistant, a model, or the name of '
             'your role.')
SHORT_FORM = 'Your name is Vector.'


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """Point the blueprint's DATA_DIR at a throwaway dir."""
    d = tmp_path / 'projects'
    d.mkdir()
    monkeypatch.setattr(A, 'DATA_DIR', d)
    return d


# ── link 2: a read failure is loud, and moves the file out of harm's way ─────

def test_corrupt_log_reads_empty_and_is_quarantined(data_dir, capsys):
    log = data_dir / 'pid_agent_log.json'
    log.write_text(TRUNCATED, encoding='utf-8')

    assert A._load_agent_log('pid') == []

    quarantined = list(data_dir.glob('pid_agent_log.json.corrupt-*'))
    assert len(quarantined) == 1, 'the unreadable file must be moved aside'
    assert quarantined[0].read_text(encoding='utf-8') == TRUNCATED, \
        'quarantine must PRESERVE the bytes — a truncated log still holds rows'
    assert not log.exists()

    out = capsys.readouterr().out
    assert 'failed to parse' in out and 'pid_agent_log.json' in out


def test_readable_check_separates_empty_from_corrupt(data_dir):
    # Absent: nothing to lose, a writer may proceed.
    assert A._agent_log_is_readable('pid') is True
    # Valid: obviously fine.
    (data_dir / 'pid_agent_log.json').write_text('[]', encoding='utf-8')
    assert A._agent_log_is_readable('pid') is True
    # Corrupt: the one case a writer must not treat as empty.
    (data_dir / 'pid_agent_log.json').write_text(TRUNCATED, encoding='utf-8')
    assert A._agent_log_is_readable('pid') is False
    # And asking must not itself move the file — the question is not an action.
    assert (data_dir / 'pid_agent_log.json').exists()


# ── link 1: the write is atomic ─────────────────────────────────────────────

def test_save_leaves_previous_content_when_serialization_fails(data_dir):
    log = data_dir / 'pid_agent_log.json'
    A._save_agent_log('pid', [{'ts': 'keep-me'}])
    good = log.read_text(encoding='utf-8')

    class Unserializable:
        pass

    with pytest.raises(TypeError):
        A._save_agent_log('pid', [{'ts': 'new', 'bad': Unserializable()}])

    assert log.read_text(encoding='utf-8') == good, \
        'a failed write must lose the NEW data, never the old'
    assert not list(data_dir.glob('.pid_agent_log.json.*')), \
        'no temp file may be left behind'


# ── link 3: the backfill refuses to overwrite what it could not read ────────

@pytest.fixture
def server_module(tmp_path, monkeypatch):
    monkeypatch.setenv('MC_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('MC_PORT', '0')
    sys.modules.pop('server', None)
    import server
    return server


def test_backfill_refuses_to_run_against_an_unreadable_log(server_module,
                                                           tmp_path, capsys):
    server = server_module
    log = server.DATA_DIR / 'pid_agent_log.json'
    log.write_text(TRUNCATED, encoding='utf-8')
    before = log.read_bytes()

    # A transcript the backfill would otherwise happily synthesize a row for.
    server_module._recent_claude_transcripts = lambda *a, **k: [
        {'session_id': 'aaaa', 'mtime': 9e9, 'first_user': 'hi',
         'last_user': 'bye', 'turns': 3, 'size': 10},
    ]
    project_dir = tmp_path / 'proj'
    project_dir.mkdir()

    added = server._backfill_agent_log_from_transcripts(
        'pid', {'project_path': str(project_dir)})

    assert added == 0
    assert log.read_bytes() == before, \
        'the damaged file must be left exactly as found, not rewritten'
    assert 'REFUSING to backfill' in capsys.readouterr().out


# ── the persona discriminator ───────────────────────────────────────────────

def _transcript(tmp_path, text):
    f = tmp_path / 'session.jsonl'
    f.write_text(json.dumps({'type': 'user', 'message': {'content': text}}),
                 encoding='utf-8')
    return f


def test_long_form_marker_is_a_persona(tmp_path):
    assert _memory._agent_name_in_transcript(
        _transcript(tmp_path, LONG_FORM)) == 'Dave'


def test_short_form_marker_is_the_default_agent_not_a_persona(tmp_path):
    # "Your name is Vector." is the INHERITED default. Stamping it would put a
    # hired-agent face on every ordinary chat — the bug inverted, not fixed.
    assert _memory._agent_name_in_transcript(
        _transcript(tmp_path, SHORT_FORM)) == ''


def test_no_marker_at_all(tmp_path):
    assert _memory._agent_name_in_transcript(
        _transcript(tmp_path, 'just a normal conversation')) == ''


def test_marker_past_the_first_megabyte_is_still_found(tmp_path):
    # Measured on a real 4.4 MB transcript: the first marker sat at byte
    # 2,295,484, because MC re-injects its context into a LATER user turn.
    # A head-window scan reports "no persona" for exactly the long chats
    # whose identity matters most.
    f = tmp_path / 'big.jsonl'
    f.write_text('x' * (3 * 1024 * 1024) + LONG_FORM, encoding='utf-8')
    assert _memory._agent_name_in_transcript(f) == 'Dave'


def test_unreadable_transcript_is_a_miss_not_a_crash(tmp_path):
    assert _memory._agent_name_in_transcript(tmp_path / 'nope.jsonl') == ''


# ── agent_name -> {name, scope}, through the character files ────────────────

@pytest.fixture
def character_dir(tmp_path, monkeypatch):
    d = tmp_path / 'agents'
    d.mkdir()
    (d / 'social-media-strategist.md').write_text(
        '---\nname: Social strategist\ndescription: posts\nagent_name: Posy\n'
        '---\nbody\n', encoding='utf-8')
    monkeypatch.setattr(_characters, 'GLOBAL_AGENTS_DIR', d)
    return d


def test_agent_name_resolves_through_the_character_file(character_dir):
    # The file stem is 'social-media-strategist'; the agent calls itself Posy.
    # Lowercasing the display name would produce 'posy', which does not exist —
    # this is why the mapping has to go through the files.
    assert _characters.ref_by_agent_name('Posy') == {
        'name': 'social-media-strategist', 'scope': 'global'}


def test_unknown_agent_name_stays_unstamped(character_dir):
    assert _characters.ref_by_agent_name('Nobody') is None
    assert _characters.ref_by_agent_name('') is None
