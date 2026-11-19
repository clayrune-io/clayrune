"""Dedicated human-only lifecycle blueprint (MC-1166 signup slice, unit U1).

Default composition is the PULL signup: ``MailFactory.bootstrap`` +
``Signup.start/resume/enable`` behind the dashboard passcode. The legacy
``Enrollment`` ceremony is reachable only through the injected ``factory`` seam
(tests); production no longer answers ``operational_adapter_unavailable``.

Pull rules (docs/AGENT_MAIL_SIGNUP_SLICE.md section 2, U1):
* GET is passive and open (no flag): protocol discriminator + ``pull_status`` +
  ``Signup.status``. It never creates a store, identity, key or network call;
  a pristine install reads ``not_enrolled``, a held store reads ``held`` and is
  never repaired here.
* Only a human ``start`` bootstraps the store. It fills the public root/origin
  from the release defaults only when BOTH are unset; an existing pin (or a
  half-set pair) is never overwritten.
* ``start`` persists enrollment availability only. The outbound/ingress/
  supervisor opt-in is written after a signed activation, and a repeated
  ``enable`` repairs a failed local save without a second activation.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Callable

from flask import Blueprint, current_app, jsonify, request

from mc import agent_mail_factory as _mail_factory
from mc import agent_mail_supervisor as _supervisor, provider_preferences, state
from mc.agent_mail_factory import ORIGIN_KEY, ROOT_KEY, MailFactory, StoreHeld
from mc.agent_mail_status_view import pull_status, recovery_snapshot
from mc.agent_mail_enrollment import Enrollment
from mc.agent_mail_enrollment_keys import EnrollmentRefused
from mc.agent_mail_service_manifest import ManifestRefused
from mc.agent_mail_signup import ID as _CONSENT_ID, _mailbox
from mc.agent_mail_store import MailStoreError
from mc.atomic_json import write_json_atomic
from mc.blueprints.agent_mail_deployment_routes import validate_values
from mc.blueprints.secrets_routes import _require_human_passcode
from mc.core import _log
from mc.blueprints.agent_mail_denial_actions import DENIAL_ACTIONS, denial_action

FLAG = 'agent_mail_enrollment_enabled'
BASE = '/api/agent-mail/enrollment'
ENABLE_ACTIONS = frozenset({'start', 'resume', 'review', 'confirm', 'verify', 'enable', 'owner', 'key', 'repin', 'proof'})
PULL_ACTIONS = frozenset({'start', 'resume', 'enable'})
# Opt-in written only after a signed activation (never by start). Same keys as
# tools/agent-mail-provision-install.py and the supervisor/outbound/ingress gates.
ACTIVATION_FLAGS = {FLAG: True, 'agent_mail_outbound_enabled': True,
                    'agent_mail_ingress_enabled': True, 'agent_mail_supervisor_enabled': True}
# Release-reviewed PUBLIC service defaults (docs/AGENT_MAIL_SIGNUP_SLICE.md O4). Empty
# until a release publishes them: nothing operator-specific is committed, and an
# install with neither default nor configured pin refuses ``deployment_unconfigured``.
RELEASE_ROOT_PUBLIC_KEY = ''
RELEASE_SERVICE_ORIGIN = ''


def _release_defaults() -> tuple[str, str]:
    return RELEASE_ROOT_PUBLIC_KEY, RELEASE_SERVICE_ORIGIN


def blocking_sessions() -> list[dict[str, str]]:
    """Mail-only conservative policy; no request-controlled attribution claims.

    Dave accepted the human-dashboard false positive for this disabled unit.
    Trusted per-request attribution is required before live mail (MC-948).
    Disable/revocation retry are always open to a valid passcode-holder.
    """
    return [{'session_id': sid, 'trigger': str(session.get('trigger_type') or 'unknown')}
            for sid, session in sorted(list(state.agent_sessions.items()))
            if session.get('status') == 'running' and session.get('trigger_type') != 'manual']


def _empty_signup(phase: str, reason: str = '') -> dict[str, Any]:
    """Same keys as ``Signup.status`` for a store that has no account row."""
    return {'state': phase, 'reason': reason, 'enabled': False, 'install_id': None, 'flow_id': None,
            'address': '', 'expires_ms': None, 'verification_state': None}


def _allow() -> bool:
    # Passed only after this request's passcode and applicable session check.
    return True


def _deny() -> bool:
    return False


@provider_preferences.serialized_write
def _save_config(path: Path | None, values: dict[str, Any]) -> None:
    """Atomic file write first, in-memory commit only after it succeeds (the
    ``agent_mail_deployment_routes`` pattern). Skips a write that changes nothing."""
    if all(state.CONFIG.get(k) == v for k, v in values.items()):
        return
    from mc.blueprints import settings_routes
    write_json_atomic(path if path is not None else settings_routes.CONFIG_PATH,
                      {**state.CONFIG, **values}, indent=2, ensure_ascii=False)
    state.CONFIG.update(values)


def create_blueprint(*, factory: Callable[[], Enrollment] | None = None,
                     mail_factory: Callable[[], MailFactory] | None = None,
                     supervisor_start: Callable[[Any], Any] | None = None,
                     release_defaults: Callable[[], tuple[str, str]] | None = None,
                     config_path: Path | None = None) -> Blueprint:
    """``factory`` is the legacy Enrollment seam (tests). With it absent the PULL
    signup runs over ``mail_factory`` (default: the process singleton)."""
    bp = Blueprint('agent_mail_enrollment', __name__)

    def mail() -> MailFactory:
        return mail_factory() if mail_factory is not None else _mail_factory.get()

    def pull_read() -> dict[str, Any]:
        """Passive: no flag gate, no store creation, no key, no transport call."""
        mf = mail()
        signup = None
        try:
            signup = mf.signup(authorize=_deny)  # inert: cannot start, enable or send
            view = signup.status()
        except StoreHeld as exc:
            view = _empty_signup('not_enrolled') if exc.reason == 'not_enrolled' else _empty_signup('held', exc.reason)
        except Exception as exc:
            _log(f'[agent-mail-enrollment] passive signup status failed: {type(exc).__name__}', flush=True)
            view = _empty_signup('held', 'store_unavailable')
        return {'protocol': 'pull', 'pull_status': pull_status(mf, supervisor=_supervisor.get(), signup=signup),
                'signup': view}

    def deployment_values() -> dict[str, str]:
        """Public root/origin to persist: {} when a full pair is already pinned."""
        root, origin = state.CONFIG.get(ROOT_KEY), state.CONFIG.get(ORIGIN_KEY)
        present = [isinstance(v, str) and bool(v) for v in (root, origin)]
        if all(present):
            return {}  # a configured pin (custom or not) is never overwritten
        if any(present):
            raise EnrollmentRefused('deployment_incomplete')  # never mix two deployments' halves
        default_root, default_origin = (release_defaults or _release_defaults)()
        try:
            return validate_values({ROOT_KEY: default_root, ORIGIN_KEY: default_origin})
        except ValueError:
            raise EnrollmentRefused('deployment_unconfigured') from None

    def pull_write(action: str, body: dict[str, Any]):
        mf = mail()
        if action in DENIAL_ACTIONS:
            return denial_action(mf, action, body, _allow)
        if action == 'start':
            owner, consent = body.get('owner'), body.get('consent_version')
            if (set(body) - {'passcode', 'owner', 'consent_version'} or not isinstance(owner, str)
                    or not isinstance(consent, str) or not _CONSENT_ID.fullmatch(consent)):
                return jsonify({'error': 'invalid_request'}), 400
            try:
                _mailbox(owner)
            except EnrollmentRefused:
                return jsonify({'error': 'invalid_request'}), 400
            _save_config(config_path, deployment_values())
            mf.bootstrap(authorize=_allow)  # the only place a store may be created
            result = mf.signup(authorize=_allow).start(owner, consent)
            if result.get('install_id'):
                _save_config(config_path, {FLAG: True})  # availability only; no mail opt-in yet
            return jsonify({'protocol': 'pull', **result})
        try:
            signup = mf.signup(authorize=_allow)
        except StoreHeld as exc:
            if exc.reason == 'not_enrolled':
                return jsonify({'protocol': 'pull', **_empty_signup('not_enrolled')})
            raise
        if action == 'resume':
            return jsonify({'protocol': 'pull', **signup.resume()})
        result = signup.enable()
        if result.get('state') == 'active' and result.get('enabled') is True:
            # Committed locally and at the service. Everything below is repairable
            # by pressing enable again: the replay returns this same active view.
            try:
                _save_config(config_path, ACTIVATION_FLAGS)
            except OSError as exc:
                _log(f'[agent-mail-enrollment] activation flags not saved: {exc}', flush=True)
                return jsonify({'error': 'storage_unavailable', 'protocol': 'pull', 'activation': 'committed'}), 503
            try:
                (supervisor_start or _supervisor.start_default)(current_app._get_current_object())  # type: ignore[attr-defined]
            except Exception as exc:
                _log(f'[agent-mail-enrollment] supervisor not started: {type(exc).__name__}', flush=True)
                return jsonify({'error': 'supervisor_unavailable', 'protocol': 'pull', 'activation': 'committed'}), 503
        return jsonify({'protocol': 'pull', **result})

    def pull(action: str):
        try:
            if request.method == 'GET':
                return jsonify(pull_read())
            body = request.get_json(silent=True)
            if not isinstance(body, dict):
                return jsonify({'error': 'invalid_request'}), 400
            refusal = _require_human_passcode(body)
            if refusal is not None:
                return refusal
            if action not in PULL_ACTIONS and action not in DENIAL_ACTIONS:
                return jsonify({'error': 'not_found'}), 404
            blockers = blocking_sessions() if action in PULL_ACTIONS else []
            if blockers:
                return jsonify({'error': 'human_required', 'blocking_sessions': blockers}), 403
            return pull_write(action, body)
        except (EnrollmentRefused, ManifestRefused) as exc:
            _log(f'[agent-mail-enrollment] refused: {exc}', flush=True)
            return jsonify({'error': str(exc)}), 409
        except StoreHeld as exc:  # held stays held: a closed reason, never repaired here
            _log(f'[agent-mail-enrollment] store held: {exc.reason}', flush=True)
            return jsonify({'error': 'held', 'reason': exc.reason}), 409
        except (MailStoreError, OSError, sqlite3.Error):
            _log('[agent-mail-enrollment] storage unavailable; operation not acknowledged', flush=True)
            return jsonify({'error': 'storage_unavailable'}), 503

    @bp.after_request
    def private(response):
        response.headers['Cache-Control'] = 'no-store'
        return response

    @bp.route(BASE, methods=['GET'])
    @bp.route(BASE + '/<action>', methods=['POST'])
    def enrollment(action: str = ''):
        if factory is None:
            return pull(action)
        if state.CONFIG.get(FLAG) is not True:
            return jsonify({'error': 'not_found'}), 404
        if request.method == 'POST':
            body = request.get_json(silent=True)
            if not isinstance(body, dict):
                return jsonify({'error': 'invalid_request'}), 400
            refusal = _require_human_passcode(body)
            if refusal is not None:
                return refusal
            blockers = blocking_sessions() if action in ENABLE_ACTIONS else []
            if blockers:
                return jsonify({'error': 'human_required', 'blocking_sessions': blockers}), 403
        else:
            body = {}
        try:
            core = factory()
            if request.method == 'GET':
                result = core.status()
                # 7e-1: bounded recovery/worker metadata; read-only, allow-listed.
                result['recovery'] = recovery_snapshot(_supervisor.get())
            elif action in {'start', 'owner', 'key', 'repin'}:
                owner, payload, signature = body.get('owner'), body.get('manifest'), body.get('signature')
                if not isinstance(owner, str) or not isinstance(payload, list) or not isinstance(signature, str):
                    return jsonify({'error': 'invalid_request'}), 400
                result = (core.start(owner, payload, signature) if action == 'start' else
                          core.change_identity(owner, payload, signature))
            elif action == 'confirm':
                confirmation = body.get('confirmation')
                if not isinstance(confirmation, dict):
                    return jsonify({'error': 'invalid_request'}), 400
                result = core.confirm(confirmation)
            elif action == 'verify':
                code = body.get('code')
                if not isinstance(code, str):
                    return jsonify({'error': 'invalid_request'}), 400
                result = core.verify(code)
            elif action in {'resume', 'review', 'enable', 'disable', 'retry-revocations', 'proof'}:
                methods = {'resume': core.resume, 'review': core.review, 'enable': core.enable,
                           'disable': core.disable, 'retry-revocations': core.retry_revocations,
                           'proof': core.arm_proof}
                result = methods[action]()
            else:
                return jsonify({'error': 'not_found'}), 404
            return jsonify(result)
        except (EnrollmentRefused, ManifestRefused) as exc:
            _log(f'[agent-mail-enrollment] refused: {exc}', flush=True)
            return jsonify({'error': str(exc)}), 409
        except (MailStoreError, OSError, sqlite3.Error):
            _log('[agent-mail-enrollment] storage unavailable; operation not acknowledged', flush=True)
            return jsonify({'error': 'storage_unavailable'}), 503

    return bp
