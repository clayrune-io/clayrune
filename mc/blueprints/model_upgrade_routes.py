"""Model pin auto-upgrade endpoints — Piece 2 of the 2026-09-23 model-auto-
upgrade spec. All policy lives in ``mc/model_upgrade.py``; this blueprint is
thin wiring: it hands that module the live CONFIG / project accessors and
returns its report as JSON.

Routes:
    GET  /api/model-upgrades/discovery    per-runtime live catalog ids, or
                                           null for a runtime with no
                                           discovery source (never guessed)
    GET  /api/model-upgrades/price-gaps   (provider, model) pairs this
                                           install has a pin on (or would
                                           upgrade to) with no price on file
                                           — what the daily run should price
    POST /api/model-upgrades/prices       record one {provider, model,
                                           input, output, source_url?,
                                           retirement_at?} price. Market
                                           data, not a character mutation —
                                           deliberately agent-writable, same
                                           as the daily run looking a price
                                           up and recording it.
    POST /api/model-upgrades/run          evaluate every known pin and, when
                                           `model_auto_upgrade_enabled` is on
                                           (default) AND the body doesn't
                                           pass {"apply": false}, APPLY the
                                           ones the gate approves. This is
                                           the only place a pin is written outside
                                           a human editing it by hand — the write
                                           path goes straight through
                                           mc.characters / mc.workflows /
                                           project_routes.save_project, never
                                           through the agent-refused
                                           character/workflow HTTP routes,
                                           and the only value it can ever
                                           write is a catalog id this
                                           module's own gate computed and
                                           priced, never anything from the
                                           request body.

No wire() path-seam beyond CONFIG_PATH — everything else (CONFIG itself,
project load/save) is read live off mc.state / mc.blueprints.project_routes,
same "read the live thing, don't cache a stale copy" pattern backup_routes
and settings_routes already use.
"""
from __future__ import annotations

from pathlib import Path

from flask import Blueprint, jsonify, request

from mc import model_upgrade as _mu
from mc import state
from mc.blueprints.project_routes import load_project, load_projects, save_project
from mc.core import _log

bp = Blueprint('model_upgrade_routes', __name__)

CONFIG_PATH: Path = None  # type: ignore[assignment]


def wire(*, config_path: Path) -> None:
    global CONFIG_PATH
    CONFIG_PATH = config_path


def _err(e: Exception, code: int = 400):
    return jsonify({'error': str(e)}), code


@bp.route('/api/model-upgrades/discovery')
def api_model_upgrades_discovery():
    return jsonify(_mu.discovery_report())


@bp.route('/api/model-upgrades/price-gaps')
def api_model_upgrades_price_gaps():
    gaps = _mu.price_gaps(load_projects, load_project, save_project,
                          state.CONFIG, CONFIG_PATH)
    return jsonify({'gaps': gaps})


@bp.route('/api/model-upgrades/prices', methods=['POST'])
def api_model_upgrades_set_price():
    data = request.get_json(silent=True) or {}
    provider = str(data.get('provider') or '')
    model = str(data.get('model') or '')
    input_price = data.get('input')
    output_price = data.get('output')
    if input_price is None or output_price is None:
        return _err(ValueError('input and output prices are required'))
    try:
        entry = _mu.set_price(
            provider, model, float(input_price), float(output_price),
            source_url=data.get('source_url') or '',
            retirement_at=data.get('retirement_at') or '',
        )
    except (ValueError, TypeError) as e:
        return _err(e)
    return jsonify({'provider': str(provider).strip().lower(),
                    'model': str(model).strip(), 'price': entry})


def _run_report(*, apply: bool):
    return _mu.run_upgrade_gate(
        load_projects_fn=load_projects, load_project_fn=load_project,
        save_project_fn=save_project, config=state.CONFIG,
        config_path=CONFIG_PATH, apply=apply,
    )


@bp.route('/api/model-upgrades/report')
def api_model_upgrades_report():
    """Preview only — never writes. What the gate WOULD do right now."""
    try:
        return jsonify(_run_report(apply=False))
    except Exception as e:
        _log(f"[model-upgrade] report failed: {e}")
        return _err(e, 500)


@bp.route('/api/model-upgrades/run', methods=['POST'])
def api_model_upgrades_run():
    data = request.get_json(silent=True) or {}
    want_apply = data.get('apply', True)
    gate_on = bool(state.CONFIG.get('model_auto_upgrade_enabled', True))
    apply = bool(want_apply) and gate_on
    try:
        report = _run_report(apply=apply)
    except Exception as e:
        _log(f"[model-upgrade] run failed: {e}")
        return _err(e, 500)
    if not gate_on and want_apply:
        report['disabled'] = True
    if report.get('upgraded'):
        _log(f"[model-upgrade] applied={apply}: "
            f"{len(report['upgraded'])} pin(s) " +
            ('upgraded' if apply else 'would upgrade'))
    return jsonify(report)
