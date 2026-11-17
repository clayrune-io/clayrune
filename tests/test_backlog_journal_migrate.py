"""MC-1179 TL15/TL16: migrate finds an item's journal by id, and stamps the real date.

Synthetic record in a temp directory only; cwd is moved to tmp_path so the
_scratch backup lands there too.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / 'tools'
sys.path.insert(0, str(TOOLS))


def _load():
    spec = importlib.util.spec_from_file_location('backlog_journal_migrate', TOOLS / 'backlog-journal-migrate.py')
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _record(tmp_path, text):
    data = tmp_path / 'data'
    data.mkdir()
    rec = {'backlog': [{
        'id': 'it1', 'text': text, 'status': 'open',
        'notes': [{'ts': '2026-11-01T00:00:00', 'agent_code': 'steward', 'text': 'cycle log'}],
    }]}
    (data / 'p.json').write_text(json.dumps(rec), encoding='utf-8')
    return data


def _run(mod, monkeypatch, tmp_path, data, jdir, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, 'argv', ['m', '--project', 'p', '--data-dir', str(data),
                                      '--journal-dir', str(jdir), '--apply'])
    assert mod.main() == 0
    return capsys.readouterr().out


def test_journal_under_an_older_slug_is_found_by_id(tmp_path, monkeypatch, capsys):
    mod = _load()
    data = _record(tmp_path, 'The text was PATCHed after the journal was written')
    jdir = tmp_path / 'j'
    jdir.mkdir()
    (jdir / 'it1-the-old-title.md').write_text('# old\n', encoding='utf-8')
    out = _run(mod, monkeypatch, tmp_path, data, jdir, capsys)
    assert 'SKIP' not in out
    rec = json.loads((data / 'p.json').read_text(encoding='utf-8'))
    notes = rec['backlog'][0]['notes']
    assert len(notes) == 1 and 'it1-the-old-title.md' in notes[0]['text']


def test_item_with_no_journal_at_all_is_still_skipped(tmp_path, monkeypatch, capsys):
    mod = _load()
    data = _record(tmp_path, 'No journal anywhere')
    jdir = tmp_path / 'j'
    jdir.mkdir()
    out = _run(mod, monkeypatch, tmp_path, data, jdir, capsys)
    assert 'SKIP it1' in out
    rec = json.loads((data / 'p.json').read_text(encoding='utf-8'))
    assert rec['backlog'][0]['notes'][0]['text'] == 'cycle log'


def test_pointer_note_carries_todays_date_not_a_fixed_one(tmp_path, monkeypatch, capsys):
    mod = _load()
    data = _record(tmp_path, 'Dated pointer')
    jdir = tmp_path / 'j'
    jdir.mkdir()
    (jdir / 'it1-dated-pointer.md').write_text('# j\n', encoding='utf-8')
    _run(mod, monkeypatch, tmp_path, data, jdir, capsys)
    rec = json.loads((data / 'p.json').read_text(encoding='utf-8'))
    text = rec['backlog'][0]['notes'][0]['text']
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    assert re.search(r'on %s\b' % today, text)
