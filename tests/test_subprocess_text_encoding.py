"""Guard against the cp1252 mojibake class (2026-09-22).

`subprocess.run(..., text=True)` with no `encoding=` decodes the child's pipes
with `locale.getpreferredencoding()` — cp1252 on a default Windows box. Every
CLI we shell out to (claude, codex, gemini, qwen, git, npm) emits UTF-8, so
their own text reached the UI byte-mangled: the Providers panel showed
"Not logged in Ã‚Â· Please run /login" on a clean Windows VM because the CLI's
U+00B7 (bytes C2 B7) was decoded as two cp1252 characters.

The fix is one keyword, so the regression guard is structural: no text-mode
subprocess call in the backend may omit an explicit encoding.
"""
import ast
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SPAWNERS = {'run', 'Popen', 'check_output', 'call', 'check_call'}


def _text_mode_calls_without_encoding(path: Path):
    try:
        tree = ast.parse(path.read_text(encoding='utf-8'))
    except SyntaxError:  # pragma: no cover
        return []
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not isinstance(fn, ast.Attribute) or fn.attr not in SPAWNERS:
            continue
        if getattr(fn.value, 'id', None) != 'subprocess':
            continue
        kw = {k.arg for k in node.keywords if k.arg}
        text_mode = bool(kw & {'text', 'universal_newlines', 'errors'})
        if text_mode and 'encoding' not in kw:
            out.append(node.lineno)
    return out


def _backend_files():
    files = [REPO / 'server.py']
    files += sorted((REPO / 'mc').rglob('*.py'))
    return [f for f in files if f.exists()]


class TestSubprocessTextEncoding(unittest.TestCase):

    def test_no_text_mode_subprocess_without_explicit_encoding(self):
        offenders = []
        for f in _backend_files():
            for lineno in _text_mode_calls_without_encoding(f):
                offenders.append(f'{f.relative_to(REPO).as_posix()}:{lineno}')
        self.assertEqual(
            offenders, [],
            'text-mode subprocess call(s) with no encoding= — on Windows these '
            "decode the child's UTF-8 output as cp1252 (mojibake). Add "
            "encoding='utf-8', errors='replace':\n  " + '\n  '.join(offenders))

    def test_guard_detects_a_planted_offender(self):
        """The guard must actually fire — otherwise it is green by accident."""
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / 'planted.py'
            p.write_text(
                'import subprocess\n'
                'subprocess.run(["x"], capture_output=True, text=True)\n',
                encoding='utf-8')
            self.assertEqual(_text_mode_calls_without_encoding(p), [2])

    def test_claude_auth_probe_decodes_utf8(self):
        """The site the bug was reported on, named explicitly."""
        src = (REPO / 'mc' / 'blueprints' / 'agent_routes.py')
        tree = ast.parse(src.read_text(encoding='utf-8'))
        probes = [n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef)
                  and n.name == '_run_claude_auth_probe']
        self.assertEqual(len(probes), 1, '_run_claude_auth_probe not found')
        calls = [n for n in ast.walk(probes[0])
                 if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute)
                 and n.func.attr in SPAWNERS]
        self.assertTrue(calls, 'auth probe spawns nothing?')
        for c in calls:
            enc = [k for k in c.keywords if k.arg == 'encoding']
            self.assertTrue(enc, f'line {c.lineno}: no encoding=')
            self.assertEqual(enc[0].value.value, 'utf-8')


if __name__ == '__main__':
    unittest.main()
