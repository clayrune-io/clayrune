#!/usr/bin/env python3
"""Get the demo instance camera-ready. Run this immediately before each take.

    python tools/demo-shoot/prep.py

Brings up an ISOLATED Clayrune (its own port, its own MC_DATA_DIR) with fake
projects, then starts three real Claude agents so the grid is genuinely live when
you hit record. Nothing here can see or touch your real projects.

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
BASE = f"http://localhost:{PORT}"
DATA_DIR = ROOT / "_scratch" / "demo-inst2"
REPOS = ROOT / "_scratch" / "demo2"

PROJECTS = [
    ("orchard", "Orchard", "Marketing site"),
    ("pathfinder", "Pathfinder", "API gateway"),
    ("lantern", "Lantern", "Mobile companion app"),
    ("almanac", "Almanac", "Weekly data digest"),
]

# Long, chatty tasks: they must still be streaming when the camera rolls.
# Real work on throwaway repos — nothing here is staged.
TAKE_TASKS = [
    ("orchard",
     "Add a responsive footer with social links. Narrate your work as you go: read "
     "the existing files and describe what you find, explain your plan step by step, "
     "then implement it in src/header.html and src/styles.css. Explain every edit."),
    ("pathfinder",
     "Add structured request-logging middleware to src/routes.js (method, path, status, "
     "duration). Do NOT ask questions — make sensible choices and state them. Read the "
     "file, describe it, discuss the trade-offs at length, then implement."),
    ("lantern",
     "Write a detailed plan for adding an offline cache to the feed. Discuss every "
     "trade-off at length. Do not edit any files."),
]

# A second wave, dispatched under hired characters. Two reasons, both about what
# the Floor looks like on camera: a character figure carries a NAME and a FACE
# (the default agent's figure reads "unnamed" with no avatar), and putting a
# second figure in a room that already has one is the only way to show that a
# room holds a crew rather than a single session.
CHARACTER_TASKS = [
    ("orchard", "global:ui-fixer",
     "Review src/styles.css for responsive problems at narrow widths. Read the file, "
     "describe at length what you find, and explain the fixes you would make. "
     "Do not edit any files."),
    ("pathfinder", "global:code-reviewer",
     "Review src/routes.js for correctness bugs and error-handling gaps. Read the file, "
     "walk through each handler in detail, and explain what you would change and why. "
     "Do not edit any files."),
    ("almanac", "global:prd-writer",
     "Write a short spec for adding week-over-week sparklines to the digest. Read "
     "src/digest.py first, describe it, then lay out requirements and open questions "
     "at length."),
]

# The default agent's figure has no name of its own, so it renders as "unnamed".
# Ron's real instance names it in config; the demo instance is built fresh every
# time, so name it here or every take shows three anonymous figures.
DEFAULT_FIGURE = {"name": "Vector", "avatar": "fig:scribe", "by": "self"}

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
    as three figures that die a few seconds after they appear. Better to stop.
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


def dispatch() -> None:
    for pid, task in TAKE_TASKS:
        try:
            r = post(f"/api/project/{pid}/agent/dispatch", {"task": task})
            sid = r.get("session_id")
            print(f"  dispatched {pid:<11} {'ok' if r.get('ok') else 'FAILED'}")
            # Name it immediately: the route only accepts a LIVE session id, so
            # there is no later moment at which this is still possible.
            if sid:
                try:
                    post(f"/api/floor/figure/{sid}/name", DEFAULT_FIGURE)
                except Exception as e:
                    print(f"  ! could not name figure for {pid}: {e}")
        except Exception as e:
            print(f"  ! dispatch {pid} failed: {e}")


def dispatch_characters() -> None:
    for pid, character, task in CHARACTER_TASKS:
        try:
            r = post(f"/api/project/{pid}/agent/dispatch",
                     {"task": task, "character": character})
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
    print("\n  starting three real agents …")
    dispatch()
    print("\n  adding hired characters (named figures, and a second one per room) …")
    dispatch_characters()

    print("\n  waiting for them to come up as IN PROGRESS …")
    for _ in range(20):
        time.sleep(2)
        try:
            with urllib.request.urlopen(f"{BASE}/api/projects", timeout=5) as r:
                ps = json.loads(r.read().decode())
            live = [p["name"] for p in ps if p.get("live_agent")]
            if len(live) >= 4:
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
