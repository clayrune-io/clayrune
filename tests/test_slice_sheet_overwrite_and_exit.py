"""tools/avatars/slice_sheet.py (MC-1179 pass 12 TL40): a replaced avatar is named
on stderr, and a cell keyed badly still gets written but ends the run with exit 2."""
import importlib.util
import sys
from pathlib import Path

import pytest
from PIL import Image

TOOL = Path(__file__).resolve().parent.parent / 'tools' / 'avatars' / 'slice_sheet.py'


def _load():
    spec = importlib.util.spec_from_file_location('slice_sheet_under_test', TOOL)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _sheet(path, figure):
    """One 100x100 cell, flat magenta. `figure` paints a solid square of that
    many px, centred, so its share of the cell is figure^2 / 100^2."""
    im = Image.new('RGB', (100, 100), (255, 0, 255))
    if figure:
        lo = (100 - figure) // 2
        for x in range(lo, lo + figure):
            for y in range(lo, lo + figure):
                im.putpixel((x, y), (120, 90, 60))
    im.save(path)


def _run(mod, monkeypatch, sheet, name):
    monkeypatch.setattr(sys, 'argv', ['slice_sheet.py', str(sheet), '--names', name,
                                      '--grid', '1x1', '--size', '32'])
    with pytest.raises(SystemExit) as e:
        mod.main()
        raise SystemExit(0)
    return e.value.code


def test_good_cell_exits_zero_and_replacing_is_named_on_stderr(tmp_path, monkeypatch, capsys):
    mod = _load()
    monkeypatch.setattr(mod, 'OUT', tmp_path / 'out')
    sheet = tmp_path / 's.png'
    _sheet(sheet, 55)   # ~30% of the cell
    assert _run(mod, monkeypatch, sheet, 'figa') == 0
    first = capsys.readouterr()
    assert 'replacing' not in first.err
    assert _run(mod, monkeypatch, sheet, 'figa') == 0
    second = capsys.readouterr()
    assert 'figa.webp' in second.err and 'replacing' in second.err


def test_badly_keyed_cell_is_written_then_exit_2(tmp_path, monkeypatch, capsys):
    mod = _load()
    monkeypatch.setattr(mod, 'OUT', tmp_path / 'out')
    sheet = tmp_path / 's.png'
    _sheet(sheet, 0)    # all backdrop: the figure share is 0, outside 20-55%
    assert _run(mod, monkeypatch, sheet, 'empty') == 2
    cap = capsys.readouterr()
    assert (tmp_path / 'out' / 'empty.webp').exists()
    assert 'keyed badly' in cap.err and 'empty' in cap.err
