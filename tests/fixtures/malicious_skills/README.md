# Malicious skill fixtures (MC-912)

These are **test fixtures only** — inert sample SKILL.md folders used by
`tests/test_skill_import_guard.py` to exercise the security scanner
(`skill_import_guard.py`). None of them are ever installed into a real
`~/.claude/skills/` directory or executed; the tests only feed their text
through `scan_skill_dir()` / `scan_text()`.

Each subfolder is named after the detector category it is meant to trip, plus
one `benign-skill` negative control that must produce zero findings.
