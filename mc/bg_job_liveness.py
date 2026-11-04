"""Background-job facts for the mid-turn rollover state block (MC-1149 phase 1).

The rollover state block used to say only "(Started with run_in_background
under the old process; the roll ends that process tree, so relaunch any that
are still needed.)" -- no output path, no word on whether the job actually
died, so the fresh session either relaunched a run that had survived
(duplicates) or never found the output of one that had finished.

What the CLI gives us, measured 2026-11-04 against claude 2.1.x on Windows:

  * The Bash tool_result for a background job (deliberate, or a foreground
    call moved to the background after its timeout) names the task id and
    the output file: "... (ID: bq3btoz07). Output is being written to:
    C:\\...\\tasks\\bq3btoz07.output. ..." / "Command running in background
    with ID: ...". No PID is reported anywhere.
  * The job's own shell processes hold that output file open as their
    stdout (bash.exe, plus whatever it runs). The CLI process itself does not.
  * Those shells are descendants of the CLI process at the moment the
    tool_result arrives. Scanning only the CLI's descendants for the file
    costs ~1.4s for 17 children; scanning every process on the box costs
    ~63s, which rules out a system-wide scan at roll time.

So the job's processes are recorded when the tool_result arrives
(`holder_pids`, pid + create_time, so a recycled pid cannot pass), and at
roll time -- after the interrupt path has tree-killed the old CLI -- the
block reports whether any of them still exist (`liveness`). A job whose
processes could not be recorded is reported `unknown`, with the output
file's size and age as the evidence the agent does have.

Leaf module: no Flask, no session store. mc/midturn_rollover.py owns the
`_mt_bg_jobs` entries and calls in here to fill and render them. Every
public function catches its own errors; nothing here may take down the
stream reader or the roll.
"""
from __future__ import annotations

import os
import re
import time
from typing import Any, Dict, List, Optional

from mc.core import _log

# Seconds `holder_pids` may spend enumerating the CLI's descendants. One
# slow/denied process (node.exe open_files was AccessDenied in the probe)
# must not stall the stream reader; whatever was found by then is kept.
HOLDER_SCAN_BUDGET_S = 3.0
_HOLDER_CAP = 8
# Processes that never hold a job's output file but are expensive or denied
# to inspect: the CLI itself and console hosts.
_SKIP_NAMES = frozenset({'node.exe', 'claude.exe', 'conhost.exe', 'node', 'claude'})

# Claude Code output files are always `<task id>.output`; the lazy match up
# to that suffix keeps the trailing ". You will be notified..." out of the
# path. The fallback takes the rest of the line for a CLI that changes the
# naming.
_OUTPUT_RE = re.compile(r'Output is being written to:\s*(.+?\.output)\b')
_OUTPUT_FALLBACK_RE = re.compile(r'Output is being written to:\s*([^\n]+?)(?:\.\s|\.$|$)')
_TASK_ID_RE = re.compile(r'(?:\(ID:|with ID:|task ID:)\s*([A-Za-z0-9_-]+)')
_MOVED_RE = re.compile(r'moved to the background', re.I)
_BG_HINT_RE = re.compile(r'running in background|moved to the background|'
                         r'Output is being written to:', re.I)


def parse_tool_result(text: Any) -> Optional[Dict[str, Any]]:
    """Facts from a Bash tool_result that announces a background job, or None
    when the text is not one. Never raises."""
    if not isinstance(text, str) or not text or not _BG_HINT_RE.search(text):
        return None
    try:
        m = _OUTPUT_RE.search(text) or _OUTPUT_FALLBACK_RE.search(text)
        output_file = m.group(1).strip() if m else None
        t = _TASK_ID_RE.search(text)
        return {
            'output_file': output_file,
            'task_id': t.group(1) if t else None,
            'moved_on_timeout': bool(_MOVED_RE.search(text)),
        }
    except Exception as e:
        _log(f"[bg-job-liveness] parse failed: {e}")
        return None


def _same_file(a: str, b: str) -> bool:
    try:
        return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))
    except Exception:
        return False


