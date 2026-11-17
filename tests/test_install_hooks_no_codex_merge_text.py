"""tools/guards/install_hooks.py: its help and docstrings no longer describe a Codex
merge of the user's real hooks.json, which the module docstring says does not exist
(MC-1179 pass 12 second wave, TL25)."""
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / 'tools' / 'guards' / 'install_hooks.py'


def test_help_does_not_describe_a_codex_merge():
    out = subprocess.run([sys.executable, str(SCRIPT), '--help'], capture_output=True, text=True, timeout=30).stdout
    assert '--real-home' in out
    assert 'codex merge' not in ' '.join(out.split()).lower()


def test_source_has_no_codex_merge_claim_outside_the_module_docstring_that_denies_it():
    body = SCRIPT.read_text(encoding='utf-8')
    assert 'for the codex merge' not in body
    assert "malformed real" not in body
    assert "Codex's is a merge" not in body
