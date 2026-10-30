"""Mapping failures must precede native diff-cover's zero-denominator default."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from mc.dev_harness_checks.coverage import source_report, validate_diff
from mc.dev_harness_checks.recipes import make_step
from mc import dev_harness_runner as runner
from test_dev_harness_diff import git, repo


def xml(name='src/app.py', lines=(1, 2), version='7.10.6'):
    return ('<coverage version="'+version+'"><sources><source>.</source></sources><packages><package><classes>'
            '<class filename="'+name+'"><lines>'+''.join('<line number="'+str(n)+'" hits="1"/>' for n in lines)+
            '</lines></class></classes></package></packages></coverage>').encode()


@pytest.fixture
def source(tmp_path):
    (tmp_path/'src').mkdir()
    (tmp_path/'src/app.py').write_text('a = 1\nb = 2\n')
    (tmp_path/'src/app.ts').write_text('const a = 1;\nconst b = 2;\n')
    return tmp_path


def test_python_native_mapping(source):
    assert source_report(xml(), 'coverage.py-xml-7.10.6', source, {'src/app.py'}, ['src']) == {'src/app.py':{1,2}}


def test_javascript_native_mapping(source):
    raw = b'TN:\nSF:src/app.ts\nDA:1,1\nDA:2,0\nend_of_record\n'
    assert source_report(raw, 'lcov-v1', source, {'src/app.ts'}, ['src']) == {'src/app.ts':{1,2}}


def test_python_declared_relative_source_root(source):
    raw = xml('app.py').replace(b'<source>.</source>', b'<source>src</source>')
    assert source_report(raw, 'coverage.py-xml-7.10.6', source, {'src/app.py'}, ['src']) == {'src/app.py':{1,2}}


def test_absolute_lcov_inside_candidate_and_project_root_scope(source):
    raw = ('SF:'+(source/'src/app.ts').as_posix()+'\nDA:1,1\nend_of_record\n').encode()
    assert source_report(raw, 'lcov-v1', source, {'src/app.ts'}, ['.']) == {'src/app.ts':{1}}


@pytest.mark.parametrize('raw', [b'', b'<coverage/>', xml('foreign.py'), xml('../src/app.py'),
    xml(lines=()), xml(lines=(1,99)), xml(lines=(1,1)), xml(version='99.0.0'),
    b'<!DOCTYPE x [<!ENTITY a "x">]><coverage/>'])
def test_empty_foreign_wrong_version_invalid_python_report(source, raw):
    with pytest.raises((ValueError, KeyError)):
        source_report(raw, 'coverage.py-xml-7.10.6', source, {'src/app.py'}, ['src'])


@pytest.mark.parametrize('raw', [b'', b'SF:src/app.ts\nend_of_record\n',
    b'SF:src/app.ts\nDA:1,1\n', b'SF:foreign.ts\nDA:1,1\nend_of_record\n',
    b'SF:src/app.ts\nDA:1,-1\nend_of_record\n'])
def test_invalid_lcov_cannot_pass(source, raw):
    with pytest.raises((ValueError, KeyError)):
        source_report(raw, 'lcov-v1', source, {'src/app.ts'}, ['src'])


def test_new_source_cannot_disappear_from_report(source):
    (source/'src/new.py').write_text('new = 1\n')
    with pytest.raises(ValueError, match='missing_changed_source_coverage'):
        source_report(xml(), 'coverage.py-xml-7.10.6', source, {'src/app.py','src/new.py'}, ['src'])


@pytest.mark.parametrize('identity', ['coverage-python', 'coverage-javascript'])
@pytest.mark.parametrize('case', ['positive', 'finding'])
def test_diff_cover_fixtures(identity, case):
    row = json.loads((Path(__file__).parent/'fixtures/dev_harness'/(identity+'.json')).read_text())[case]
    missing = [2] if row['uncovered'] else []
    raw = json.dumps({'src_stats':{'src/app.py':{'covered_lines':[1] if missing else [1,2],
        'violation_lines':missing}}, 'total_num_lines':row['lines'], 'total_num_violations':row['uncovered'],
        'total_percent_covered':row['percent']}).encode()
    result = validate_diff(raw, {'src/app.py':{1,2}}, 75)
    assert result['status'] == ('pass' if case == 'positive' else 'fail')
    assert result['branch_coverage'] == 'not_evaluated'


@pytest.mark.parametrize('data', [
    {'src_stats':{},'total_num_lines':0,'total_num_violations':0,'total_percent_covered':100},
    {'src_stats':{'foreign.py':{'covered_lines':[1],'violation_lines':[]}},'total_num_lines':1,'total_num_violations':0,'total_percent_covered':100},
    {'src_stats':{'src/app.py':{'covered_lines':[99],'violation_lines':[]}},'total_num_lines':1,'total_num_violations':0,'total_percent_covered':100},
])
def test_noop_foreign_diff_report_is_error(data):
    with pytest.raises(ValueError):
        validate_diff(json.dumps(data).encode(), {'src/app.py':{1,2}}, 75)


def coverage_step(repo, mode='positive', identity='coverage-python'):
    (repo/'src').mkdir()
    suffix = '.py' if identity == 'coverage-python' else '.ts'
    app, new = 'src/app'+suffix, 'src/new'+suffix
    (repo/app).write_text('a = 1\n')
    git(repo, 'add', app)
    git(repo, 'commit', '-qm', 'source baseline')
    git(repo, 'checkout', '-qb', 'incoming')
    (repo/app).write_text('a = 2\n')
    (repo/new).write_text('b = 1\n')
    git(repo, 'add', app, new)
    git(repo, 'commit', '-qm', 'changed sources')
    git(repo, 'checkout', '-q', 'master')
    tools = repo/'.clayrune/tool-fixtures'
    tools.mkdir(parents=True)
    producer = tools/'producer.py'
    names = [app] if mode == 'missing-new' else ['foreign.py'] if mode == 'foreign' else [app,new]
    report_name = '.clayrune/coverage/report.xml' if identity == 'coverage-python' else '.clayrune/coverage/lcov.info'
    if identity == 'coverage-python':
        raw = '<coverage version="7.10.6"><sources><source>.</source></sources><packages><package><classes>'
        raw += ''.join('<class filename="'+n+'"><lines><line number="1" hits="1"/></lines></class>' for n in names)
        raw += '</classes></package></packages></coverage>'
    else:
        raw = ''.join('TN:\nSF:'+n+'\nDA:1,1\nend_of_record\n' for n in names)
    if mode == 'empty':
        raw = ''
    producer.write_text('import sys\nfrom pathlib import Path\n'
        'if "--version" in sys.argv:\n print('+repr('7.10.6' if identity == 'coverage-python' else '3.2.4')+'); sys.exit(0)\n'
        + ('raise SystemExit(2)\n' if mode == 'producer-crash' else '')
        + ('if "xml" in sys.argv:\n Path(sys.argv[-1]).write_text('+repr(raw)+')\n' if identity == 'coverage-python'
           else 'Path('+repr(report_name)+').write_text('+repr(raw)+')\n'))
    diff = tools/'diff.py'
    missing = [1] if mode == 'finding' else []
    data = {'src_stats':{app:{'covered_lines':[] if missing else [1],'violation_lines':missing},
                        new:{'covered_lines':[1],'violation_lines':[]}},
            'total_num_lines':2,'total_num_violations':len(missing),'total_percent_covered':50 if missing else 100}
    diff.write_text('import sys\nfrom pathlib import Path\n'
        'if "--version" in sys.argv:\n print('+repr('9.0.0' if mode == 'wrong-diff-version' else '10.2.0')+'); sys.exit(0)\n'
        + ('raise SystemExit(2)\n' if mode == 'diff-crash' else '')
        + 'path = next(v[len("--format=json:"):] for v in sys.argv if v.startswith("--format=json:"))\n'
        + 'Path(path).write_text('+repr(json.dumps(data))+')\nraise SystemExit('+str(1 if missing else 0)+')\n')
    return make_step(identity, launcher=[sys.executable,str(producer)], targets=[], configs=[],
        tool_roots=[str(producer),str(diff)],command_args=['run'],
        coverage={'report':report_name,'format':'coverage.py-xml-7.10.6' if identity == 'coverage-python' else 'lcov-v1',
            'source_roots':['src'],'floor_percent':75,'diff_launcher':[sys.executable,str(diff)]})


@pytest.mark.parametrize('identity', ['coverage-python','coverage-javascript'])
@pytest.mark.parametrize('mode,status', [('positive','pass'),('finding','fail'),('producer-crash','error'),
    ('diff-crash','error'),('wrong-diff-version','error'),('missing-new','error'),('foreign','error'),('empty','error')])
def test_frozen_coverage_pipeline(repo, identity, mode, status):
    step = coverage_step(repo, mode, identity)
    registered, done = [], []
    result = runner.evaluate(project={'id':'fixture','project_path':str(repo)}, steps=[step],
        target='master',incoming='incoming',logs=repo/'.clayrune/logs',
        register=lambda proc, name:registered.append(proc.pid),unregister=done.append)
    assert result['status'] == status, result
    assert registered == done
    assert all(p.read_bytes() == b'' for p in (repo/'.clayrune/logs').glob('*.log'))
    if status == 'pass':
        assert result['results'][0]['changed_lines'] == 2
        assert result['results'][0]['source_digest'] == result['source_manifest_digest']
        assert result['results'][0]['diff_cover_version'] == '10.2.0'
        assert not result['authorizes_merge']


def test_pending_index_coverage_uses_actual_b_to_t(repo):
    from mc.dev_harness_pending_merge import freeze_pending
    step = coverage_step(repo)
    git(repo, 'merge', '--no-commit', '--no-ff', 'incoming')
    frozen = freeze_pending(repo)
    result = runner.evaluate(project={'id':'fixture','project_path':str(repo)}, steps=[step],
        _frozen=frozen, logs=repo/'.clayrune/logs')
    assert result['status'] == 'pass', result
    assert result['source']['kind'] == 'pending_merge'
    assert result['source']['T'] == git(repo, 'write-tree')
    assert result['results'][0]['changed_lines'] == 2
