#!/usr/bin/env python3
"""Get the demo instance camera-ready. Run this immediately before each take.

    python tools/demo-shoot/prep.py

Brings up an ISOLATED Clayrune (its own port, its own MC_DATA_DIR) with fake
projects, then starts six real Claude agents — one hired character per room —
so the grid is genuinely live, with distinct faces, when you hit record.
Nothing here can see or touch your real projects.

Why a script and not a checklist: the agents finish in a couple of minutes, so
they have to be started fresh for every take. Doing that by hand between takes is
how a shoot dies.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
PORT = 5200
# Every demo dispatch is forced onto this, overriding any character pin.
DEMO_MODEL = "claude-sonnet-5"
BASE = f"http://localhost:{PORT}"
DATA_DIR = ROOT / "_scratch" / "demo-inst2"
REPOS = ROOT / "_scratch" / "demo2"

PROJECTS = [
    ("orchard", "Orchard", "Marketing site"),
    ("pathfinder", "Pathfinder", "API gateway"),
    ("lantern", "Lantern", "Mobile companion app"),
    ("almanac", "Almanac", "Weekly data digest"),
    ("signalworks", "Signalworks", "Internal ops dashboard"),
    ("brightleaf", "Brightleaf", "Growth analytics widget"),
]

# One hired character per room, one room per character — this is the whole fix
# for the "same two faces on every card" shot. A character dispatch carries its
# own name AND avatar (see `_figure_name`/`_figure_avatar` in floor_routes.py),
# so there is no separate figure-naming step here the way the old default-agent
# dispatch needed; naming it is what made every card read as the SAME face
# ("Vector") no matter which project it sat in.
#
# Six distinct avatars, one per room, none reused — Ron's ask, verbatim: avatar
# reads at LinkedIn thumbnail size even when the name doesn't.
#
# The lantern task is deliberately under-specified: builder's own character
# ("the brief is a hypothesis... if the diagnosis is wrong, say so and stop")
# is instructed to surface that as a real `mc:question` block instead of
# guessing, so one card sits in NEEDS-YOU next to the others' WORKING — the
# contrast Ron wants in the shot.
#
# Everything else needs to still be STREAMING two minutes in, on a repo of one
# or two tiny files — so the lever isn't "discuss at length" (tried, and the
# first take's agents still wrapped up inside 30 seconds: a short file makes a
# short conclusion easy to reach no matter how the prompt is worded). The
# lever that actually works is a hard, checkable word-count floor per section,
# repeated in every non-lantern task below, plus a re-read instruction so each
# session makes at least two tool calls instead of one.
_LENGTH_FLOOR = (
    " Word-count floor, not a suggestion: at least 150 words per numbered "
    "section, full prose, no bullet-point shorthand. Read the file once, form "
    "a view, then re-read it a second time checking your view against every "
    "line before you write a word. If you notice yourself concluding early, "
    "go back and deepen an earlier section instead of stopping — a 200-word "
    "answer to this is a failed answer regardless of how correct it is."
)
CHARACTER_TASKS = [
    ("orchard", "global:ui-fixer",
     "Review src/styles.css for responsive problems at narrow widths. Narrate a "
     "thorough audit in exactly eight numbered sections, one per breakpoint: "
     "320px, 375px, 414px, 480px, 600px, 768px, 1024px, 1280px. For each, "
     "state what breaks, why, and the exact fix." + _LENGTH_FLOOR +
     " Do not edit any files."),
    ("pathfinder", "global:code-reviewer",
     "Review src/routes.js for correctness bugs and error-handling gaps. For "
     "every handler, evaluate it in turn against each of these eight lenses, "
     "one numbered section per lens: correctness, error handling, input "
     "validation, security, performance, concurrency/race conditions, "
     "testability, and naming/readability." + _LENGTH_FLOOR +
     " Do not edit any files."),
    ("lantern", "global:builder",
     "Brief: \"Add an offline cache to the feed.\" This brief does not say what "
     "should happen when the cache and the network disagree, which your own rules "
     "say to treat as a reason to stop and ask rather than guess. Before reading "
     "or touching anything, ask the user which policy to use — stale-while-revalidate "
     "or cache-invalidates-on-reconnect — using exactly this block, verbatim, as "
     "your entire response, then stop:\n\n"
     "```mc:question\n"
     "{\"questions\": [{\"header\": \"Offline cache policy\", \"question\": "
     "\"When the cache and the network disagree, which should win?\", \"options\": "
     "[{\"label\": \"Stale-while-revalidate\", \"description\": \"Show the cached "
     "feed immediately, refresh in the background\"}, {\"label\": "
     "\"Invalidate on reconnect\", \"description\": \"Hold the cached feed until "
     "reconnect, then force a full refresh\"}], \"multiSelect\": false}]}\n"
     "```"),
    ("almanac", "global:prd-writer",
     "Write a spec for adding week-over-week sparklines to the digest. Read "
     "src/digest.py in full first, then produce the spec as exactly ten numbered "
     "sections: problem, background, users & personas, goals, non-goals, "
     "functional requirements, non-functional requirements, data model, edge "
     "cases, and a rollout plan." + _LENGTH_FLOOR),
    ("signalworks", "global:security-privacy-auditor",
     "Audit src/config.py and src/auth.py for what this dashboard exposes. "
     "Report exactly six numbered findings, worst first. For each finding cover, "
     "as its own labeled paragraph: what is wrong, a step-by-step walkthrough of "
     "how it actually gets exploited, the blast radius if it is, the fix, and a "
     "test you would add to catch a regression." + _LENGTH_FLOOR +
     " Do not edit any files."),
    ("brightleaf", "global:market-researcher",
     "Read src/pricing.md in full — do not search the web, work from this file "
     "only. Write a competitive positioning memo covering each of the three "
     "competitor moves listed, one numbered section per competitor: how their "
     "move pressures each of our three tiers specifically, which of our tiers is "
     "most exposed and why, and a concrete numeric pricing response. Close with "
     "a final numbered section ranking the three responses by urgency." +
     _LENGTH_FLOOR),
]

# Config for the isolated instance. Written before the FIRST start only — after
# that the file is the instance's own state and we leave it alone.
#
# Everything here that is switched off is switched off because it would
# otherwise reach outside the demo: the scheduler would fire this box's real
# routines, the distiller and scribe would write learning artifacts and memory
# from throwaway repos, and the question channel would email a real person from
# a fake project.
DEMO_CONFIG = {
    "port": PORT,
    "agent_model": "claude-opus-5",
    "agent_effort": "high",
    "agent_name": "Vector",
    "user_name": "",
    "use_streaming_agent": True,
    "activity_states_enabled": True,
    "scheduler_paused": True,
    "distiller_enabled_global": False,
    "distiller_cross_project_enabled": False,
    "scribe_enabled": False,
    "scribe_reconcile_enabled": False,
    "scribe_checkpoint_enabled": False,
    "condense_enabled": False,
    "exploration_readback_enabled": False,
    "coordination_enabled": False,
    "question_channel": "off",
    "keep_awake_enabled": False,
    "reply_summarize_enabled": False,
}


def post(path: str, payload: dict, timeout: int = 30):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode() or "{}")


def up() -> bool:
    try:
        urllib.request.urlopen(f"{BASE}/api/system/heartbeat", timeout=3)
        return True
    except Exception:
        return False


def lan_ip() -> str:
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -like "
             "'192.168.*' -or $_.IPAddress -like '10.*' } | Select-Object -First 1 "
             "-ExpandProperty IPAddress)"],
            capture_output=True, text=True, timeout=10)
        return (out.stdout or "").strip() or "<your-LAN-IP>"
    except Exception:
        return "<your-LAN-IP>"


def write_config() -> None:
    """Seed the isolated instance's config.json, once.

    Skipped if it already exists: after the first start the file belongs to the
    instance, and clobbering it would undo anything changed in its own Settings.
    """
    cfg_path = DATA_DIR / "config.json"
    if cfg_path.exists():
        return
    cfg = dict(DEMO_CONFIG)
    cfg["shared_rules_path"] = str(DATA_DIR / "data" / "SHARED_RULES.md")
    cfg["projects_base"] = str(REPOS)
    cfg["auto_workspace_base"] = str(REPOS)
    (DATA_DIR / "data").mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    rules = DATA_DIR / "data" / "SHARED_RULES.md"
    if not rules.exists():
        # Deliberately bland. The real SHARED_RULES.md is one operator's personal
        # working agreement; loading it here would put their rules in a public
        # screenshot and their habits in a throwaway agent.
        rules.write_text(
            "Explain your work as you go. Describe what you read, state your plan,\n"
            "then make the change. Prefer small, reviewable edits.\n",
            encoding="utf-8")
    print("  wrote isolated config (scheduler/distiller/scribe off)")


def check_repos() -> None:
    """Fail loudly if the throwaway repos are missing.

    `_scratch/` is gitignored, so a fresh clone — or a cleaned working tree —
    has no repos here. Without this check `reset_repos` silently skips them and
    the agents get dispatched into directories that do not exist, which surfaces
    as figures that die a few seconds after they appear. Better to stop.
    """
    missing = [pid for pid, _, _ in PROJECTS if not (REPOS / pid).is_dir()]
    if missing:
        sys.exit(
            f"  FAILED: throwaway repos missing under {REPOS}: {', '.join(missing)}\n"
            "  They are gitignored, so they do not survive a fresh clone. Recreate\n"
            "  them (a src/ file or two each is enough) and `git init` + commit a\n"
            "  baseline in each, so reset_repos has something to roll back to.")


def start_instance() -> None:
    if up():
        print(f"  demo instance already up on :{PORT}")
        return
    print(f"  starting demo instance on :{PORT} …")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    write_config()
    # MC_REMOTE_ENABLED=0 is NOT optional. The tunnel token is not stored in
    # MC_DATA_DIR, so a second instance picks up the SAME token and registers a
    # second connector on the operator's live hostname — Cloudflare then load
    # balances the public URL across the real instance and this fake one, and
    # visitors land on whichever answers. Worse, the connector PID ledger at
    # ~/.clayrune/cloudflared_pids.json is shared, so this instance's startup
    # reap can kill the real instance's connector. Measured 2026-09-01.
    env = dict(os.environ, MC_PORT=str(PORT), MC_DATA_DIR=str(DATA_DIR),
               MC_REMOTE_ENABLED="0")
    log = open(DATA_DIR / "server.log", "ab")
    subprocess.Popen([sys.executable, str(ROOT / "server.py")],
                     cwd=str(ROOT), env=env, stdout=log, stderr=log)
    for _ in range(30):
        time.sleep(1)
        if up():
            print("  up.")
            return
    sys.exit("  FAILED: demo instance did not come up. Check _scratch/demo-inst2/server.log")


def ensure_projects() -> None:
    for pid, name, summary in PROJECTS:
        try:
            post(f"/api/project/{pid}", {
                "id": pid, "name": name, "summary": summary,
                "project_path": str(REPOS / pid), "status": "active",
            })
        except urllib.error.URLError as e:
            print(f"  ! could not create {pid}: {e}")
    # `_incognito` is the pseudo-project backing incognito mode. It is re-seeded
    # on every boot and shows up by name in the Floor's quiet list, where it
    # reads to an outsider as a real project someone is hiding. Drop it — the
    # delete removes MC's record only, and the mode re-creates it if used.
    try:
        req = urllib.request.Request(BASE + "/api/project/_incognito", method="DELETE")
        urllib.request.urlopen(req, timeout=10).read()
    except Exception:
        pass


def reset_repos() -> None:
    """Roll the throwaway repos back to their committed baseline.

    Without this the agent finds its own leftovers from the last take and says so
    on camera ("this looks like it's already been done") — which is exactly the
    kind of thing that reads as fake.
    """
    for pid, _, _ in PROJECTS:
        d = REPOS / pid
        if not (d / ".git").is_dir():
            continue
        subprocess.run(["git", "checkout", "--", "."], cwd=str(d), capture_output=True)
        subprocess.run(["git", "clean", "-fdq"], cwd=str(d), capture_output=True)
    print("  repos reset to baseline")


def dispatch_characters() -> None:
    for pid, character, task in CHARACTER_TASKS:
        try:
            # Pin the model explicitly. A character's own `model:` front-matter
            # otherwise wins, and prd-writer pins claude-fable-5 — which is how a
            # demo seeded 680 Fable calls across 68 sessions on 2026-09-02/03
            # before anyone noticed (the demo instance re-dispatched on a loop).
            # The demo is a screenshot prop; it must never pick an expensive model.
            r = post(f"/api/project/{pid}/agent/dispatch",
                     {"task": task, "character": character,
                      "model": DEMO_MODEL})
            name = character.split(":", 1)[-1]
            print(f"  dispatched {name:<16} -> {pid:<11} "
                  f"{'ok' if r.get('ok') else 'FAILED'}")
        except Exception as e:
            print(f"  ! dispatch {character} failed: {e}")


def main() -> None:
    print("Clayrune — demo shoot prep\n")
    check_repos()
    start_instance()
    ensure_projects()
    reset_repos()
    print("\n  dispatching one hired character per room …")
    dispatch_characters()

    print("\n  waiting for them to come up as IN PROGRESS …")
    for _ in range(20):
        time.sleep(2)
        try:
            with urllib.request.urlopen(f"{BASE}/api/projects", timeout=5) as r:
                ps = json.loads(r.read().decode())
            live = [p["name"] for p in ps if p.get("live_agent")]
            if len(live) >= 6:
                print(f"  LIVE: {', '.join(live)}")
                break
        except Exception:
            pass
    else:
        print("  (some agents may still be spinning up — check the grid)")

    # ASCII only: the Windows console is cp1252 and box-drawing characters raise
    # UnicodeEncodeError, which would kill the script at the last line.
    ip = lan_ip()
    print(f"""
{'-' * 62}
  READY. Roll camera within ~2 minutes - the agents finish.

  Desktop :  http://localhost:{PORT}
  PHONE   :  http://{ip}:{PORT}      <- same WiFi. NOT the tunnel.

  The tunnel points at your REAL instance (:5199) and would put
  your real project names on camera. Always use the IP above.
{'-' * 62}
""")


if __name__ == "__main__":
    main()
