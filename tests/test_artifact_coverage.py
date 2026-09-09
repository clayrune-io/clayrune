"""Artifact coverage — the substitution check (mc/artifact_coverage.py).

The anchor case is the 2026-09-08 incident reproduced verbatim in
`test_linkedin_substitution_is_caught`: a pasted search URL, a different search
actually run, a confident answer. Everything else in this file exists to keep
the check from becoming noise, because an advisory that fires on ordinary turns
is one the user learns to ignore — which is the same failure as not having it.
"""

from mc import artifact_coverage as ac


# The URL Ron pasted, and the URL the agent actually fetched instead.
ASKED_URL = (
    'https://www.linkedin.com/jobs/search-results/?currentJobId=4464684242'
    '&eBP=NOT_ELIGIBLE_FOR_CHARGING&refId=GF1PjfAzf%2BLygbgISPC3rw%3D%3D'
    '&keywords=Product%20manager%20infra%20posted%20in%20the%20past%2024%20hours'
    '&origin=JOB_SEARCH_PAGE_JOB_FILTER&geoId=103977389&distance=0.0&f_EA=true'
)
ASKED = 'Can you review the search results here?\n' + ASKED_URL
SUBSTITUTED = ac.flatten_tool_input('WebFetch', {
    'url': ('https://www.linkedin.com/jobs/search/?keywords=product+manager+'
            'infrastructure&geoId=103977389&distance=25'),
})
FAITHFUL = ac.flatten_tool_input('WebFetch', {'url': ASKED_URL})


def _labels(asked, blobs):
    return [m['label'] for m in ac.uncovered(asked, blobs)]


def test_linkedin_substitution_is_caught():
    labels = _labels(ASKED, [SUBSTITUTED])
    joined = ' | '.join(labels)
    assert 'distance=0.0' in joined
    assert 'f_EA=true' in joined
    assert 'keywords=Product manager infra' in joined
    assert ac.advisory_line(ASKED, [SUBSTITUTED])


def test_faithful_fetch_is_silent():
    assert ac.advisory_line(ASKED, [FAITHFUL]) == ''


def test_percent_encoding_does_not_cause_a_false_miss():
    """The user typed `distance=0.0`; the agent sent it URL-encoded inside a
    JSON body. Same value, and the check must not call that a substitution."""
    blob = ac.flatten_tool_input('WebFetch', {
        'url': 'https://www.linkedin.com/jobs/search/',
        'body': 'keywords=Product+manager+infra+posted+in+the+past+24+hours'
                '&geoId=103977389&distance=0.0&f_EA=true'
                '&currentJobId=4464684242',
    })
    assert ac.advisory_line(ASKED, [blob]) == ''


def test_url_rewrite_is_allowed_when_params_survive():
    """`/jobs/search-results/` → `/jobs/search/` is a legitimate rewrite. The
    check is param-level once the host was reached, so it must not fire on the
    path change alone."""
    blob = ac.flatten_tool_input('WebFetch', {
        'url': ('https://www.linkedin.com/jobs/search/?currentJobId=4464684242'
                '&keywords=Product manager infra posted in the past 24 hours'
                '&geoId=103977389&distance=0.0&f_EA=true'),
    })
    assert ac.advisory_line(ASKED, [blob]) == ''


def test_host_never_touched_reports_the_url_not_every_param():
    """When nothing reached the host, one miss is reported as the URL — not
    the same miss counted once per query param."""
    blob = ac.flatten_tool_input('Bash', {'command': 'git status'})
    misses = ac.uncovered(ASKED, [blob])
    assert [m['kind'] for m in misses] == ['url']


def test_conversational_turn_with_no_tool_calls_is_silent():
    assert ac.advisory_line(ASKED, []) == ''
    assert ac.uncovered(ASKED, []) == []


def test_tracking_params_are_never_reported():
    """refId/eBP/origin are junk the user pasted along, not intent."""
    joined = ' | '.join(_labels(ASKED, [SUBSTITUTED]))
    assert 'refId' not in joined
    assert 'eBP' not in joined
    assert 'origin=' not in joined


