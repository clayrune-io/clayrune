"""Source-only packaging gates; never runs installers, imports app, or contacts services.

macOS: evaluate only the spec's literal datas list, and traverse the local static
import graph that PyInstaller Analysis follows, including imports inside functions.
Windows/Linux: installers clone the whole repository; check the actual Git archive
against the on-disk asset inventory (so an untracked new module is caught too).
This is not a frozen .app/clean-VM startup test.
"""
import ast
import io
import re
import subprocess
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PYTHON = sorted(ROOT.glob('mc/**/agent_mail*.py'))
STATIC = sorted(ROOT.glob('static/js/agent-mail*.js')) + sorted(ROOT.glob('static/css/agent-mail*.css'))
# The offline root signer is operator tooling, not an app runtime import. It
# ships with source installs; frozen clients only consume signed manifests.
SOURCE_TOOLS = sorted(ROOT.glob('tools/agent-mail*.py'))


def local_imports(entry):
    seen, todo = set(), [entry]
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        path = ROOT.joinpath(*name.split('.')).with_suffix('.py')
        if not path.is_file():
            path = ROOT.joinpath(*name.split('.'), '__init__.py')
        if not path.is_file():
            continue
        seen.add(name)
        package = name if path.name == '__init__.py' else name.rpartition('.')[0]
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8-sig'))):
            if isinstance(node, ast.Import):
                todo.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ''
                if node.level:
                    bits = package.split('.')
                    base = '.'.join(bits[:len(bits) - node.level + 1] + ([base] if base else []))
                todo.append(base)
                todo.extend(base + '.' + alias.name for alias in node.names)
    return seen


@pytest.fixture(scope='module')
def import_graph():
    tree = ast.parse((ROOT / 'installer/build-macos.spec').read_text(encoding='utf-8'))
    roots = ['app']
    for node in tree.body:
        if isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name) and node.target.id == 'hidden':
            if isinstance(node.value, ast.List):
                roots.extend(n.value for n in node.value.elts if isinstance(n, ast.Constant) and isinstance(n.value, str))
    return set().union(*(local_imports(root) for root in roots))


@pytest.fixture(scope='module')
def mac_data():
    tree = ast.parse((ROOT / 'installer/build-macos.spec').read_text(encoding='utf-8'))
    assignment = next(n for n in tree.body if isinstance(n, ast.Assign)
                      and any(isinstance(t, ast.Name) and t.id == 'datas' for t in n.targets))
    # No full spec execution: that would build identity temp files/run git/import PyInstaller.
    expression = compile(ast.Expression(assignment.value), '<datas>', 'eval')
    return eval(expression, {'__builtins__': {}, 'R': lambda *parts: ROOT.joinpath(*parts)})


@pytest.mark.parametrize('path', PYTHON, ids=lambda p: p.name)
def test_mac_analysis_reaches_every_agent_mail_module(path, import_graph):
    name = '.'.join(path.relative_to(ROOT).with_suffix('').parts)
    assert name in import_graph, f'{name} needs an import or explicit hiddenimport in the macOS spec'


@pytest.mark.parametrize('path', STATIC, ids=lambda p: p.name)
def test_mac_datas_and_dashboard_include_every_agent_mail_asset(path, mac_data):
    relative = path.relative_to(ROOT)
    assert any(path.is_relative_to(src) and Path(dest) / path.relative_to(src) == relative
               for src, dest in mac_data if src.is_dir()), f'{relative} missing from macOS datas'
    index = (ROOT / 'static/index.html').read_text(encoding='utf-8')
    assert re.search(r'(?:src|href)="/static/' + re.escape(relative.relative_to('static').as_posix())
                     + r'(?:\?[^" ]*)?"', index), f'{relative} not loaded by index.html'


@pytest.fixture(scope='module')
def source_archive():
    archive = subprocess.run(['git', 'archive', '--format=tar', 'HEAD'], cwd=ROOT,
                             check=True, capture_output=True).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        return set(tar.getnames())


@pytest.mark.parametrize('path', PYTHON + STATIC + SOURCE_TOOLS, ids=lambda p: p.name)
def test_source_install_archive_contains_every_agent_mail_file(path, source_archive):
    assert path.relative_to(ROOT).as_posix() in source_archive, f'{path.name} is not in the source distribution'


def test_source_installers_use_complete_checkouts():
    windows = (ROOT / 'installer/install.ps1').read_text(encoding='utf-8')
    unix = (ROOT / 'installer/install.sh').read_text(encoding='utf-8')
    assert '& git clone $repoUrl $installDir' in windows
    assert 'git clone "$REPO_URL" "$INSTALL_DIR"' in unix
    for text in (windows, unix):
        assert not re.search(r'^\s*[^#\n]*\b(?:sparse-checkout|--sparse)\b', text, re.M)


def test_mac_analysis_uses_the_checked_entrypoint_and_manifests():
    tree = ast.parse((ROOT / 'installer/build-macos.spec').read_text(encoding='utf-8'))
    call = next(n for n in ast.walk(tree) if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Name) and n.func.id == 'Analysis')
    assert ast.literal_eval(call.args[0].elts[0].args[0]) == 'app.py'
    options = {kw.arg: kw.value for kw in call.keywords}
    assert isinstance(options['datas'], ast.Name) and options['datas'].id == 'datas'
    assert isinstance(options['hiddenimports'], ast.Name) and options['hiddenimports'].id == 'hidden'
