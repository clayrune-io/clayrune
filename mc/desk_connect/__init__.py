"""Desk connect-by-URL (docs/DESK_CONNECT_BY_URL_SPEC.md). One module per concern:

    url_check   what a pasteable service address may be (pure, no network)
    registry    the data-only list of known hosts (registry.json) and its loader
    methods     the Method step: an address in, its connection options out
    commit      the one Save: vault entry first, then the Desk record, with a
                compensating delete if the record write fails

The routes are `mc/blueprints/desk_connect_routes.py`.
"""
