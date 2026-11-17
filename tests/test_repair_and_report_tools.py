"""tools/repair-reframed-skill-frontmatter.py and tools/report-oneshot-log-rows.py
(MC-1179 pass 12 TL31, TL32)."""
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'tools' / filename)
    mod = importlib.util.module_from_spec(spec)
    before = sys.path[:]
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.path[:] = before
    return mod


repair = _load('repair_frontmatter_tool', 'repair-reframed-skill-frontmatter.py')


def test_yaml_scalar_quotes_what_would_not_parse_bare():
    yaml = pytest.importorskip('yaml')
    for v in ['TRIGGER when the build fails: check the lockfile first', 'a # b', '-x', '"q"',
              ' padded ', 'ends with:', 'plain words only', '']:
        assert yaml.safe_load('k: ' + repair._yaml_scalar(v)) == {'k': v if v else ''}
    assert repair._yaml_scalar('plain words only') == 'plain words only'


def test_apply_fix_writes_a_description_with_a_colon_that_still_parses(tmp_path):
    yaml = pytest.importorskip('yaml')
    p = tmp_path / 'SKILL.md'
    p.write_text('---\nname: s\ndescription: old\n---\n\nbody\n', encoding='utf-8')
    desc = 'TRIGGER when the build fails: check the lockfile first'
    repair.apply_fix(p, {'body_lines_removed': 0, 'proposed_description': desc})
    block = p.read_text(encoding='utf-8').split('---')[1]
    assert yaml.safe_load(block) == {'name': 's', 'description': desc}


def test_one_unreadable_skill_does_not_end_the_run(tmp_path, monkeypatch, capsys):
    bad = tmp_path / 'a' / 'SKILL.md'
    bad.mkdir(parents=True)  # a directory: read_text raises
    monkeypatch.setattr(repair, '_iter_skill_md', lambda scope: [('project:x', bad)])
    monkeypatch.setattr(sys, 'argv', ['repair'])
    assert repair.main() == 0
    out = capsys.readouterr().out
    assert 'unreadable' in out and '1 file(s) flagged' in out


def test_report_reads_the_cap_from_the_data_root_config(tmp_path, monkeypatch, capsys):
    report = _load('report_oneshot_tool', 'report-oneshot-log-rows.py')
    (tmp_path / 'data' / 'projects').mkdir(parents=True)
    (tmp_path / 'config.json').write_text('{"agent_log_max_entries": 7}', encoding='utf-8')
    monkeypatch.setenv('MC_DATA_DIR', str(tmp_path))
    monkeypatch.setattr(sys, 'argv', ['report'])
    report.main()
    assert 'agent_log_max_entries=7' in capsys.readouterr().out
