"""tools/_mc_restart.ps1 was removed (MC-1179 pass 12 second wave, TL13): it
killed every python.exe whose command line held server.py and had no caller.
Nothing tracked may name it again."""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAME = '_mc_' + 'restart.ps1'   # split so this file does not name it


def _tracked():
    out = subprocess.run(['git', 'ls-files'], cwd=ROOT, capture_output=True,
                         text=True, timeout=60).stdout.splitlines()
    return [p for p in out if (ROOT / p).is_file()]


def test_helper_is_gone():
    assert not (ROOT / 'tools' / NAME).exists()
    assert 'tools/' + NAME not in _tracked()


def test_no_tracked_file_names_it():
    stem = NAME[:-4]
    hits = []
    for rel in _tracked():
        if (rel.startswith('docs/_review/') or rel in ('CHANGELOG.md', 'tests/test_mc_restart_helper_removed.py')
                or rel.endswith('.png')):
            continue
        try:
            text = (ROOT / rel).read_text(encoding='utf-8')
        except (UnicodeDecodeError, OSError):
            continue
        if stem in text:
            hits.append(rel)
    assert hits == []
