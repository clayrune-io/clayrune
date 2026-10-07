"""Human-started, read-only browser connection check (no credential use/fill)."""
from flask import Blueprint, jsonify, request
from mc.core import _log

from mc.desk_connect import browser_check
from mc.unattended import is_unattended_caller

bp=Blueprint('desk_connect_browser_check',__name__)


@bp.route('/api/desk/connect/browser-profile/check',methods=['POST'])
def check():
    if is_unattended_caller():
        return jsonify(error='this action needs a human',code='human_required'),403
    try:
        clean=browser_check.clean(request.get_json(silent=True),own_hosts=((request.host or '').split(':')[0],))
        return jsonify(browser_check.run(clean))
    except browser_check.CheckError as e:
        return jsonify(error=str(e),code=e.code),e.status
    except Exception as e:
        _log(f'[desk_connect] browser check failed: {type(e).__name__}',flush=True)
        return jsonify(error='the connection check could not run; try again',code='failed'),500
