"""A1 follow-up (MC-1179): every bracketed line Clayrune appends to `log_lines`
is a status line, whatever text it embeds, and model text that begins with '['
stays reply text.

The inventory below is the append sites found by grep in mc/, server.py and
steward/ on 2026-11-17 (file:line in the comment). The first rewrite of
`is_status_line` treated only a bracket pair with NO brackets inside as a
status line, so `[stdin write error: [Errno 22] Invalid argument]` became reply
text and the saved summary of a crashed session was the error line.
"""
import pytest

from mc import agent_runtime as ar
from mc import reply_line_kind as rlk

collect = ar._collect_trailing_reply_text

# Texts an interpolated exception or message can carry.
PLAIN = 'boom'
BRACKETED = '[Errno 22] Invalid argument'
WINERR = '[WinError 5] Access is denied'
UNBALANCED = 'bad [ pattern'
STRAY_CLOSE = 'odd ] value'
EMBEDDED = [PLAIN, BRACKETED, WINERR]

# Forms that interpolate exception/user text: (template with {x}).
INTERPOLATED = [
    '[callback post_turn error: {x}]',                      # agent_runtime.py:5006, 6530
    '[stream error: {x}]',                                  # agent_runtime.py:5179,6748; agent_routes 6391,6789; terminal_routes
    '[capture error: {x}]',                                 # agent_runtime.py:6587
    '[capture EOF error: {x}]',                             # agent_runtime.py:6765
    '[backlog-sync error: {x}]',                            # agent_routes.py:6296,6714
    '[Fresh restart also failed: {x}]',                     # agent_routes.py:6899
    '[follow-up failed: {x}]',                              # agent_routes.py:10832,10867
    '[follow-up reservation failed; message kept queued: {x}]',  # agent_routes.py:10847
    '[qwen dispatch failed: {x}]',                          # agent_routes.py:11537,11657
    '[Image bridge failed, images were NOT described: {x}]',  # agent_routes.py:12247
    '[stdin write error on dispatch: {x}]',                 # agent_routes.py:13412
    '[codex followup error: {x}]',                          # agent_routes.py:14641
    '[respawn error: {x}]',                                 # agent_routes.py:15142
    '[follow-up process failed: {x}]',                      # agent_routes.py:15246
    '[stdin write error: {x}]',                             # agent_routes.py:15376; scheduler_routes.py:1271
    '[gemini interrupt error: {x}]',                        # agent_routes.py:15656
    '[interrupt-resume error: {x}]',                        # agent_routes.py:15937
    '[hook followup failed: {x}]',                          # server.py:3478
    '[queued follow-up not sent ({x}): {x}]',               # mid_turn_send.py:178
    '[question mail held: {x}]',                            # question_channel.py:314
    '[question NOT confirmed sent: {x}. It will NOT be re-sent automatically]',  # question_send_failure.py:157
    '[question NOT sent: {x}. Attempt 1 of 3; retrying]',   # question_send_failure.py:161
    '[question NOT sent: {x}. Gave up after 3 attempts. Ask again]',  # :164
    '[question NOT sent: {x}. It will not be retried. Ask again]',    # :167
    '[Waiting for usage reset until {x}]',                  # limit_resume.py:185 (a timestamp)
    '[launch note: {x}]',                                   # agent_routes.py:592,611,8056 ([{note}])
    '[{x}; unattended OpenCode is refused while fencing is enabled]',  # vendor_fence.py:51
    '[terminal:abc123:{x}]',                                # terminal_routes.py:344,358
]

