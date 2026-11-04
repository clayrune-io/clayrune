"""mc/turn_clock.py -- the per-turn `[Now ... since your last reply]` stamp and
the `Since your last reply:` gap briefing (Ron, 2026-11-03: agents were not
time-aware; a follow-up 9 hours later read like it came a second later).

Pinned here: elapsed formatting, the threshold, the settings-off paths, a fresh
session with no prior reply, the 10-line cap with its truncation notice, each
briefing source, and that the injection sites in agent_routes stay wired.
"""
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc import turn_clock as tc  # noqa: E402

NOW = 1_800_000_000.0   # fixed epoch so every elapsed figure is exact
H = 3600.0


def _iso(epoch):
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000000Z')


@pytest.fixture()
def cfg(monkeypatch):
    """Isolated CONFIG so a test's setting never leaks into another."""
    from mc import state
    monkeypatch.setattr(state, 'CONFIG', {})
    return state.CONFIG


# ── elapsed formatting ───────────────────────────────────────────────────────

@pytest.mark.parametrize('seconds,expected', [
    (0, 'under a minute'),
    (59, 'under a minute'),
    (60, '1m'),
    (45 * 60, '45m'),
    (59 * 60 + 59, '59m'),
    (3600, '1h'),
    (9 * 3600 + 20 * 60, '9h 20m'),
    (23 * 3600 + 59 * 60, '23h 59m'),
    (24 * 3600, '1d'),
    (3 * 86400 + 4 * 3600, '3d 4h'),
    (-5, 'under a minute'),     # clock skew must not print a negative
])
def test_format_elapsed(seconds, expected):
    assert tc.format_elapsed(seconds) == expected


# ── the stamp line ───────────────────────────────────────────────────────────

_STAMP = re.compile(r'^\[Now \d{4}-\d{2}-\d{2} \d{2}:\d{2} \S+( · (.+) since your last reply)?\]$')


def test_stamp_with_prior_reply_carries_elapsed():
    line = tc.stamp_line({tc._REPLY_KEY: NOW - (9 * H + 20 * 60)}, now=NOW)
    m = _STAMP.match(line)
    assert m, line
    assert m.group(2) == '9h 20m'


def test_stamp_for_fresh_session_has_no_elapsed():
    """No prior reply (brand-new session) -> `Now` only, never a made-up gap."""
    for sess in ({}, None, {tc._REPLY_KEY: None}, {tc._REPLY_KEY: 0}, 'not-a-dict'):
        line = tc.stamp_line(sess, now=NOW)
        m = _STAMP.match(line)
        assert m and m.group(1) is None, (sess, line)


def test_stamp_ignores_reply_in_the_future():
    line = tc.stamp_line({tc._REPLY_KEY: NOW + 500}, now=NOW)
    assert 'since your last reply' not in line


def test_note_reply_stamps_and_never_raises():
    s = {}
    tc.note_reply(s, now=123.0)
    assert s[tc._REPLY_KEY] == 123.0
    tc.note_reply(None)           # not a dict: silently nothing
    tc.note_reply('x')


# ── threshold behaviour ──────────────────────────────────────────────────────

def _project_with_backlog_change(at_epoch):
    return {'backlog': [{
        'id': 'a1', 'key': 'MC-7', 'text': 'Ship the clock',
        'status_history': [{'ts': _iso(at_epoch), 'from': 'open', 'to': 'done', 'by': 'x'}],
    }]}


def test_below_threshold_no_briefing(cfg):
    sess = {tc._REPLY_KEY: NOW - 29 * 60}                     # 29m < 30m default
    proj = _project_with_backlog_change(NOW - 10 * 60)
    out = tc.render(proj, sess, now=NOW)
    assert out.startswith('[Now ') and 'Since your last reply:' not in out


def test_above_threshold_appends_briefing(cfg):
    sess = {tc._REPLY_KEY: NOW - 31 * 60}                     # 31m > 30m default
    proj = _project_with_backlog_change(NOW - 10 * 60)
    out = tc.render(proj, sess, now=NOW)
    lines = out.split('\n')
    assert lines[0].startswith('[Now ') and lines[0].endswith(']')
    assert lines[1] == 'Since your last reply:'
    assert 'backlog MC-7 open→done: Ship the clock' in out


