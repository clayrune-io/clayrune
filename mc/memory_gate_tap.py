"""G1 report-mode tap for `mc.memory.write_topic_note` (MC-964 step 10).

`mc/memory_note_gate.py` (G1) had zero write traffic: its only entry point is
the HTTP route nothing calls, so the step-9 rejection rate was undefined. Every
automated mint (the habit classifier and `mint_topic_node` both end in
`write_topic_note`) now passes through here so each one leaves a G1 JSONL row.

This is an OBSERVER, not a gate. It never refuses, never alters content and
never reads `memory_gate_mode` (the row is stamped mode='report' because that
is the only posture this tap has). The note lands byte-for-byte as it would
without it. Both calls are fail-open: any exception is `_log`'d and swallowed,
so a gate fault can never block a mint (Condition 4/22, "mint WRITEs fail-open").

Two phases because the question G1 answers is "what would refuse this write",
which has to be asked against the vault BEFORE the note exists:

    pending = begin(project_id, mem_dir, slug, text, origin)   # before write
    ...write lands...
    finish(pending)                                            # after write

`begin` returns None on failure; `finish(None)` is a no-op, and a write that
raises never reaches `finish`, so no row claims a write that did not land.
"""

from mc.core import _log

OP = 'mint'
MODE = 'report'


def begin(project_id, mem_dir, slug, text, origin):
    """Evaluate `text` against the vault as it stands. -> pending dict, or None
    (gate fault, already logged). Never raises, never writes."""
    try:
        from mc import memory_note_gate as gate
        ev = gate.evaluate(mem_dir, slug=slug, content=text)
        violations = list(ev['violations'])
        dup = gate.identity_check(mem_dir, slug)[1]
        if dup:
            violations.append(dup)
        return {'project_id': project_id, 'slug': slug, 'origin': origin,
                'violations': violations, 'repairs': ev['repairs']}
    except Exception as e:
        _log(f'[memory-gate] mint evaluate failed for {slug}: {e}')
        return None


def finish(pending):
    """Record the G1 row for a write that landed. Never raises."""
    if not pending:
        return
    try:
        from mc import memory_note_gate as gate
        gate.record(pending['project_id'], op=OP, slug=pending['slug'],
                    mode=MODE, origin=pending['origin'], result='written',
                    violations=pending['violations'],
                    repairs=pending['repairs'])
    except Exception as e:
        _log(f'[memory-gate] mint record failed for {pending.get("slug")}: {e}')
