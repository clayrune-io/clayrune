"""Token-free allowance probes — the evidence a stale exhaustion record is
checked against (mc/allowance_state.py `heal`).

An allowance record is only as good as its evidence, and the evidence is one
failed run. Once the user buys more quota nothing corrects it (2026-09-19:
Codex refused for five days after a top-up), so a dispatch that would be
refused may first ask the vendor whether it is still out.

Only vendors with a real, non-spending read belong here. Today that is Codex:
its app-server answers `account/rateLimits/read` from the account backend
without starting a turn (no model call, no tokens). Live-captured 2026-09-19
against codex-cli 0.154.0:

  {"id":2,"result":{"ordinaryUsageAllowed":true,"rateLimits":{...
   "rateLimitReachedType":null,...}}}

Claude, Gemini and Qwen have NO cheap probe: their CLIs report quota only as
part of a real run (Claude's `rate_limit_event` arrives on the stream and
already clears the record in system_routes when it says "allowed"; Gemini and
Qwen give plain error text). Their runtimes inherit `probe_allowance() -> None`
and the "I topped up" control is the only way to re-check them — deliberately
no invented probe.
"""

from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
import time
from typing import List, Optional

from mc.core import _log

_PROBE_TIMEOUT_S = 12.0


def _tree_kill(proc: subprocess.Popen) -> None:
    """Kill the probe's own process tree by the PID we got back. On Windows
    the npm shim (`codex.cmd`) spawns node which spawns codex.exe, and
    killing only the shim orphans the other two."""
    try:
        if sys.platform == 'win32':
            subprocess.run(['taskkill', '/F', '/T', '/PID', str(proc.pid)],
                           capture_output=True, timeout=5)
        else:
            proc.kill()
    except Exception as e:
        _log(f"[allowance] probe teardown failed: {e}", flush=True)


def _parse_window(win) -> Optional[dict]:
    """One app-server rate-limit window (`usedPercent`, `windowDurationMins`,
    `resetsAt`) in the rollout file's snake_case shape (`used_percent`,
    `window_minutes`, `resets_at`), so callers reading either source share one
    window parser. None when the slot is absent."""
    if not isinstance(win, dict):
        return None

    def _pick(*keys):
        for k in keys:
            if win.get(k) is not None:
                return win.get(k)
        return None

    return {
        'used_percent': _pick('usedPercent', 'used_percent'),
        'window_minutes': _pick('windowDurationMins', 'window_minutes'),
        'resets_at': _pick('resetsAt', 'resets_at'),
    }


def parse_rate_limits_result(result) -> Optional[dict]:
    """Parse an `account/rateLimits/read` `result` into

        {'ordinary_usage_allowed': bool|None,
         'rate_limits': {'primary': win|None, 'secondary': win|None,
                         'credits': {has_credits, unlimited, balance}|None,
                         'plan_type': str|None,
                         'rate_limit_reached_type': str|None}}

    `rate_limits` mirrors the `rate_limits` block of a rollout `token_count`
    line (snake_case, same window keys) so the usage bar reads both sources
    with the same code. None when `result` is not a dict."""
    if not isinstance(result, dict):
        return None
    allowed = result.get('ordinaryUsageAllowed')
    raw = result.get('rateLimits')
    raw = raw if isinstance(raw, dict) else {}
    credits = raw.get('credits')
    if isinstance(credits, dict):
        credits = {
            'has_credits': credits.get('hasCredits', credits.get('has_credits')),
            'unlimited': credits.get('unlimited'),
            'balance': credits.get('balance'),
        }
    else:
        credits = None
    return {
        'ordinary_usage_allowed': allowed if isinstance(allowed, bool) else None,
        'rate_limits': {
            'primary': _parse_window(raw.get('primary')),
            'secondary': _parse_window(raw.get('secondary')),
            'credits': credits,
            'plan_type': raw.get('planType', raw.get('plan_type')),
            'rate_limit_reached_type': raw.get(
                'rateLimitReachedType', raw.get('rate_limit_reached_type')),
        },
    }


def ordinary_usage_allowed_from(snapshot: Optional[dict]) -> Optional[bool]:
    """Derive the allowance verdict from a `parse_rate_limits_result` snapshot.

    True  = the backend says usage is allowed and no rate limit is reached.
    False = the backend says it is reached / not allowed.
    None  = unanswered, or the fields were absent. The schema says an absent
            `ordinaryUsageAllowed` must not be read as recovery, and a False
            can coexist with purchased credits, so callers must treat only
            True as evidence.
    """
    if not isinstance(snapshot, dict):
        return None
    allowed = snapshot.get('ordinary_usage_allowed')
    reached = (snapshot.get('rate_limits') or {}).get('rate_limit_reached_type')
    if allowed is True and not reached:
        return True
    if allowed is False or reached:
        return False
    return None


def codex_ordinary_usage_allowed(cmd_prefix: List[str],
                                 timeout_s: float = _PROBE_TIMEOUT_S,
                                 popen_kwargs: Optional[dict] = None
                                 ) -> Optional[bool]:
    """Ask the Codex app-server whether ordinary usage is allowed (see
    `ordinary_usage_allowed_from` for the True/False/None contract).
    Never raises."""
    return ordinary_usage_allowed_from(
        codex_rate_limits_snapshot(cmd_prefix, timeout_s, popen_kwargs))


def codex_rate_limits_snapshot(cmd_prefix: List[str],
                               timeout_s: float = _PROBE_TIMEOUT_S,
                               popen_kwargs: Optional[dict] = None
                               ) -> Optional[dict]:
    """Ask the Codex app-server for `account/rateLimits/read` and return the
    parsed snapshot (see `parse_rate_limits_result`), or None if it did not
    answer within `timeout_s`. Never raises. The single JSON-RPC handshake
    behind every token-free Codex read (allowance verdict, usage bar).
    """
    proc = None
    try:
        proc = subprocess.Popen(
            list(cmd_prefix) + ['app-server', '--listen', 'stdio://'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding='utf-8',
            **(popen_kwargs or {}))
        lines: 'queue.Queue[Optional[str]]' = queue.Queue()

        def _reader(stream=proc.stdout):
            try:
                for ln in (stream or ()):
                    lines.put(ln)
            finally:
                lines.put(None)

        threading.Thread(target=_reader, daemon=True).start()

        def _send(obj: dict) -> None:
            assert proc is not None and proc.stdin is not None
            proc.stdin.write(json.dumps(obj) + '\n')
            proc.stdin.flush()

        _send({'id': 1, 'method': 'initialize', 'params': {
            'clientInfo': {'name': 'clayrune', 'title': 'Clayrune',
                           'version': '0'}}})
        deadline = time.monotonic() + timeout_s
        sent_read = False
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                ln = lines.get(timeout=remaining)
            except queue.Empty:
                return None
            if ln is None:
                return None
            try:
                msg = json.loads(ln)
            except Exception:
                continue
            if not isinstance(msg, dict):
                continue
            if msg.get('id') == 1 and not sent_read:
                _send({'method': 'initialized'})
                _send({'id': 2, 'method': 'account/rateLimits/read'})
                sent_read = True
            elif msg.get('id') == 2:
                return parse_rate_limits_result(msg.get('result'))
    except Exception as e:
        _log(f"[allowance] codex probe failed: {e}", flush=True)
        return None
    finally:
        if proc is not None:
            try:
                if proc.stdin:
                    proc.stdin.close()
            except Exception:
                pass
            _tree_kill(proc)