def test_threshold_is_configurable(cfg):
    cfg['turn_clock_gap_threshold_minutes'] = 5
    sess = {tc._REPLY_KEY: NOW - 6 * 60}
    out = tc.render(_project_with_backlog_change(NOW - 60), sess, now=NOW)
    assert 'Since your last reply:' in out
    cfg['turn_clock_gap_threshold_minutes'] = 120
    out = tc.render(_project_with_backlog_change(NOW - 60), sess, now=NOW)
    assert 'Since your last reply:' not in out


@pytest.mark.parametrize('bad', ['abc', None, -10])
def test_bad_threshold_falls_back_to_default(cfg, bad):
    cfg['turn_clock_gap_threshold_minutes'] = bad
    assert tc.threshold_seconds() == tc.DEFAULT_THRESHOLD_MINUTES * 60


def test_quiet_gap_adds_no_empty_block(cfg):
    """A long gap in which nothing changed: stamp only, no hollow header."""
    sess = {tc._REPLY_KEY: NOW - 5 * H}
    out = tc.render({'backlog': []}, sess, now=NOW)
    assert out.startswith('[Now ') and '\n' not in out


def test_fresh_session_never_gets_briefing(cfg):
    out = tc.render(_project_with_backlog_change(NOW - 60), {}, now=NOW)
    assert out.startswith('[Now ') and '\n' not in out


# ── settings off ─────────────────────────────────────────────────────────────

def test_master_setting_off_renders_nothing(cfg):
    cfg['turn_clock_enabled'] = False
    sess = {tc._REPLY_KEY: NOW - 5 * H}
    assert tc.render(_project_with_backlog_change(NOW - 60), sess, now=NOW) == ''
    assert tc.prepend({}, sess, 'hello', now=NOW) == 'hello'


def test_briefing_setting_off_keeps_the_stamp(cfg):
    cfg['turn_clock_gap_briefing_enabled'] = False
    sess = {tc._REPLY_KEY: NOW - 5 * H}
    out = tc.render(_project_with_backlog_change(NOW - 60), sess, now=NOW)
    assert out.startswith('[Now ') and 'since your last reply' in out
    assert 'Since your last reply:' not in out


def test_defaults_are_on(cfg):
    assert tc.enabled() and tc.briefing_enabled()
    assert tc.threshold_seconds() == 30 * 60


# ── briefing sources ─────────────────────────────────────────────────────────

def test_backlog_only_reports_changes_inside_the_window():
    proj = {'backlog': [
        {'key': 'MC-1', 'text': 'old', 'status_history': [{'ts': _iso(NOW - 9 * H), 'from': 'open', 'to': 'done'}]},
        {'key': 'MC-2', 'text': 'new', 'status_history': [{'ts': _iso(NOW - 60), 'from': 'open', 'to': 'blocked'}]},
        {'key': 'MC-3', 'text': 'never moved'},
        {'key': 'MC-4', 'text': 'junk', 'status_history': [{'ts': 'not-a-date'}]},
    ]}
    got = tc.backlog_changes_since(proj, NOW - 2 * H)
    assert got == ['backlog MC-2 open→blocked: new']


def test_backlog_uses_last_transition_not_first():
    proj = {'backlog': [{'key': 'MC-5', 'text': 't', 'status_history': [
        {'ts': _iso(NOW - 9 * H), 'from': 'open', 'to': 'in_progress'},
        {'ts': _iso(NOW - 60), 'from': 'in_progress', 'to': 'done'}]}]}
    assert tc.backlog_changes_since(proj, NOW - 2 * H) == ['backlog MC-5 in_progress→done: t']


def test_sessions_finished_dispatched_and_sibling_only():
    me = {'session_id': 'me', 'project_id': 'p', '_spawned_by': 'boss'}
    since = NOW - 2 * H
    mk = lambda sid, **kw: {'session_id': sid, 'project_id': 'p', 'status': 'completed',
                            'last_status_change_time': NOW - 60, 'task': f'task {sid}', **kw}
    sessions = [
        me,
        mk('child', _notify_session='me', _spawned_by='me'),       # dispatched by me
        mk('sib', _spawned_by='boss'),                            # same spawner
        mk('stranger', _spawned_by='other'),                      # unrelated
        mk('running-child', _notify_session='me', _spawned_by='me', status='running'),
        mk('old-child', _notify_session='me', _spawned_by='me', last_status_change_time=NOW - 9 * H),
        mk('other-project', _notify_session='me', _spawned_by='me', project_id='q'),
    ]
    got = tc.sessions_finished_since(me, sessions, since)
    assert len(got) == 2
    assert any(g.startswith('dispatched session child completed') for g in got)
    assert any(g.startswith('sibling session sib completed') for g in got)