# Fixed-text forms.
FIXED = [
    '[interrupted]',                                        # agent_runtime.py:5347,6881
    '[gemini exited with code 1]',                          # agent_runtime.py:5228
    '[exited with code 1]',                                 # agent_routes.py:6415,6560
    '[gemini binary missing \u2014 cannot continue]',       # agent_runtime.py:5263
    '[Plan mode exit detected \u2014 waiting for user approval]',  # agent_routes.py:6283,6701
    '[\u2014 restored from transcript; conversation continues below \u2014]',  # :7668
    '[agent started a new turn on its own \u2014 not sent from Clayrune]',     # :9410
    '[follow-up skipped: project not found]',               # :10786
    '[follow-up skipped: project path invalid]',            # :10790
    '[follow-up queued: the session resumed while it was being prepared]',     # :10821
    '[Settings changed \u2014 applying via resume]',        # :14875
    '[Agent stopped by user]',                              # :15272
    '[Got your message]',                                   # :15622
    '[Guardian: retry requested by user]',                  # :16470
    '[\u2014 read-only history; send a message to resume this session \u2014]',  # :16820,16839,16919,16997
    '[\u2014 read-only history from the run log; claude keeps no full transcript \u2014]',  # :16952
    '[Process killed via Process Manager]',                 # system_routes.py:236
    '[Reset continuation needs manual review; continue manually or cancel.]',   # limit_resume.py:354
    '[Automatic reset continuation cancelled.]',            # limit_resume_dispatch.py:188
    '[API credits: subscription replay failed]',            # api_credits_session.py:281
    '[interrupted via runtime hook]',                       # server.py:3499
    '[stop-hook-redo:claude]',                              # agent_routes.py:4582
    '[Waiting for your answer \u2014 choose above to continue]',
]

# Labelled forms: label + payload, the payload may hold anything.
LABELLED = [
    '[tool: Read] C:/a/b.py',
    '[tool: Bash] ls -la [abc]',
    '[tool: Bash] echo "[Errno 22]"',
    '[denied: Bash] ls',
    '[denied: Bash] rm -rf [a-z]*',
    '[denied: Edit] C:/a/[x].py',
    '[denied: tool]',
    '[hint] not logged in',
    '[hint] [WinError 5] Access is denied',
    '[qwen error] [API Error: quota exceeded]',
    '[claude warn] cli gate [x]',
    '[codex warn] rmcp AuthRequired',
    '[mcp-sync] failed (best-effort): [Errno 2] missing',
]


def _expanded(templates):
    return [t.format(x=x) for t in templates for x in EMBEDDED]


@pytest.mark.parametrize('line', _expanded(INTERPOLATED))
def test_interpolated_status_lines_with_plain_and_bracketed_text(line):
    assert rlk.is_status_line(line), line


@pytest.mark.parametrize('line', [t.format(x=x) for t in INTERPOLATED
                                  for x in (UNBALANCED, STRAY_CLOSE)
                                  if not t.startswith('[terminal:')
                                  and 'unattended' not in t and 'launch note' not in t
                                  and 'usage reset' not in t])
def test_interpolated_status_lines_with_unbalanced_exception_text(line):
    assert rlk.is_status_line(line), line


@pytest.mark.parametrize('line', FIXED)
def test_fixed_status_lines(line):
    assert rlk.is_status_line(line), line


@pytest.mark.parametrize('line', LABELLED)
def test_labelled_status_lines(line):
    assert rlk.is_status_line(line), line


@pytest.mark.parametrize('line', _expanded(INTERPOLATED) + FIXED + LABELLED)
def test_every_inventory_form_ends_the_reply_walk(line):
    assert collect(['The real reply.', line]) == 'The real reply.'
    assert collect(['Old.', line, 'New ', 'reply']) == 'New reply'


def test_crash_tail_keeps_the_real_reply():
    """Live log 2026-11-17: the last two lines of a crashed session."""
    assert collect([
        'Here is the finished analysis.',
        '[stdin write error: [Errno 22] Invalid argument]',
        '[exited with code 1]',
    ]) == 'Here is the finished analysis.'


def test_denied_line_does_not_become_the_reply():
    assert collect(['The answer.', '[denied: Bash] ls']) == 'The answer.'


@pytest.mark.parametrize('line', [
    '[Docs](http://x) are here',
    '[1, 2, 3] is the list',
    '[docs](x)',
    '[docs](x) and [more](y)',
    '[1] First point, see [2]',
    '[1] first point',
    '[file:C:/a/b.md] is the report',
    '[file:C:/a/b.md|Full report] is attached',
    # Near the `coverage` / `context trim` labels, but not them.
    '[coverage report](http://x) is here',
    '[coverage: 91%] of lines are hit',
    '[context] matters here',
    '[context trimming] was discussed',
    '[{"a": 1}, {"b": 2}] are the rows',
])
def test_model_text_that_begins_with_a_bracket_is_reply_text(line):
    assert not rlk.is_status_line(line), line
    assert collect(['Earlier.', '[tool: Read] x', line]) == line


def test_non_bracket_lines_are_never_status():
    assert not rlk.is_status_line('plain text')
    assert not rlk.is_status_line('')
