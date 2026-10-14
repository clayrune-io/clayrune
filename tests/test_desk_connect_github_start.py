"""Clone-local inference, including the YouTube repository's declared metadata."""
import pytest
from mc.desk_connect.github_start_command import infer


def test_youtube_package_bin_and_missing_dist():
    package = {'name': 'zubeid-youtube-mcp-server',
               'bin': {'zubeid-youtube-mcp-server': 'dist/cli.js'},
               'scripts': {'start': 'node ./dist/index.js', 'build': 'tsc'}}
    got = infer(package, {'package.json': 'sha', 'src/cli.ts': 'sha'})
    assert got == {'command': 'node', 'args': ['dist/cli.js'],
                   'command_source': 'package.json bin', 'build_required': True,
                   'required_steps': ['script:build']}


def test_script_start_and_committed_file():
    assert infer({'scripts': {'start': 'node ./server.js'}}, {'server.js':'sha'})['args'] == ['server.js']


def test_ambiguous_bins_use_start():
    got = infer({'bin': {'a':'a.js','b':'b.js'}, 'scripts': {'start':'node ./b.js'}}, {'a.js':'sha','b.js':'sha'})
    assert got['args'] == ['b.js'] and not got['build_required']


@pytest.mark.parametrize('path', ['../outside.js','/tmp/evil.js','C:/evil.js','https://x/a.js','evil.js;whoami','a\u202e.js'])
def test_unsafe_paths_not_inferred(path):
    assert infer({'bin':path}, {path:'sha'}) == {}


@pytest.mark.parametrize('start', ['npx -y example','node a.js && whoami','node -e "whoami"','npm start','node "unterminated'])
def test_no_shell_or_registry_inference(start):
    assert infer({'scripts':{'start':start}}, {'a.js':'sha'}) == {}


def test_missing_output_requires_explicit_build():
    assert infer({'bin':'dist/cli.js'}, {}) == {}
    assert infer({'bin':'src/cli.js','scripts':{'build':'tsc'}}, {}) == {}


def test_staging_uses_inference_without_running_build(staged, monkeypatch):
    # Fixture is imported from the existing stage tests to exercise the real path.
    from mc.desk_connect import github_connection as github
    import json
    from pathlib import Path
    rec = github._stages[staged[0]['stage_id']]
    metadata = {'bin': {'youtube': 'dist/cli.js'}, 'scripts': {'build':'tsc','start':'node ./dist/index.js'}}
    (Path(rec['install_dir'])/'package.json').write_text(json.dumps(metadata))
    monkeypatch.setattr(github.installer, 'stage_clone', lambda *a, **k: {key:rec[key] for key in ('sha','install_dir')})
    got = github.stage({'url':'https://github.com/ZubeidHendricks/youtube-mcp-server'})
    assert not got['manual_command'] and got['command']=='node' and got['args']==['dist/cli.js']
    assert got['required_steps']==['script:build']
    assert not any('run' in command or 'install' in command for command in staged[1])


from tests.test_desk_connect_github import staged  # noqa: E402,F401
