"""The read floor must be injected on EVERY path that builds an agent context.

WHY THIS TEST EXISTS (S6, 2026-08-16). `_build_agent_context(project, task='')`
gates the memory read floor on `if task:`. Thirteen recovery and respawn call
sites passed no task at all, so those sessions were built with no memory context
whatsoever — and they are precisely the sessions that just lost their context to
a crash, a failed resume, or an oversized transcript.

Measured before the fix: 1,373 of 1,638 user turns (84%) were served a context
with no read floor. That is not a subtle degradation. The BM25 ranker, the link
expansion and the topk tuning were all running at a fraction of their reach,
because most of the time the ranker was never called.

It matters more than it looks because of `_respawn_sysprompt_args`: it PREFERS
the context stashed on the session at spawn and only rebuilds when the stash is
missing. That is deliberate — byte-identical content keeps the resumed prefix
prompt-cache friendly. But it means a task-less context stashed once is replayed
for the rest of that session's life. Site 4981 (the sticky-settings respawn) did
exactly that, permanently pinning an empty floor onto the session.

This test is a source-level guard rather than a behavioural one on purpose: the
defect is "an argument was not passed", which is invisible at runtime — the
context builds fine, it is just emptier than it should be. Nothing fails, nothing
logs. A grep is the honest instrument for that.
"""

import re
from pathlib import Path

import pytest

ROUTES = Path(__file__).resolve().parent.parent / 'mc' / 'blueprints' / 'agent_routes.py'

# `_build_agent_context(p)` with no further arguments — the exact shape of the
# defect. Sites that pass task=/incognito=/character_body= are fine.
BARE_CALL = re.compile(r'_build_agent_context\(\s*p\s*\)')


def _source():
    return ROUTES.read_text(encoding='utf-8')


def test_no_context_is_built_without_a_task():
    """Zero call sites may build an agent context with no task.

    Run against the pre-fix tree this reports 13. If it ever reports more than 0
    again, a new recovery path was added that silently ships agents with no
    memory — pass the in-scope task/message, do not delete the assertion.
    """
    offenders = []
    for n, line in enumerate(_source().split('\n'), start=1):
        if BARE_CALL.search(line):
            offenders.append(f'  agent_routes.py:{n}: {line.strip()}')
    assert not offenders, (
        f'{len(offenders)} call site(s) build an agent context with no task, so the '
        f'memory read floor is skipped entirely:\n' + '\n'.join(offenders))


def test_read_floor_is_gated_on_task_and_logs_failures():
    """The gate itself, and that its failure is observable.

    The search sits behind `if task:` — that is fine and is why passing the task
    matters. What is not fine is swallowing a failure silently: the read floor is
    the only retrieval channel that actually runs (agents open a memory file in
    5% of sessions), so a silent failure degrades every agent with no signal.
    """
    src = _source()
    idx = src.find('def _build_agent_context(')
    assert idx != -1, '_build_agent_context moved — update this test'
    body = src[idx:idx + 12000]

    assert 'if task:' in body, 'the read floor is no longer gated on task'
    m = re.search(r'hits = _memory_search\((.|\n)*?except Exception as e:(.|\n)*?_log\(',
                  body)
    assert m, ('the read-floor search swallows its exception silently again — '
               'log it, per the exception-swallowing policy in CLAUDE.md')


@pytest.mark.parametrize('site', [
    'task=task', 'task=message',
])
def test_recovery_paths_pass_a_real_task(site):
    """The 13 repaired sites pass a variable carrying the user's request.

    Guards against a future 'fix' that satisfies the grep with a placeholder —
    `task='recovery'` would pass the first test and retrieve nothing useful.
    """
    assert site in _source(), f'expected repaired call sites passing {site}'
