"""Studio screenshot routes and the per-product app-address setting."""
from flask import Blueprint, jsonify, request
from urllib.parse import urlsplit

from mc import caller_attribution, desk_capture as capture, state
from mc.blueprints import desk_routes
from mc.core import _log

bp = Blueprint('desk_capture_routes', __name__)


def _local_url():
    # SERVER_PORT is the bound server port, not the caller's Host/tunnel address.
    return 'http://127.0.0.1:' + str(int(request.environ.get('SERVER_PORT') or 5199)) + '/'


def _human_typed():
    env = request.environ
    att = caller_attribution.attribute_caller(request.remote_addr or '', env.get('REMOTE_PORT'), env.get('SERVER_PORT'),
            caller_attribution.managed_roots(state.agent_sessions, state.tracked_processes))
    source = request.headers.get('Origin') or ''
    try:
        parsed = urlsplit(source)
    except ValueError:
        return False
    # HTTPS terminates at the tunnel; Flask still sees HTTP. Compare the app
    # authority, without trusting caller-supplied forwarded headers.
    same_app = (parsed.scheme in ('http', 'https') and parsed.netloc.lower() == request.host.lower()) or source in (
        'capacitor://localhost', 'ionic://localhost')
    return att.status == caller_attribution.UNATTRIBUTED and same_app


def _call(fn, *args, **kw):
    try:
        return jsonify(fn(*args, load_project=desk_routes.load_project, local_url=_local_url(), **kw))
    except capture.Error as e:
        return jsonify({'error': str(e)}), e.status
    except Exception as e:
        _log(f'[desk_capture] route failed: {type(e).__name__}', flush=True)
        return jsonify({'error': 'Capture could not be saved. Try again.'}), 500


@bp.route('/api/desk/capture/projects/<project_id>', methods=['GET', 'PUT'])
def app_address(project_id):
    if request.method == 'GET':
        return _call(capture.settings, project_id)
    d = request.get_json(silent=True)
    if not isinstance(d, dict):
        return jsonify({'error': 'Enter the app address.'}), 400
    return _call(capture.save_address, project_id, d.get('app_address'), human_typed=_human_typed())


@bp.route('/api/desk/capture', methods=['POST'])
def take_picture():
    d = request.get_json(silent=True)
    if not isinstance(d, dict) or not isinstance(d.get('project_id'), str):
        return jsonify({'error': 'Choose a product.'}), 400
    return _call(capture.capture, d['project_id'], d.get('page'))
