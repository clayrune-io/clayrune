"""Sanity checks for the gh test harness itself.

Proves the fake_gh / gs fixtures wire up correctly so the Sprint 2 P0
regression tests (IMPROVEMENT_PLAN_V2.md P0-1..P0-7) have a trusted
foundation. These assert CURRENT behavior; they are not the P0 fix tests.
"""
import json


def test_fake_gh_records_and_dispatches(gs, fake_gh, project_store):
    fake_gh.on(
        ["issue", "list"],
        stdout=json.dumps([
            {"number": 1, "title": "Hello", "state": "OPEN",
             "labels": [], "author": {"login": "octocat"}, "updatedAt": "x"},
        ]),
    )
    project_store["p1"] = {
        "id": "p1", "github_repo": "o/r",
        "github_sync_enabled": True, "backlog": [],
    }

    ok, summary = gs.sync_project("p1")

    assert ok, summary
    assert "1 new" in summary
    assert fake_gh.count(["issue", "list"]) == 1
    # The new issue was merged into the in-memory project.
    backlog = project_store["p1"]["backlog"]
    assert len(backlog) == 1
    assert backlog[0]["github_issue_number"] == 1
    assert backlog[0]["text"] == "Hello"


def test_a_missing_issue_is_unlinked_even_in_the_same_timestamp(gs, fake_gh, monkeypatch):
    stamp = '2026-11-18T00:00:00Z'
    monkeypatch.setattr(gs, '_now_iso', lambda: stamp)
    fake_gh.on(['issue', 'list'], stdout='[]')
    item = {'github_issue_number': 42, 'github_synced_at': stamp, 'text': 'Keep the local task'}
    project = {'id': 'p', 'backlog': [item]}

    gs._pull_issues(project, 'o/r')

    assert item['github_issue_number'] is None
    assert item['github_deleted'] is True
    assert item['text'] == 'Keep the local task'


def test_sanitize_strips_html_and_control(gs):
    out = gs.sanitize("<script>x</script>hi\x07 there")
    assert "<script>" not in out
    assert "\x07" not in out
    assert "hi" in out and "there" in out


def test_rate_limit_blocks_second_immediate_sync(gs, fake_gh, project_store):
    fake_gh.on(["issue", "list"], stdout=json.dumps([]))
    project_store["p"] = {
        "id": "p", "github_repo": "o/r",
        "github_sync_enabled": True, "backlog": [],
    }
    ok1, _ = gs.sync_project("p")
    ok2, msg2 = gs.sync_project("p")
    assert ok1 is True
    assert ok2 is False and "Rate limited" in msg2
