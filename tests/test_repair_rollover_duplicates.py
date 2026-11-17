"""tools/repair-agent-log-rollover-duplicates.py (MC-1179 S1 cleanup).

Temp data dir only; nothing touches a real log.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _tool():
    spec = importlib.util.spec_from_file_location(
        'repair_rollover_dups', REPO / 'tools' / 'repair-agent-log-rollover-duplicates.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fake(csid, **over):
    row = {'ts': '2026-11-16T09:00:00Z', 'task': 'secret task text', 'status': 'interrupted',
           'session_id': '', 'claude_session_id': csid, 'synthesized': True}
    row.update(over)
    return row


OWN = {'ts': '2026-11-16T10:00:00Z', 'status': 'completed', 'session_id': 'mc1',
       'claude_session_id': 'new', 'claude_session_ids': ['old', 'new']}


def _rows():
    return [
        dict(OWN),                                    # the conversation's own row: keep
        _fake('old'),                                 # listed by OWN: remove
        _fake('stranger'),                            # listed nowhere: keep
        {'ts': 'x', 'status': 'interrupted', 'session_id': 'mc2',
         'claude_session_id': 'old'},                 # real interrupted row: keep
        _fake('old', session_id='mc3'),               # synthesized but owned by a session: keep
        _fake('old', status='completed'),             # not interrupted: keep
        _fake('old', synthesized=False),              # not synthesized: keep
    ]


def _setup(tmp_path, monkeypatch, rows):
    monkeypatch.setenv('MC_DATA_DIR', str(tmp_path))
    d = tmp_path / 'data' / 'projects'
    d.mkdir(parents=True)
    f = d / 'pid_agent_log.json'
    f.write_text(json.dumps(rows), encoding='utf-8')
    return f


def test_selection_removes_exactly_the_listed_synthesized_row():
    t = _tool()
    assert t.duplicate_indexes(_rows()) == [1]


def test_two_synthesized_rows_for_one_unlisted_transcript_are_kept():
    t = _tool()
    assert t.duplicate_indexes([_fake('a'), _fake('a')]) == []


def test_dry_run_changes_nothing_and_prints_no_row_text(tmp_path, monkeypatch, capsys):
    f = _setup(tmp_path, monkeypatch, _rows())
    before = f.read_bytes()
    monkeypatch.setattr(sys, 'argv', ['x'])
    assert _tool().main() == 0
    out = capsys.readouterr().out
    assert f.read_bytes() == before
    assert 'would remove 1 of 7' in out
    assert 'secret task text' not in out
    assert not list(f.parent.glob('*.pre-rollover-cleanup-*'))


def test_apply_removes_only_those_rows_and_backs_up_first(tmp_path, monkeypatch):
    f = _setup(tmp_path, monkeypatch, _rows())
    original = f.read_bytes()
    monkeypatch.setattr(sys, 'argv', ['x', '--apply'])
    assert _tool().main() == 0
    kept = json.loads(f.read_text(encoding='utf-8'))
    expected = [r for i, r in enumerate(_rows()) if i != 1]
    assert kept == expected
    backups = list(f.parent.glob('pid_agent_log.json.pre-rollover-cleanup-*'))
    assert len(backups) == 1 and backups[0].read_bytes() == original
    assert not backups[0].name.endswith('.json')   # load_projects() globs *.json


def test_apply_is_idempotent_and_writes_no_backup_when_nothing_to_do(tmp_path, monkeypatch):
    f = _setup(tmp_path, monkeypatch, [dict(OWN), _fake('stranger')])
    before = f.read_bytes()
    monkeypatch.setattr(sys, 'argv', ['x', '--apply'])
    assert _tool().main() == 0
    assert f.read_bytes() == before
    assert not list(f.parent.glob('*.pre-rollover-cleanup-*'))


def test_unreadable_log_is_skipped_not_rewritten(tmp_path, monkeypatch):
    f = _setup(tmp_path, monkeypatch, [])
    f.write_text('[{"ts": "trunc', encoding='utf-8')
    before = f.read_bytes()
    monkeypatch.setattr(sys, 'argv', ['x', '--apply'])
    assert _tool().main() == 0
    assert f.read_bytes() == before


def test_project_flag_limits_the_run(tmp_path, monkeypatch):
    f = _setup(tmp_path, monkeypatch, _rows())
    other = f.parent / 'other_agent_log.json'
    other.write_bytes(f.read_bytes())
    monkeypatch.setattr(sys, 'argv', ['x', '--apply', '--project', 'pid'])
    assert _tool().main() == 0
    assert len(json.loads(f.read_text(encoding='utf-8'))) == 6
    assert len(json.loads(other.read_text(encoding='utf-8'))) == 7
