"""The demo shoot may only dispatch characters that ship with Clayrune."""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _prep():
    spec = importlib.util.spec_from_file_location('demo_shoot_prep', ROOT / 'tools' / 'demo-shoot' / 'prep.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_every_demo_character_is_a_shipped_builtin():
    shipped = {p.stem for p in (ROOT / 'data' / 'agents' / 'builtin').glob('*.md')}
    names = [c.split(':', 1)[1] for _, c, _ in _prep().CHARACTER_TASKS if c.startswith('global:')]
    assert names, 'prep.py names no global characters'
    assert not [n for n in names if n not in shipped]
