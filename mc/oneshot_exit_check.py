"""Decide whether a finished one-shot transform subprocess produced an answer.

`run_text_transform` treats a runtime's `oneshot()` returning a result as a
completed transform and only a `None` as a failure (mc/agent_runtime.py). The
Gemini and Qwen `oneshot` never looked at the exit code, so a quota, auth or
network failure came back as `''`, a fragment, or (Qwen) the raw JSON event
stream, delivered to Scribe, condense, the Distiller and the mail launderer as
the model's output (MC-1179 G1, Q1). Their `describe_image` already did this
check; this module is that check in one place so both `oneshot` paths share it.
"""
import re
from typing import Optional

# qwen-code 0.23.4 on Windows can crash with a native libuv assertion during
# its own teardown AFTER it has written the complete reply (live-verified
# 2026-09-15; see QwenRuntime.explain_exit_error). The answer is good and only
# the exit status is wrong, so it is the one non-zero exit that is not a failure.
_QWEN_TEARDOWN_CRASH_RE = re.compile(
    r'uv_handle_closing|assertion failed[^\n]*async\.c', re.IGNORECASE)


def is_qwen_teardown_crash(*streams: Optional[str]) -> bool:
    return any(_QWEN_TEARDOWN_CRASH_RE.search(s or '') for s in streams)


def failure_detail(returncode: int, stderr: Optional[str], text: str, *,
                   error_text: str = '', benign_exit: bool = False) -> Optional[str]:
    """None when the run is a usable answer, else the `last_error` string.

    `error_text` is a vendor-reported error (an `is_error` result event):
    always a failure, whatever the exit code says. A non-zero exit is a failure
    unless `benign_exit` (a known teardown crash after a complete reply) and no
    error was reported. An empty answer is a failure. stderr is the reason and is kept (tail 300).
    """
    tail = (stderr or '').strip().replace('\n', ' ')
    err = f'error: {error_text.strip()[:300]}' if error_text else ''
    if returncode != 0 and not (benign_exit and not err):
        return f'rc={returncode}: {tail[-300:]}' + (f' | {err}' if err else '')
    if err:
        return err
    if not (text or '').strip():
        return 'empty output'
    return None