def test_file_path_the_agent_never_opened():
    asked = 'Check mc/agent_runtime.py for the resume path'
    seen = ac.flatten_tool_input('Read', {'file_path': 'mc/distiller.py'})
    assert 'mc/agent_runtime.py' in ' | '.join(_labels(asked, [seen]))


def test_file_path_the_agent_did_open_is_silent():
    asked = 'Check mc/agent_runtime.py for the resume path'
    seen = ac.flatten_tool_input('Read', {'file_path': '/repo/mc/agent_runtime.py'})
    assert ac.advisory_line(asked, [seen]) == ''


def test_backticked_token_is_an_artifact():
    asked = 'Does `artifact_coverage_enabled` default to on?'
    seen = ac.flatten_tool_input('Grep', {'pattern': 'behavior_tail_enabled'})
    assert 'artifact_coverage_enabled' in ' | '.join(_labels(asked, [seen]))


def test_plain_prose_produces_no_artifacts_at_all():
    """The check must be silent on the ordinary case — no URLs, no paths, no
    ids means nothing to compare, whatever the agent did."""
    asked = 'can you have a look at the login flow and tell me if it is sane'
    seen = ac.flatten_tool_input('Bash', {'command': 'ls'})
    assert ac.extract_artifacts(asked) == []
    assert ac.advisory_line(asked, [seen]) == ''


def test_report_is_capped():
    asked = 'https://x.test/?a=1&b=2&c=3&d=4&e=5&f=6&g=7'
    seen = ac.flatten_tool_input('WebFetch', {'url': 'https://x.test/?z=9'})
    assert len(ac.uncovered(asked, [seen])) <= 4


def test_never_raises_on_junk_input():
    # Deliberately off-type: these arrive from a live stream, not a caller
    # that pyright has checked, and the observer must never raise into a turn.
    assert ac.advisory_line(None, None) == ''      # type: ignore[arg-type]
    assert ac.advisory_line('', ['']) == ''
    assert ac.extract_artifacts(None) == []        # type: ignore[arg-type]
    assert ac.flatten_tool_input('X', object())


# ── Wiring (mc/blueprints/agent_routes.py) ──────────────────────────────────
# The module above is pure; these guard the three places it is attached to the
# live stream. A check that is correct but unwired is the failure it exists to
# catch, applied to itself.

def test_wired_at_both_tool_use_sites_and_both_turn_ends():
    src = open('mc/blueprints/agent_routes.py', encoding='utf-8').read()
    # Leading-newline anchored so the `def` line is not counted as a site.
    assert src.count('\n                            _coverage_note_tool(') == 2
    assert src.count('\n                    _emit_coverage_advisory(') == 2


def test_last_user_message_is_read_off_the_transcript():
    from mc.blueprints import agent_routes as ar
    from mc import state
    label = state.CONFIG.get('user_name') or 'User'
    session = {'log_lines': [
        f'\n> {label}: first thing\n',
        '[tool: Bash]',
        'some agent narration',
        f'\n> {label}: check https://x.test/?a=1\n',
        '[tool: WebFetch]',
    ]}
    assert ar._coverage_last_user_message(session) == 'check https://x.test/?a=1'


def test_advisory_appends_one_line_and_clears_the_buffer():
    from mc.blueprints import agent_routes as ar
    from mc import state
    label = state.CONFIG.get('user_name') or 'User'
    session = {
        'log_lines': [f'\n> {label}: fetch https://x.test/?mode=strict\n'],
        '_coverage_blobs': [ac.flatten_tool_input(
            'WebFetch', {'url': 'https://x.test/?mode=loose'})],
    }
    ar._emit_coverage_advisory(session)
    assert session['log_lines'][-1].startswith('[coverage] ')
    assert 'mode=strict' in session['log_lines'][-1]
    assert session['_coverage_blobs'] == []
    # Idempotent: a second turn-end with an empty buffer must stay silent.
    ar._emit_coverage_advisory(session)
    assert sum(1 for x in session['log_lines']
               if x.startswith('[coverage] ')) == 1