def holder_pids(output_file: Optional[str], root_pid: Any,
                budget_s: float = HOLDER_SCAN_BUDGET_S) -> Optional[List[Dict[str, Any]]]:
    """The descendants of `root_pid` (the CLI process) that hold `output_file`
    open, as [{'pid', 'create_time', 'name'}]. None when nothing could be
    determined (no path, no psutil, root gone, scan failed); [] when the
    scan ran and found no holder (a job that already finished). Never raises."""
    if not output_file or not isinstance(root_pid, int) or root_pid <= 0:
        return None
    try:
        import psutil  # type: ignore[import-not-found]
    except Exception:
        return None
    try:
        kids = psutil.Process(root_pid).children(recursive=True)
    except Exception as e:
        _log(f"[bg-job-liveness] descendants of pid {root_pid} unavailable: {e}")
        return None
    found: List[Dict[str, Any]] = []
    deadline = time.monotonic() + max(0.0, budget_s)
    for k in kids:
        if time.monotonic() > deadline:
            _log(f"[bg-job-liveness] holder scan hit {budget_s}s budget after "
                 f"{len(found)} holder(s); remaining descendants not inspected")
            break
        try:
            name = k.name()
            if name.lower() in _SKIP_NAMES:
                continue
            if any(_same_file(f.path, output_file) for f in k.open_files()):
                found.append({'pid': k.pid, 'create_time': float(k.create_time()),
                              'name': name})
                if len(found) >= _HOLDER_CAP:
                    break
        except Exception:
            continue
    return found


def liveness(holders: Any) -> str:
    """'alive' when any recorded holder process still exists with the same
    create_time, 'dead' when all recorded ones are gone, 'unknown' when none
    were recorded. Never raises."""
    if not isinstance(holders, list) or not holders:
        return 'unknown'
    try:
        import psutil  # type: ignore[import-not-found]
    except Exception:
        return 'unknown'
    for h in holders:
        try:
            p = psutil.Process(int(h['pid']))
            if abs(p.create_time() - float(h.get('create_time') or 0)) < 1.0:
                return 'alive'
        except Exception:
            continue
    return 'dead'


def file_stats(path: Optional[str], now: Optional[float] = None) -> str:
    """'<size> bytes, last written <age>s before the roll', or why not."""
    if not path:
        return 'unknown (tool_result not seen)'
    try:
        st = os.stat(path)
    except FileNotFoundError:
        return f'{path} (missing)'
    except Exception as e:
        return f'{path} (unreadable: {e})'
    age = int(max(0.0, (now if now is not None else time.time()) - st.st_mtime))
    return f'{path} ({st.st_size} bytes, last written {age}s before the roll)'


def render_jobs(jobs: List[Dict[str, Any]], *, job_url: str,
                now: Optional[float] = None) -> str:
    """The "Background jobs" section body for the rollover state block.
    `jobs` are mc.midturn_rollover's `_mt_bg_jobs` entries. Never raises."""
    if not jobs:
        return '(none)'
    try:
        lines: List[str] = []
        for j in jobs:
            head = f"- {j.get('tool') or '(tool)'}: {j.get('input') or ''}".rstrip()
            if j.get('task_id'):
                head += f"  [task {j['task_id']}]"
            if j.get('moved_on_timeout'):
                head += '  (moved to the background by the CLI timeout)'
            lines.append(head)
            lines.append(f"  output file: {file_stats(j.get('output_file'), now)}")
            state = liveness(j.get('holders'))
            holders = j.get('holders') or []
            if state == 'alive':
                pids = ', '.join(str(h.get('pid')) for h in holders)
                lines.append(f"  process: ALIVE (survived the roll; pid {pids}) -> do NOT "
                             "relaunch; Read the output file for its result.")
            elif state == 'dead':
                lines.append("  process: DEAD (ended with the old process tree) -> if still "
                             "needed, relaunch it via the job route below, not "
                             "run_in_background.")
            else:
                why = ('scan ran, no process held the output file when it started -- '
                       'it may have finished at once'
                       if isinstance(j.get('holders'), list) else
                       'no process was recorded when it started')
                lines.append(f"  process: unknown ({why}) -> check the output file "
                             "(size/age above) before relaunching; relaunch only via "
                             "the job route below.")
        lines.append(f"Job route (survives a rollover): POST {job_url} "
                     '{"command":"...","timeout_minutes":optional,"shell":optional}')
        return '\n'.join(lines)
    except Exception as e:
        _log(f"[bg-job-liveness] render failed: {e}")
        return '\n'.join(f"- {j.get('tool')}: {j.get('input')}" for j in jobs)
