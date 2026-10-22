"""Both entry points must opt into pywebview's default-off text selection."""
import ast
from pathlib import Path

import pytest


@pytest.mark.parametrize('filename', ['app.py', 'mc/popout_windows.py'])
def test_native_window_enables_document_selection(filename):
    tree = ast.parse((Path(__file__).resolve().parents[1] / filename).read_text(encoding='utf-8'))
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Attribute) and node.func.attr == 'create_window']
    assert calls
    for call in calls:
        options = {kw.arg: kw.value for kw in call.keywords}
        assert 'text_select' in options
        assert ast.literal_eval(options['text_select']) is True
