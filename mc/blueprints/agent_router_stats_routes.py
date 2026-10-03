"""Auto-router statistics API — carved out of agent_routes.py.

First leaf extraction from `mc/blueprints/agent_routes.py`
(docs/ENTANGLEMENT_AUDIT.md rank #1, concern "Router statistics API").
Moved VERBATIM: one read-only route, no state, one dependency (the project
records dir). The writer side (`_router_stat`) stays with the dispatch code
in agent_routes.py.

The route is registered on the ORIGINAL `agent_routes` Blueprint, not a new
one, so the Flask endpoint stays `agent_routes.get_router_stats_aggregate`
(tests/test_agent_routes.py pins Blueprint ownership of every route).
agent_routes.py calls `register_routes(bp, data_dir_fn=...)` at import time,
before server.py registers the Blueprint.
"""

import json
from pathlib import Path
from typing import Callable

from flask import jsonify


def register_routes(bp, *, data_dir_fn: Callable[[], Path]):
    """Attach /api/router/stats to `bp`. `data_dir_fn` is called per request
    because agent_routes.DATA_DIR is bound late, by wire()."""

    @bp.route('/api/router/stats', methods=['GET'])
    def get_router_stats_aggregate():
        """Cross-project auto-router counters. Sums totals and by_pair across
        every project's _router_stats.json. Surfaces last_fallback as the most
        recent across projects. Read-only; never mutates state.

        Response shape:
          {
            "totals": {"manual": N, "auto": N, "fallback": N},
            "by_pair": {"opus->haiku": N, ...},
            "last_fallback": {"ts": "...Z", "reason": "...", "project_id": "..."},
            "projects": N            # how many had a stats file
          }
        """
        agg_totals = {}
        agg_by_pair = {}
        last_fb = None
        project_count = 0
        for f in data_dir_fn().glob('*_router_stats.json'):
            try:
                data = json.loads(f.read_text(encoding='utf-8') or '{}')
            except Exception:
                continue
            if not isinstance(data, dict):
                continue
            project_count += 1
            for k, v in (data.get('totals') or {}).items():
                agg_totals[k] = int(agg_totals.get(k, 0)) + int(v or 0)
            for k, v in (data.get('by_pair') or {}).items():
                agg_by_pair[k] = int(agg_by_pair.get(k, 0)) + int(v or 0)
            fb = data.get('last_fallback')
            if isinstance(fb, dict) and fb.get('ts'):
                if last_fb is None or fb['ts'] > last_fb.get('ts', ''):
                    # Derive project_id from the filename suffix-strip.
                    pid = f.name[:-len('_router_stats.json')]
                    last_fb = {**fb, 'project_id': pid}
        return jsonify({
            'totals': agg_totals,
            'by_pair': agg_by_pair,
            'last_fallback': last_fb,
            'projects': project_count,
        })

    return get_router_stats_aggregate
