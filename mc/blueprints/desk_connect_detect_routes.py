"""Parameter detection for user-chosen MCP servers and APIs (`mc/desk_connect/parameter_detect.py`,
slice U1 of docs/DESK_SERVICE_PROFILES_SPEC.md). Its own blueprint: a new concern does not
grow `desk_connect_routes.py`.

    POST /api/desk/connect/detect   {kind?, input, text?, docs_url?}: read the evidence for an
                                    npm package, PyPI package, remote MCP address, API
                                    description or API base address, and return EDITABLE,
                                    provenance-marked draft parameters.

Detection only. It never installs, registers, saves, probes the target, starts a process or
touches the vault, and what it returns is `approved: false` by construction.

Humans only (403 for an unattended agent session): a detection reads a third party's text
and starts a model call, and nothing an agent needs goes through here.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from mc.core import _log
from mc.desk_connect import parameter_detect as _detect
from mc.unattended import is_unattended_caller

bp = Blueprint('desk_connect_detect_routes', __name__)

_NO_AGENTS = {'error': 'this action needs a human: an unattended agent session cannot run a connection detection'}


def _own_hosts() -> tuple:
    host = (request.host or '').split(':')[0].lower()
    return (host,) if host else ()


@bp.route('/api/desk/connect/detect', methods=['POST'])
def detect_parameters():
    if is_unattended_caller():
        return jsonify(_NO_AGENTS), 403
    d = request.get_json(silent=True)
    d = d if isinstance(d, dict) else {}
    kind, raw, text, docs_url = d.get('kind'), d.get('input'), d.get('text'), d.get('docs_url')
    if (kind is not None and not isinstance(kind, str)) or (raw is not None and not isinstance(raw, str)) \
            or (text is not None and not isinstance(text, str)) or (docs_url is not None and not isinstance(docs_url, str)):
        return jsonify({'error': 'kind, input, text and docs_url must be text', 'code': 'bad_request'}), 400
    try:
        answer = _detect.detect(kind or None, raw or '', text=text, docs_url=docs_url or None, own_hosts=_own_hosts())
    except _detect.NeedsKind as e:
        return jsonify({'error': str(e), 'code': 'needs_kind'}), 422
    except _detect.InputError as e:
        return jsonify({'error': str(e), 'code': e.code}), 400
    except _detect.DetectError as e:
        return jsonify({'error': str(e), 'code': e.code}), e.status
    except _detect.Busy:
        return jsonify({'error': 'Another detection is still running. Wait for it to finish.', 'code': 'busy'}), 429
    except Exception as e:
        _log(f'[desk_connect] parameter detection failed unexpectedly: {type(e).__name__}', flush=True)
        return jsonify({'error': 'The detection failed; see the server log.', 'code': 'failed'}), 500
    return jsonify(answer)
