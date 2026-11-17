"""tools/regen-api-reference.py route scan (MC-1179 pass 12 second wave, TL33).

The tool used to read only `@app.route` lines in server.py (8 of ~585 routes).
"""
import importlib.util
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / 'tools' / 'regen-api-reference.py'


def _load():
    spec = importlib.util.spec_from_file_location('regen_api_reference_tool', TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


regen = _load()


def test_scan_reads_blueprints_not_just_server():
    paths = {p for p, _ in regen.scan_routes()}
    # 8 routes live in server.py; hundreds live in mc/blueprints/.
    assert len(paths) > 400
    assert '/api/project/<project_id>/backlog' in paths


def test_scan_does_not_import_server():
    sys.modules.pop('server', None)
    regen.scan_routes()
    assert 'server' not in sys.modules


def test_blueprint_decorator_prefix_constants_and_methods(tmp_path):
    src = tmp_path / 'demo_routes.py'
    src.write_text(textwrap.dedent('''
        from flask import Blueprint
        bp = Blueprint('demo', __name__, url_prefix='/api/demo')
        BASE = '/things'

        @bp.route('/a', methods=['POST', 'PUT'])
        def a(): pass

        @bp.route(BASE + '/<int:tid>')
        def b(tid): pass

        @bp.route(wire.PATH)
        def c(): pass
    '''), encoding='utf-8')
    routes, unresolved, _ = regen.scan_routes_detailed([src])
    assert ('/api/demo/a', ['POST', 'PUT']) in routes
    assert ('/api/demo/things/<tid>', ['GET']) in routes
    assert len(unresolved) == 1 and unresolved[0].startswith('demo_routes.py:')


def test_cli_reports_blueprint_routes():
    out = subprocess.run([sys.executable, str(TOOL), '--json'], capture_output=True,
                         text=True, cwd=str(ROOT), timeout=60)
    assert '"total_routes"' in out.stdout
    import json
    data = json.loads(out.stdout)
    assert data['total_routes'] > 400
    # A live documented path must not be reported as removed.
    assert '/api/project/<project_id>/backlog' not in data['stale_in_doc']
