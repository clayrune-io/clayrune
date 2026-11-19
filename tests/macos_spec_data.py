"""Evaluate the macOS spec's data entries without importing PyInstaller."""
import ast
from pathlib import Path


SPEC = Path(__file__).resolve().parents[1] / 'installer/build-macos.spec'


def macos_datas(repo):
    # Stop before the spec writes build metadata or runs Analysis.
    tree = ast.parse(SPEC.read_text(encoding='utf-8').split('\n_build_info =', 1)[0])
    tree.body = [
        node for node in tree.body
        if not isinstance(node, ast.ImportFrom) or node.module != 'PyInstaller.utils.hooks'
    ]
    namespace = {'SPECPATH': str(repo / 'installer'), 'collect_submodules': lambda _: []}
    exec(compile(tree, str(SPEC), 'exec'), namespace)
    return {(Path(source), destination) for source, destination in namespace['datas']}
