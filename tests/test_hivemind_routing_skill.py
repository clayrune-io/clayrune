"""BUILTINS-4 HR-6a: the mc-hivemind-routing built-in directive.

Pins the skill text to mc.hivemind_routing so the documented contract cannot
drift from the validator, and proves the existing built-in installer ships it.
No test writes under the real home: GLOBAL_SKILLS_DIR is redirected to tmp.
"""
import re
from pathlib import Path

import pytest

from mc import hivemind_fallback, hivemind_routing, skills

ROOT = Path(__file__).resolve().parents[1]
BUILTIN_ROOT = ROOT / 'data' / 'skills' / 'builtin'
SKILL_DIR = BUILTIN_ROOT / 'mc-hivemind-routing'
SKILL_MD = SKILL_DIR / 'SKILL.md'


@pytest.fixture(scope='module')
def text() -> str:
    return SKILL_MD.read_text(encoding='utf-8')


def _section(text: str, start: str, end: str) -> str:
    return text.split(start, 1)[1].split(end, 1)[0]


def _bullet_fields(block: str) -> set[str]:
    return set(re.findall(r'^- `([a-z_]+)`', block, flags=re.M))


def test_frontmatter_parses_and_names_the_directory(text):
    fm = skills._parse_frontmatter(text)
    assert fm.get('name') == 'mc-hivemind-routing'
    desc = fm.get('description') or ''
    assert 'TRIGGER' in desc and 'plan_routing' in desc


def test_existing_installer_discovers_and_installs_it(tmp_path, monkeypatch):
    home_skills = tmp_path / 'skills'
    monkeypatch.setattr(skills, 'GLOBAL_SKILLS_DIR', home_skills)
    first = skills.install_builtins(BUILTIN_ROOT)
    assert 'mc-hivemind-routing' in first['installed']
    installed = home_skills / 'mc-hivemind-routing'
    assert (installed / 'SKILL.md').read_text(encoding='utf-8') == SKILL_MD.read_text(encoding='utf-8')
    assert (installed / skills._INSTALL_MARKER).is_file()
    # Second boot is a no-op for an unmodified copy (checksum-preserved path).
    second = skills.install_builtins(BUILTIN_ROOT)
    assert 'mc-hivemind-routing' in second['skipped']


def test_documented_plan_and_unit_fields_match_validator(text):
    plan_block = _section(text, 'Plan (top-level) fields', 'Unit fields:')
    unit_block = _section(text, 'Unit fields:', 'Any other field is refused')
    assert _bullet_fields(plan_block) == hivemind_routing._PLAN_KEYS
    assert _bullet_fields(unit_block) == hivemind_routing._UNIT_KEYS


def test_documented_enums_match_fallback_vocabulary(text):
    unit_block = _section(text, 'Unit fields:', 'Any other field is refused')
    wt = unit_block.split('`work_type`', 1)[1].split('\n- ', 1)[0]
    cx = unit_block.split('`complexity`', 1)[1].split('\n\n', 1)[0]
    assert set(re.findall(r'`([a-z]+)`', wt)) == set(hivemind_fallback.WORK_TYPES)
    assert set(re.findall(r'`([a-z]+)`', cx)) == set(hivemind_fallback.COMPLEXITIES)


def test_documented_result_keys_and_error_codes_exist(text):
    result = hivemind_routing.plan_routing(
        None, app_config={}, read_character=lambda _n: None)
    assert result['route'] == 'invalid'
    for key in result:
        assert f'`{key}`' in text, key
    src = Path(hivemind_routing.__file__).read_text(encoding='utf-8')
    codes = set(re.findall(r'`([a-z]+_[a-z_]+)`', _section(text, 'Typical codes:', '\n\n')))
    assert codes, 'no error codes documented'
    for code in codes:
        assert f"'{code}'" in src, f'documented code {code} not emitted by validator'
    for route in ('ordinary', 'hivemind', 'invalid'):
        assert f'- `{route}`' in text


def test_example_snippet_uses_the_real_call_shape(text):
    assert 'plan_routing(\n    plan, app_config=config, project=project,' in text
    assert "characters.read_character('global', name, include_body=False)" in text
    # Same lookup the roster module uses by default, so the snippet is not a guess.
    roster_src = (ROOT / 'mc' / 'hivemind_roster.py').read_text(encoding='utf-8')
    assert "_characters.read_character('global', name, include_body=False)" in roster_src
    assert "/api/hivemind/create" in text


def test_directive_carries_the_routing_rules(text):
    for rule in ['POST `payload` ONCE', 'Do NOT also dispatch',
                 'Never dispatch the part of an invalid plan',
                 'Never raise it', 'never a unit and never counts as\n  a worker',
                 '`held_units`', 'hold_reason']:
        assert rule in text, rule


def test_no_operator_specific_or_persona_text(text):
    lowered = text.lower()
    for leak in ['ron', 'dave', 'levir', 'leviran', 'c:\\users', 'gmail', '5199']:
        assert not re.search(rf'\b{re.escape(leak)}\b', lowered), leak
