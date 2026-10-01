#!/usr/bin/env python
"""Stamp `human_joined` on scheduled/steward log rows a human actually typed into.

A scheduled run a person chatted inside is that person's chat, but its
agent_log row says trigger_type 'schedule' and the Chats list drops every such
row after a restart (conversation.js `_isNoiseConvoRow`). New joins are stamped
live by agent_routes `_mark_human_joined`; this repairs the ones that happened
before it existed.

A row qualifies when ANY transcript it ran under (`claude_session_id` plus
`claude_session_ids`) holds a genuine human-typed user turn. Not counted:
tool results, the scheduled prompt itself (the row's `task`, or a
'[Scheduled run' continuation header), '[Steward cycle]' prompts,
<task-notification>/<system-reminder> blocks, turns that are only an injected
'---'/'===' context block, Stop-hook feedback, and isMeta/isSynthetic turns.

`trigger_type` is never changed -- the flag is display-only, so the fence keeps
treating these sessions as unattended.

DRY RUN BY DEFAULT -- prints what it would stamp and writes nothing. Pass
--apply to write. Idempotent: a row already stamped is skipped.

    python tools/backfill-human-joined.py                               # all projects, dry run
    python tools/backfill-human-joined.py --project drop_shipping_company
    python tools/backfill-human-joined.py --project drop_shipping_company --session 88001c6d8849 --apply
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from mc import agent_runtime as _art           # noqa: E402
from mc import memory as _memory               # noqa: E402
from mc.atomic_json import write_json_atomic   # noqa: E402

# Mirror of agent_routes._HUMAN_JOINABLE_TRIGGERS.
JOINABLE = {'schedule', 'steward', 'night-review'}

_MACHINE_PREFIX_RE = re.compile(
    r'^\s*(?:\[Scheduled run\b|\[Steward cycle\]|\[Night review\b|<task-notification|'
    r'<system-reminder|Stop hook feedback|\[dispatched agent finished\]|(?:---|===)\s)',
    re.IGNORECASE)
_RESUME_NUDGE_RE = re.compile(r'^\s*continue (?:from where|where) (?:you|we) left off', re.IGNORECASE)


def data_dir() -> Path:
    """Same resolution server.py uses: MC_DATA_DIR, else the repo."""
    return Path(os.environ.get('MC_DATA_DIR') or REPO_ROOT) / 'data' / 'projects'


def _norm(s: str) -> str:
    return ' '.join((s or '').split())


def _is_scheduled_prompt(text: str, task: str) -> bool:
    """`text` is (a continuation of) the row's own scheduled prompt."""
    a, b = _norm(text), _norm(task)
    if not a or not b:
        return False
    n = min(len(a), len(b), 80)
    return n >= 20 and a[:n] == b[:n]


def genuine_human_turns(transcript: Path, task: str) -> list[str]:
    """User turns in `transcript` that a person typed (see module docstring)."""
    rt = _art.get_runtime('claude')
    out: list[str] = []
    try:
        fh = open(transcript, 'r', encoding='utf-8', errors='replace')
    except OSError:
        return out
    with fh:
        for line in fh:
            ev = rt.parse_event(line)  # pyright: ignore[reportAttributeAccessIssue]
            if ev is None or ev.type != _art.EventType.USER_MESSAGE:
                continue
            if ev.payload.get('role') != 'user':
                continue
            raw = ev.raw or {}
            if raw.get('isMeta') or raw.get('isSynthetic') or _art.is_stop_hook_feedback(raw):
                continue
            content = ev.payload.get('content', '')
            if isinstance(content, list):
                text = ' '.join(str(b.get('text', '')).strip() for b in content
                                if isinstance(b, dict) and b.get('type') == 'text').strip()
            else:
                text = str(content).strip() if content else ''
            if not text or _MACHINE_PREFIX_RE.match(text):
                continue
            clean = _art.strip_injected_preamble(text)
            if (not clean or _MACHINE_PREFIX_RE.match(clean) or _art.is_nonuser_message(clean)
                    or _RESUME_NUDGE_RE.match(clean) or _is_scheduled_prompt(clean, task)):
                continue
            out.append(clean)
    return out