def test_commits_since_reads_a_real_repo(tmp_path):
    def git(*a, **env):
        import os
        e = {**os.environ, 'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@t',
             'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@t', **env}
        subprocess.run(['git', '-C', str(tmp_path), *a], check=True, capture_output=True, env=e)
    git('init', '-q')
    git('commit', '--allow-empty', '-q', '-m', 'ancient',
        GIT_AUTHOR_DATE='2020-01-01T00:00:00', GIT_COMMITTER_DATE='2020-01-01T00:00:00')
    git('commit', '--allow-empty', '-q', '-m', 'fresh change')
    since = datetime.now(timezone.utc).timestamp() - 3600
    got = tc.commits_since(tmp_path, since)
    assert len(got) == 1 and got[0].endswith('fresh change') and got[0].startswith('commit ')


def test_commits_since_degrades_quietly(tmp_path):
    assert tc.commits_since(None, NOW) == []
    assert tc.commits_since(tmp_path / 'nope', NOW) == []
    assert tc.commits_since(tmp_path, NOW) == []      # exists, not a repo


# ── the cap ──────────────────────────────────────────────────────────────────

def test_cap_truncates_and_says_so(cfg):
    n = 25
    proj = {'backlog': [
        {'key': f'MC-{i}', 'text': 'x', 'status_history': [{'ts': _iso(NOW - 60 - i), 'from': 'a', 'to': 'b'}]}
        for i in range(n)]}
    out = tc.render(proj, {tc._REPLY_KEY: NOW - 5 * H}, now=NOW)
    body = out.split('\n')
    items = [l for l in body if l.startswith('- backlog')]
    assert len(items) == tc.MAX_BRIEF_LINES == 10
    assert body[-1] == f'- (+{n - 10} more, truncated)'


def test_cap_is_fair_across_sources():
    groups = [[f's{i}' for i in range(30)], ['b0', 'b1'], ['c0']]
    lines, dropped = tc._cap(groups)
    assert len(lines) == 10 and dropped == 33 - 10
    assert 'b0' in lines and 'c0' in lines       # a noisy source can't crowd the rest out


def test_exactly_at_cap_is_not_marked_truncated():
    lines, dropped = tc._cap([[f'a{i}' for i in range(10)]])
    assert len(lines) == 10 and dropped == 0


# ── revive: recover last reply time from restored transcript ─────────────────

def test_seed_from_history_uses_last_assistant_line():
    lines = ['\n> Ron: hi\n', 'agent says hello', '\n> Ron: more\n',
             '[— restored from transcript; conversation continues below —]']
    ts = [_iso(NOW - 10 * H), _iso(NOW - 9 * H), _iso(NOW - 8 * H), None]
    s = {}
    tc.seed_from_history(s, lines, ts)
    assert s[tc._REPLY_KEY] == pytest.approx(NOW - 9 * H)


def test_seed_from_history_empty_or_garbage_is_a_noop():
    s = {}
    tc.seed_from_history(s, [], [])
    tc.seed_from_history(s, ['a'], ['garbage'])
    tc.seed_from_history(s, None, None)
    assert tc._REPLY_KEY not in s


# ── never raises ─────────────────────────────────────────────────────────────

def test_render_never_raises_on_garbage(cfg):
    assert tc.render(None, None, now=NOW).startswith('[Now ')
    assert tc.render('x', {tc._REPLY_KEY: NOW - 5 * H}, now=NOW, sessions=[{}, {'session_id': 1}]).startswith('[Now ')


# ── wiring in agent_routes ───────────────────────────────────────────────────

ROUTES = (PROJECT_ROOT / 'mc' / 'blueprints' / 'agent_routes.py').read_text(encoding='utf-8')


