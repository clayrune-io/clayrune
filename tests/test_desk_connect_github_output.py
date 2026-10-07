"""The generated-output policy never hides source, including shipped source in dist/."""
from pathlib import Path
import json
import shutil
import subprocess
import pytest
from mc.desk_connect import github_install_output as output, github_manifest as manifest
from mc.desk_connect import github_activation as activation
from mc.desk_connect.mcp_errors import ActivationError


@pytest.fixture
def installed(tmp_path):
    (tmp_path/'src').mkdir()
    (tmp_path/'src/main.js').write_text('reviewed')
    (tmp_path/'dist').mkdir()
    (tmp_path/'dist/shipped.js').write_text('reviewed build')
    source=manifest.inventory(str(tmp_path))
    (tmp_path/'dist/generated.js').write_text('generated')
    (tmp_path/'dist/assets').mkdir()
    (tmp_path/'dist/assets/build.txt').write_text('built')
    (tmp_path/'package-lock.json').write_text('generated lock')
    return {'directory':str(tmp_path),'source_files':source,'file_checks':output.POLICY}


def test_record_and_check_sources_with_mixed_build_directory(installed):
    saved=output.capture(installed['directory'],installed['source_files'])
    assert set(saved['skip'])=={'dist/generated.js','dist/assets','package-lock.json'}
    assert len(saved['output_files'])==3 and output.source_matches(installed,saved)
    (Path(installed['directory'])/'dist/shipped.js').write_text('modified reviewed build')
    assert not output.source_matches(installed,saved)


def test_added_source_file_refuses_start(installed):
    saved=output.capture(installed['directory'],installed['source_files'])
    (Path(installed['directory'])/'src/extra.js').write_text('new unreviewed file')
    assert not output.source_matches(installed,saved)


@pytest.mark.parametrize('path',['src','src/main.js','dist','../outside','/absolute','src\\main.js','.'])
def test_corrupt_skip_cannot_hide_reviewed_source(installed,path):
    saved=output.capture(installed['directory'],installed['source_files'])
    saved['skip']=[path]
    assert not output.source_matches(installed,saved)


def test_corrupt_source_record_refuses_start(installed):
    saved=output.capture(installed['directory'],installed['source_files'])
    saved['source_files']={}
    assert not output.source_matches(installed,saved)


@pytest.mark.parametrize('bound,value',[('MAX_ENTRIES',1),('MAX_BYTES',1)])
def test_output_record_has_independent_limits(installed,monkeypatch,bound,value):
    monkeypatch.setattr(output,bound,value)
    with pytest.raises(ActivationError,match='too large'):
        output.capture(installed['directory'],installed['source_files'])


def test_missing_source_refuses_save(installed):
    (Path(installed['directory'])/'src/main.js').unlink()
    with pytest.raises(ActivationError,match='changed'):
        output.capture(installed['directory'],installed['source_files'])


def test_install_output_junction_refuses_save(installed,monkeypatch):
    link=Path(installed['directory'])/'dist/assets'
    monkeypatch.setattr(Path,'is_junction',lambda path:path==link,raising=False)
    with pytest.raises(ActivationError,match='Junction'):
        output.capture(installed['directory'],installed['source_files'])


def test_internal_npm_link_recorded_but_external_link_refused(installed,tmp_path):
    link=Path(installed['directory'])/'bin-link'
    try:
        link.symlink_to(Path('dist')/'generated.js')
    except OSError:
        pytest.skip('Creating symbolic links needs platform permission')
    saved=output.capture(installed['directory'],installed['source_files'])
    assert saved['output_files']['bin-link']=={'link':'dist/generated.js'}
    link.unlink()
    link.symlink_to(tmp_path.parent)
    with pytest.raises(ActivationError,match='inside'):
        output.capture(installed['directory'],installed['source_files'])


def test_legacy_approval_still_checks_entire_tree(installed,monkeypatch,tmp_path):
    op={**installed};op.pop('file_checks')
    full=manifest.inventory(op['directory'])
    saved=tmp_path.parent/(tmp_path.name+'-manifest.json')
    saved.write_text(json.dumps(full))
    monkeypatch.setattr(activation,'_manifest_path',lambda op:saved)
    assert activation.problem(op)==''
    (Path(op['directory'])/'dist/generated.js').write_text('modified output')
    assert activation.problem(op)


def test_npm_can_load_empty_install_configuration(tmp_path,monkeypatch):
    """An offline npm command catches the identical-user/global-path startup failure."""
    from mc.desk_connect import custom_npm_scripts as scripts
    npm=shutil.which('npm.cmd') or shutil.which('npm')
    node=shutil.which('node')
    if not npm or not node:
        pytest.skip('npm and Node are required for this offline configuration check')
    home=tmp_path/'home';home.mkdir();(home/'tmp').mkdir()
    cwd=tmp_path/'repo';cwd.mkdir()
    monkeypatch.setenv('NPM_CONFIG_USERCONFIG','inherited-unapproved.npmrc')
    env=scripts.script_env({'script':'install','package':'fixture','version':'1'},home,str(Path(node).parent),cwd)
    done=subprocess.run([npm,'config','get','userconfig'],cwd=cwd,env=env,
                        capture_output=True,text=True,timeout=15,check=False)
    assert done.returncode==0,done.stderr
    assert Path(done.stdout.strip())==home/'user.npmrc'


@pytest.mark.parametrize('lockfile',[None,'package-lock.json','npm-shrinkwrap.json'])
def test_dependency_step_preserves_reviewed_lockfile(tmp_path,monkeypatch,lockfile):
    from mc.desk_connect import github_install_steps as steps
    (tmp_path/'package.json').write_text('{}')
    if lockfile:
        (tmp_path/lockfile).write_text('{}')
    monkeypatch.setattr(steps.installer,'_resolve_npm',lambda:'npm')
    [step]=steps.dependencies(str(tmp_path))
    assert step['id']=='dependencies:0'
    assert step['argv']==['npm','ci' if lockfile else 'install','--no-audit','--no-fund','--ignore-scripts']
