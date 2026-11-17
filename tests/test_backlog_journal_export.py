"""MC-1179 TL14: re-running the journal export must never lose agent-appended text.

Synthetic items in a temp directory only; the live backlog and docs/_journal
are never touched (fetch is replaced, --out is a tmp_path).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / 'tools'
sys.path.insert(0, str(TOOLS))
from backlog_journal_common import journal_name  # noqa: E402


def _load():
    spec = importlib.util.spec_from_file_location('backlog_journal_export', TOOLS / 'backlog-journal-export.py')
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


export = _load()

AGENT_ENTRY = '\n### 2026-11-16T10:00:00  ·  agent-x  ·  unattended\n\nagent appended finding\n'


def note(ts, text, code='abc'):
    return {'ts': ts, 'agent_code': code, 'text': text}


def item(iid='it1', text='Fix the widget', notes=None):
    return {'id': iid, 'text': text, 'priority': 'normal', 'status': 'open',
            'notes': notes if notes is not None else [note('2026-11-01T00:00:00', 'first')]}


def snapshot(d):
    return {f.name: (f.read_bytes(), f.stat().st_mtime_ns) for f in d.glob('*.md') if f.name != 'INDEX.md'}


def run_main(monkeypatch, out, items, *extra, capsys=None):
    monkeypatch.setattr(export, 'fetch', lambda *_: items)
    monkeypatch.setattr(sys, 'argv', ['x', '--out', str(out), *extra])
    assert export.main() == 0
    return capsys.readouterr().out if capsys else ''


def test_appended_entry_survives_second_export(tmp_path, monkeypatch, capsys):
    items = [item()]
    run_main(monkeypatch, tmp_path, items)
    j = tmp_path / journal_name(items[0])
    with open(j, 'ab') as fh:
        fh.write(AGENT_ENTRY.encode('utf-8'))
    before = j.read_bytes()
    out = run_main(monkeypatch, tmp_path, items, capsys=capsys)
    assert j.read_bytes() == before
    assert '0 created, 0 got appended notes, 1 left alone' in out


def test_new_note_appended_once_not_twice(tmp_path, monkeypatch):
    items = [item()]
    run_main(monkeypatch, tmp_path, items)
    j = tmp_path / journal_name(items[0])
    with open(j, 'ab') as fh:
        fh.write(AGENT_ENTRY.encode('utf-8'))
    before = j.read_bytes()
    items[0]['notes'].append(note('2026-11-02T00:00:00', 'second'))
    run_main(monkeypatch, tmp_path, items)
    after = j.read_bytes()
    assert after.startswith(before)                       # nothing before the append moved
    assert after[len(before):].decode('utf-8').count('### 2026-11-02T00:00:00') == 1
    run_main(monkeypatch, tmp_path, items)                # third run
    assert j.read_bytes() == after
    assert after.decode('utf-8').count('second') == 1


def test_append_to_file_without_trailing_newline_and_crlf(tmp_path, monkeypatch):
    items = [item()]
    j = tmp_path / journal_name(items[0])
    j.write_bytes(b'# t\r\n\r\n### 2026-11-01T00:00:00  \xc2\xb7  abc  \xc2\xb7  interactive\r\n\r\nfirst\r\nagent tail')
    items[0]['notes'].append(note('2026-11-02T00:00:00', 'second'))
    run_main(monkeypatch, tmp_path, items)
    data = j.read_bytes()
    assert data.startswith(b'# t\r\n\r\n### 2026-11-01T00:00:00')
    assert b'agent tail\r\n\r\n### 2026-11-02T00:00:00' in data
    assert b'\n' not in data.replace(b'\r\n', b'')        # appended part kept CRLF too


def test_index_only_touches_no_journal(tmp_path, monkeypatch, capsys):
    items = [item('a1', 'Alpha'), item('b2', 'Beta')]
    run_main(monkeypatch, tmp_path, items)
    ja = tmp_path / journal_name(items[0])
    with open(ja, 'ab') as fh:
        fh.write(AGENT_ENTRY.encode('utf-8'))
    items[0]['notes'].append(note('2026-11-05T00:00:00', 'brand new'))   # would append in a normal run
    (tmp_path / 'INDEX.md').unlink()
    snap = snapshot(tmp_path)
    out = run_main(monkeypatch, tmp_path, items, '--index-only', capsys=capsys)
    assert snapshot(tmp_path) == snap                     # bytes AND mtimes
    idx = (tmp_path / 'INDEX.md').read_text(encoding='utf-8')
    assert 'a1' in idx and 'b2' in idx
    assert 'no journal touched' in out


def test_index_only_does_not_create_missing_journal(tmp_path, monkeypatch):
    run_main(monkeypatch, tmp_path, [item()], '--index-only')
    assert [f.name for f in tmp_path.glob('*.md')] == ['INDEX.md']


def test_item_without_journal_gets_one(tmp_path, monkeypatch, capsys):
    out = run_main(monkeypatch, tmp_path, [item()], capsys=capsys)
    files = [f.name for f in tmp_path.glob('*.md') if f.name != 'INDEX.md']
    assert files == ['it1-fix-the-widget.md']
    text = (tmp_path / files[0]).read_text(encoding='utf-8')
    assert '### 2026-11-01T00:00:00  ·  abc  ·  interactive' in text and 'first' in text
    assert '1 created, 0 got appended notes, 0 left alone' in out


def test_item_without_notes_or_journal_gets_nothing(tmp_path, monkeypatch):
    run_main(monkeypatch, tmp_path, [item(notes=[])])
    assert [f.name for f in tmp_path.glob('*.md')] == ['INDEX.md']


def test_changed_item_text_finds_journal_by_id(tmp_path, monkeypatch):
    items = [item(text='Old title')]
    run_main(monkeypatch, tmp_path, items)
    items[0]['text'] = 'A completely different title'
    items[0]['notes'].append(note('2026-11-02T00:00:00', 'second'))
    run_main(monkeypatch, tmp_path, items)
    files = sorted(f.name for f in tmp_path.glob('*.md') if f.name != 'INDEX.md')
    assert files == ['it1-old-title.md']                  # no second file under the new slug
    assert 'second' in (tmp_path / files[0]).read_text(encoding='utf-8')


def test_note_already_in_other_journal_of_same_id_is_not_duplicated(tmp_path, monkeypatch):
    items = [item(text='New title')]
    run_main(monkeypatch, tmp_path, items)                # it1-new-title.md
    old = tmp_path / 'it1-aaa-old.md'                     # sorts first, not the derived name
    old.write_text('# old\n\n### 2026-11-01T00:00:00  ·  abc  ·  interactive\n\nfirst\n', encoding='utf-8')
    exact = tmp_path / journal_name(items[0])
    exact.unlink()
    items[0]['notes'].append(note('2026-11-02T00:00:00', 'second'))
    run_main(monkeypatch, tmp_path, items)
    files = sorted(f.name for f in tmp_path.glob('*.md') if f.name != 'INDEX.md')
    assert files == ['it1-aaa-old.md']
    t = old.read_text(encoding='utf-8')
    assert t.count('first') == 1 and t.count('second') == 1


def test_id_prefix_does_not_match_a_longer_id(tmp_path, monkeypatch):
    other = tmp_path / 'it10-something.md'
    other.write_text('# other item\n', encoding='utf-8')
    before = snapshot(tmp_path)
    run_main(monkeypatch, tmp_path, [item('it1')])
    assert snapshot(tmp_path)[other.name] == before[other.name]
    assert (tmp_path / 'it1-fix-the-widget.md').exists()


def test_two_notes_with_same_timestamp_both_kept_and_stable(tmp_path, monkeypatch):
    items = [item(notes=[note('2026-11-01T00:00:00', 'note-alpha'), note('2026-11-01T00:00:00', 'note-beta')])]
    run_main(monkeypatch, tmp_path, items)
    j = tmp_path / journal_name(items[0])
    first = j.read_bytes()
    run_main(monkeypatch, tmp_path, items)
    assert j.read_bytes() == first
    items[0]['notes'].append(note('2026-11-01T00:00:00', 'note-gamma'))
    run_main(monkeypatch, tmp_path, items)
    t = j.read_text(encoding='utf-8')
    assert t.count('note-alpha') == 1 and t.count('note-beta') == 1 and t.count('note-gamma') == 1