def project_path_for(projects_dir: Path, project_id: str) -> str:
    try:
        return json.loads((projects_dir / f'{project_id}.json').read_text(
            encoding='utf-8')).get('project_path', '')
    except Exception as e:
        print(f'  ! cannot read {project_id}.json: {e}')
        return ''


def run(projects_dir: Path, project_id: str, only_session: str, apply: bool) -> Counter:
    counts: Counter = Counter()
    log_file = projects_dir / f'{project_id}_agent_log.json'
    try:
        log = json.loads(log_file.read_text(encoding='utf-8'))
    except Exception as e:
        print(f'  ! {log_file.name} does not parse ({e}) -- SKIPPING, not rewriting')
        counts['unreadable'] += 1
        return counts
    if not isinstance(log, list):
        print(f'  ! {log_file.name} is not a list -- skipping')
        counts['unreadable'] += 1
        return counts
    pp = project_path_for(projects_dir, project_id)
    if not pp:
        print('  ! no project_path on the record -- cannot locate transcripts')
        return counts

    # One verdict per MC session: every row of a continued scheduled run shares
    # a session_id, and they are one conversation.
    verdict: dict[str, tuple[bool, str]] = {}
    changed = 0
    for row in log:
        if not isinstance(row, dict) or (row.get('trigger_type') or '') not in JOINABLE:
            continue
        if row.get('human_joined'):
            counts['already-stamped'] += 1
            continue
        sid = row.get('session_id') or ''
        if only_session and sid != only_session:
            continue
        key = sid or (row.get('claude_session_id') or '')
        if key not in verdict:
            task = row.get('task') or ''
            csids = [c for c in [row.get('claude_session_id')] + list(row.get('claude_session_ids') or []) if c]
            found, sample = False, ''
            for log_row in log:   # every transcript any row of this session ran under
                if isinstance(log_row, dict) and sid and log_row.get('session_id') == sid:
                    for c in [log_row.get('claude_session_id')] + list(log_row.get('claude_session_ids') or []):
                        if c and c not in csids:
                            csids.append(c)
            for csid in csids:
                f = _memory._find_transcript_file(pp, csid)
                if not f:
                    continue
                turns = genuine_human_turns(f, task)
                if turns:
                    found, sample = True, turns[0][:80].replace('\n', ' ')
                    break
            verdict[key] = (found, sample)
            if found:
                print(f'  session {sid or key[:12]} [{row.get("trigger_type")}]: '
                      f'human turn: "{sample}"')
        if verdict[key][0]:
            row['human_joined'] = True
            changed += 1
            counts['stamp'] += 1
        else:
            counts['no-human-turn'] += 1

    if changed and apply:
        write_json_atomic(log_file, log, indent=2, ensure_ascii=False)
        print(f'  wrote {log_file.name} ({changed} row(s) stamped)')
    elif changed:
        print(f'  would stamp {changed} row(s) -- DRY RUN, nothing written (pass --apply)')
    else:
        print('  nothing to stamp')
    return counts


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--project', action='append', dest='projects',
                    help='project id (repeatable). Default: every project with a log.')
    ap.add_argument('--session', default='',
                    help='limit to one MC session_id (e.g. 88001c6d8849).')
    ap.add_argument('--apply', action='store_true',
                    help='actually write. Without this the run is a dry run.')
    args = ap.parse_args()

    projects_dir = data_dir()
    if not projects_dir.is_dir():
        print(f'no such data dir: {projects_dir}')
        return 1
    ids = args.projects or sorted(
        f.name[:-len('_agent_log.json')] for f in projects_dir.glob('*_agent_log.json'))
    print(f'{"APPLY" if args.apply else "DRY RUN"} over {projects_dir}')
    total: Counter = Counter()
    for pid in ids:
        print(f'\n{pid}:')
        c = run(projects_dir, pid, args.session, args.apply)
        total.update(c)
        print(f'  {dict(c)}')
    print(f'\ntotals: {dict(total)}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
