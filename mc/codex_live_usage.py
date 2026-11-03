"""Live Codex rate-limit snapshot for the usage bar.

The bar's Codex number comes from the latest rollout file's last `token_count`
line (system_routes `_fetch_codex_weekly_usage` / `_fetch_codex_usage_detail`).
Rollouts are only written when a Codex run happens, so a reset or top-up done
in the Codex app never reached the bar until the next run (2026-11-03: bar read
100% while the account read 0%).

This module supplies the other source: the same token-free app-server read the
allowance probe already does (`allowance_probe.codex_rate_limits_snapshot`).
It owns only WHEN to ask and the cache:

  * a rollout sample newer than `STALE_AFTER_S` is trusted, no spawn;
  * otherwise one app-server read, cached for `PROBE_TTL_S` (failures too, so
    a broken binary is not re-spawned on every poll of the bar);
  * single-flight: concurrent pollers wait on the one read in progress and
    then take its cached result;
  * `request_fresh()` (the manual refresh) bypasses both gates once.

Choosing between the probe and the rollout sample (newest wins) is the
caller's job; `probe_if_stale` returns the snapshot with its `probed_at`.
"""

from __future__ import annotations

import threading
import time
from typing import Optional

from mc.core import _log

STALE_AFTER_S = 120.0   # a rollout event younger than this is trusted as-is
PROBE_TTL_S = 60.0      # matches system_routes' _CODEX_DETAIL_TTL cadence

_lock = threading.Lock()
_cache: dict = {'ts': 0.0, 'data': None}
_force = False


def _read_snapshot() -> Optional[dict]:
    """The app-server read. Seam: tests replace this, so no test spawns codex.
    Never raises."""
    try:
        from mc import agent_runtime
        return agent_runtime.get_runtime('codex').read_rate_limits()  # type: ignore[attr-defined]
    except Exception as e:
        _log(f"[codex-live-usage] snapshot read failed: {e}", flush=True)
        return None


def request_fresh() -> None:
    """Make the next `probe_if_stale` call read the app-server regardless of
    cache age or rollout freshness (POST /api/system/usage/refresh)."""
    global _force
    with _lock:
        _cache['ts'] = 0.0
        _cache['data'] = None
        _force = True


def reset_for_tests() -> None:
    global _force
    with _lock:
        _cache['ts'] = 0.0
        _cache['data'] = None
        _force = False


def probe_if_stale(rollout_event_epoch: Optional[float],
                   now: Optional[float] = None) -> Optional[dict]:
    """Return {'rate_limits': {...rollout-shaped...}, 'probed_at': epoch} from
    a live app-server read, or None when no read is warranted (the rollout
    sample is fresh) or the read failed. `rollout_event_epoch` is the newest
    rollout sample's event time, None when there is no rollout sample."""
    global _force
    now = time.time() if now is None else now
    with _lock:
        if not _force:
            if (now - _cache['ts']) < PROBE_TTL_S and _cache['ts'] > 0.0:
                return _cache['data']
            if (rollout_event_epoch is not None
                    and (now - rollout_event_epoch) < STALE_AFTER_S):
                return None
        _force = False
        snap = _read_snapshot()
        data = None
        if isinstance(snap, dict) and isinstance(snap.get('rate_limits'), dict):
            data = {'rate_limits': snap['rate_limits'], 'probed_at': time.time()}
        else:
            _log("[codex-live-usage] app-server read gave no snapshot; "
                 "falling back to the rollout sample", flush=True)
        _cache['ts'] = now
        _cache['data'] = data
        return data
