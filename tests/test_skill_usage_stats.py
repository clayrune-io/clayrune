"""skills.skill_usage_stats() (MC-941).

Walks ~/.claude/projects/*/*.jsonl for assistant tool_use blocks naming the
Skill tool. Before this fix it globbed only the top level of each project
dir, so a Skill invocation made by a Task-tool subagent — which lives ONLY in
its own nested `<parent_csid>/subagents/agent-<id>.jsonl`, never inlined into
the parent's own transcript — silently didn't count. Same gap MC-939 found
for the Documents tab (tests/test_documents_tab.py), same fix
(agent_runtime.iter_transcript_files_in_dir), different caller.
"""
import json
from pathlib import Path

from mc import skills


def _skill_call_line(skill_name, ts='2026-09-01T00:00:00Z'):
    return json.dumps({
        'type': 'assistant',
        'timestamp': ts,
        'message': {
            'role': 'assistant',
            'content': [{'type': 'tool_use', 'name': 'Skill', 'id': 't1',
                        'input': {'skill': skill_name}}],
        },
    })


def test_counts_a_skill_call_in_a_top_level_transcript(tmp_path, monkeypatch):
    root = tmp_path / 'claude_projects'
    root.mkdir()
    monkeypatch.setattr(skills, '_claude_projects_root', lambda: root)

    proj_dir = root / '-Users-me-proj'
    proj_dir.mkdir()
    (proj_dir / 'csid1.jsonl').write_text(_skill_call_line('mc-project-status') + '\n',
                                          encoding='utf-8')

    stats = skills.skill_usage_stats(days=30)
    assert stats['mc-project-status']['invocations'] == 1
    assert stats['mc-project-status']['project_count'] == 1


def test_counts_a_skill_call_made_only_by_a_dispatched_subagent(tmp_path, monkeypatch):
    """The real gap: the parent transcript never sees the subagent's Skill
    call at all — only the nested file does."""
    root = tmp_path / 'claude_projects'
    root.mkdir()
    monkeypatch.setattr(skills, '_claude_projects_root', lambda: root)

    proj_dir = root / '-Users-me-proj'
    proj_dir.mkdir()
    # Parent transcript: no Skill call, just an Agent dispatch.
    (proj_dir / 'parent-csid.jsonl').write_text(json.dumps({
        'type': 'assistant', 'timestamp': '2026-09-01T00:00:00Z',
        'message': {'role': 'assistant',
                    'content': [{'type': 'tool_use', 'name': 'Agent', 'id': 'a1',
                                'input': {'subagent_type': 'prd-writer'}}]},
    }) + '\n', encoding='utf-8')

    sub_dir = proj_dir / 'parent-csid' / 'subagents'
    sub_dir.mkdir(parents=True)
    (sub_dir / 'agent-abc123.jsonl').write_text(
        _skill_call_line('mc-changelog-update', ts='2026-09-01T00:05:00Z') + '\n',
        encoding='utf-8')

    stats = skills.skill_usage_stats(days=30)
    assert stats.get('mc-changelog-update', {}).get('invocations') == 1
    # Attribution stays on the top-level project dir the nested file was found under.
    assert stats['mc-changelog-update']['project_count'] == 1


def test_days_cutoff_still_applies_to_nested_transcripts(tmp_path, monkeypatch):
    import os
    root = tmp_path / 'claude_projects'
    root.mkdir()
    monkeypatch.setattr(skills, '_claude_projects_root', lambda: root)

    proj_dir = root / '-Users-me-proj'
    sub_dir = proj_dir / 'parent-csid' / 'subagents'
    sub_dir.mkdir(parents=True)
    f = sub_dir / 'agent-old.jsonl'
    f.write_text(_skill_call_line('stale-skill') + '\n', encoding='utf-8')
    old_ts = 1_000_000  # 1970 — long past any cutoff
    os.utime(f, (old_ts, old_ts))

    stats = skills.skill_usage_stats(days=30)
    assert 'stale-skill' not in stats
