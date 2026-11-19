"""Fixture coverage for route shortcuts and source discovery."""
import importlib.util
import textwrap
from pathlib import Path


TOOL = Path(__file__).resolve().parents[1] / 'tools' / 'regen-api-reference.py'
spec = importlib.util.spec_from_file_location('regen_api_reference_tool', TOOL)
assert spec is not None and spec.loader is not None
regen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(regen)


def test_flask_shortcuts_report_their_http_methods(tmp_path):
    source = tmp_path / 'routes.py'
    source.write_text(textwrap.dedent('''
        from flask import Blueprint
        bp = Blueprint('demo', __name__, url_prefix='/api/demo')

        @bp.get('/items/<int:item_id>')
        @bp.post('/items')
        @bp.put('/items/<int:item_id>')
        @bp.patch('/items/<int:item_id>')
        @bp.delete('/items/<int:item_id>')
        def items(item_id=None): pass
    '''), encoding='utf-8')

    assert regen.scan_routes([source]) == [
        ('/api/demo/items/<item_id>', ['GET']),
        ('/api/demo/items', ['POST']),
        ('/api/demo/items/<item_id>', ['PUT']),
        ('/api/demo/items/<item_id>', ['PATCH']),
        ('/api/demo/items/<item_id>', ['DELETE']),
    ]


def test_default_scan_finds_routes_outside_blueprints(tmp_path, monkeypatch):
    monkeypatch.setattr(regen, 'ROOT', tmp_path)
    (tmp_path / 'server.py').write_text(textwrap.dedent('''
        from flask import Flask
        from mc.feature.routes import bp
        app = Flask(__name__)
        app.register_blueprint(bp)

        @app.route('/api/server')
        def server_route(): pass
    '''), encoding='utf-8')
    package = tmp_path / 'mc' / 'feature'
    package.mkdir(parents=True)
    (package / 'routes.py').write_text(textwrap.dedent('''
        from flask import Blueprint
        bp = Blueprint('feature', __name__)

        @bp.route('/api/feature', methods=['POST'])
        def feature_route(): pass
    '''), encoding='utf-8')

    assert regen.scan_routes() == [
        ('/api/server', ['GET']),
        ('/api/feature', ['POST']),
    ]
