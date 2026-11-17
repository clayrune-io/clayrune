"""MC-1179 TL29: the link check counts a markdown link as a way in to a note,
and a note that links to itself is still an orphan."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load():
    path = ROOT / 'tools' / 'memory-link-check.py'
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location('memory_link_check_under_test', path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _orphans(tmp_path, monkeypatch, capsys, notes):
    for name, text in notes.items():
        (tmp_path / name).write_text(text, encoding='utf-8')
    mod = _load()
    monkeypatch.setattr(sys, 'argv', ['memory-link-check.py', '--dir', str(tmp_path)])
    assert mod.main() == 0
    out = capsys.readouterr().out
    return out.split('ORPHANS')[1].splitlines()[1:]


def test_markdown_link_from_the_index_is_a_way_in(tmp_path, monkeypatch, capsys):
    rows = _orphans(tmp_path, monkeypatch, capsys, {
        'MEMORY.md': '- [Cited note](cited_note.md) -- one line\n',
        'cited_note.md': '---\nname: cited-note\n---\nbody\n',
        'lonely_note.md': '---\nname: lonely-note\n---\nbody\n',
    })
    names = [r.strip() for r in rows if r.strip()]
    assert 'lonely_note.md' in names
    assert 'cited_note.md' not in names


def test_a_note_linking_only_to_itself_is_still_an_orphan(tmp_path, monkeypatch, capsys):
    rows = _orphans(tmp_path, monkeypatch, capsys, {
        'MEMORY.md': '# idx\n',
        'selfish.md': '---\nname: selfish\n---\nsee [[selfish]] and [me](selfish.md)\n',
    })
    assert 'selfish.md' in [r.strip() for r in rows]