def test_clock_wrap_skips_slash_commands_and_respects_setting(monkeypatch):
    import server  # noqa: F401 -- wires the blueprint (and rebinds state.CONFIG, so patch AFTER)
    from mc import state
    from mc.blueprints import agent_routes as ar
    cfg = {}
    monkeypatch.setattr(state, 'CONFIG', cfg)
    sess = {'session_id': 's1', 'project_id': 'p', tc._REPLY_KEY: 1.0}
    proj = {'id': 'p', 'project_path': ''}
    assert ar._clock_wrap(proj, sess, '/goal finish it').startswith('/goal')   # CLI parses /cmd only at byte 0
    wrapped = ar._clock_wrap(proj, sess, 'hello there')
    assert wrapped.startswith('[Now ') and wrapped.endswith('\n\nhello there')
    cfg['turn_clock_enabled'] = False
    assert ar._clock_wrap(proj, sess, 'hello there') == 'hello there'


def test_every_outgoing_site_is_wired():
    """Live stdin write, Mode B respawn, Mode A follow-up, interrupt B + A,
    native follow-up + native interrupt, revive -- plus (round 2) initial dispatch
    helper, steer, queued Mode A follow-up, guardian recovery A+B, revive A/B:
    13 `_clock_wrap` call sites -- plus both
    stream readers stamping replies."""
    assert len(re.findall(r'_clock_wrap\(', ROUTES)) == 14, (
        'expected the def + 13 call sites of _clock_wrap')
    assert ROUTES.count('_turn_clock.note_reply(session)') == 2
    assert 'seed_from_history(_clock_sess, history_lines, history_ts)' in ROUTES


def test_stamp_is_the_first_thing_in_the_live_stdin_write():
    i = ROUTES.index('def _write_mode_b_stdin(')
    body = ROUTES[i:i + 2500]
    assert body.index('refresh_for_turn') < body.index('_clock_wrap(project, sess, out, msg)'), (
        'the clock wrap must be applied AFTER memory assembly so it is the leading line')


def test_runtime_stamps_non_claude_turns():
    src = (PROJECT_ROOT / 'mc' / 'agent_runtime.py').read_text(encoding='utf-8')
    assert src.count('_turn_clock.note_reply(session)') == 2


def test_steward_cycle_marker_stays_the_first_bytes(monkeypatch):
    """`[Steward cycle]` is matched by prefix by the steward gates; a leading
    clock line would silently un-gate it."""
    import server  # noqa: F401
    from mc import state
    from mc.blueprints import agent_routes as ar
    monkeypatch.setattr(state, 'CONFIG', {})
    sess = {'session_id': 's1', 'project_id': 'p', tc._REPLY_KEY: 1.0}
    msg = '[Steward cycle]\n\nWake (local time): now'
    assert ar._clock_wrap({'project_path': ''}, sess, msg) == msg


def test_strip_injected_preamble_peels_clock_header_then_memory():
    """The stamp (+ briefing) leads the memory block, so the transcript
    stripper must peel it or every rebuilt user turn shows it."""
    from mc.agent_runtime import strip_injected_preamble
    mem = '--- RELEVANT MEMORY (auto) ---\n  • fact one\n\n'
    stamp = '[Now 2026-11-04 07:10 PST · 9h 20m since your last reply]'
    brief = 'Since your last reply:\n- commit abc fix\n- (+6 more, truncated)'
    assert strip_injected_preamble(f'{stamp}\n\n{mem}hello there') == 'hello there'
    assert strip_injected_preamble(f'{stamp}\n{brief}\n\n{mem}hello there') == 'hello there'
    assert strip_injected_preamble(f'{stamp}\n{brief}\n\nhello there') == 'hello there'
    assert strip_injected_preamble('hello there') == 'hello there'


def test_strip_injected_preamble_on_real_render_output():
    import time
    from mc.agent_runtime import strip_injected_preamble
    sess = {'session_id': 's1', 'project_id': 'p', tc._REPLY_KEY: time.time() - 9 * 3600}
    header = tc.render({'id': 'p', 'project_path': ''}, sess)
    assert header.startswith('[Now ')
    mem = '--- RELEVANT MEMORY (auto) ---\n  • fact one\n\n'
    assert strip_injected_preamble(f'{header}\n\n{mem}hello there') == 'hello there'
