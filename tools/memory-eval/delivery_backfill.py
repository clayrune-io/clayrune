"""Seed the delivery counters from transcript history, then report on them.

DAVE_DESIGN §9 phase 4. The counters (`mc/memory_delivery.py`) accrue on live
traffic, which means the residency decision they exist to inform would be weeks
away and would be designed against an empty file. It does not have to be: the
read floor is deterministic — no model, ranked grep — so replaying it over the
tasks we already dispatched reproduces exactly what those sessions were served.
That is the same method that validated the BM25 rewrite over 206 real sessions.

WHAT IT MEASURES, AND WHAT IT DOES NOT. Delivery is REACH, not use: a note
landing in a prompt is not proof the agent read it. The strong signal is the
negative one — never reached across hundreds of real tasks means genuinely
unreachable, and that is the case that matters. It is exactly the number
MC-892's eviction lacked when 29-30 of its 67 proposed cuts turned out to have
no delivery channel at all.

Read-only against the vault. It writes ONE file, the delivery sidecar, under a
distinct `backfill` context so live traffic stays separable — and `--dry-run`
writes nothing at all.

NEVER import `server` here (the tunnel reaper) — see `_harness.py`.
"""
import argparse
import glob
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _harness  # noqa: E402

# Windows consoles default to cp1252 and a box-drawing character is enough
# to kill the run AFTER the sidecar has been written - a crash that looks
# like the measurement failed when it actually succeeded.
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

ROOT = os.path.join(os.path.expanduser("~"), ".claude", "projects")
# Below this a "task" is an acknowledgement, not a dispatch. An earlier probe
# reported 29% of tasks surfacing nothing; those were sessions whose first user
# message was the literal string "ok".
MIN_TASK_CHARS = 25


def first_user_task(path):
    """The dispatched task — the first real user message of a session."""
    try:
        fh = open(path, "r", encoding="utf-8", errors="replace")
    except Exception:
        return None
    with fh:
        for line in fh:
            if '"user"' not in line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            msg = rec.get("message") or {}
            if msg.get("role") != "user":
                continue
            c = msg.get("content")
            if isinstance(c, str):
                text = c
            elif isinstance(c, list):
                text = " ".join(b.get("text", "") for b in c
                                if isinstance(b, dict) and b.get("type") == "text")
            else:
                continue
            text = text.strip()
            if not text or text.startswith("<"):
                continue
            return text[:2000]
    return None


def collect_tasks():
    dirs = [x for x in os.listdir(ROOT)
            if "mission-control" in x and "scratch" not in x and "demo" not in x]
    files = []
    for x in dirs:
        files += glob.glob(os.path.join(ROOT, x, "*.jsonl"))
    tasks, trivial = [], 0
    for f in files:
        t = first_user_task(f)
        if not t:
            continue
        if len(t) < MIN_TASK_CHARS:
            trivial += 1
            continue
        tasks.append(t)
    return files, tasks, trivial


def pct(n, d):
    return 100.0 * n / max(1, d)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="report only; write nothing to the sidecar")
    ap.add_argument("--reset", action="store_true",
                    help="drop the existing sidecar first (a re-run otherwise "
                         "double-counts every task it already replayed)")
    ap.add_argument("--topk", type=int, default=None)
    ap.add_argument("--limit", type=int, default=0,
                    help="replay only the first N tasks (for a quick look)")
    args = ap.parse_args()

    m, project = _harness.wire()
    from mc import memory_delivery as deliv
    topk, expand = _harness.live_signature()
    if args.topk is not None:
        topk = args.topk

    corpus = m.corpus_uids(project)
    by_cls = Counter(v[1] for v in corpus.values())
    files, tasks, trivial = collect_tasks()
    if args.limit:
        tasks = tasks[:args.limit]

    print("sessions: %d | real dispatched tasks: %d | trivial excluded: %d"
          % (len(files), len(tasks), trivial))
    print("corpus: %d units (%s)" % (
        len(corpus), ", ".join(f"{k} {v}" for k, v in sorted(by_cls.items()))))
    print("signature: topk=%d expand=%d" % (topk, expand))

    if args.reset and not args.dry_run:
        path = deliv._stats_path(project)
        if path.exists():
            path.unlink()
            print("sidecar reset: %s" % path.name)

    # `record=` is deliberately not used: the backfill must be attributable, and
    # a replayed task is not a live delivery. Same counters, its own context.
    context = "backfill"
    counts, starved = Counter(), 0
    for t in tasks:
        hits = m._memory_search(project, t, topk, expand=expand)
        if not hits:
            starved += 1
        if not args.dry_run:
            # Re-run WITH recording. The search is pure and cached, so this is
            # cheap; doing it in one pass would mean reaching inside the search
            # for the uid-bearing hits, which the public shape strips.
            m._memory_search(project, t, topk, expand=expand,
                             record=context)
        for h in hits:
            counts[h["file"]] += 1

    print("\ntasks that surfaced NOTHING: %d (%.1f%%)"
          % (starved, pct(starved, len(tasks))))

    if args.dry_run:
        print("\n(dry run - sidecar untouched; per-unit detail needs a real run)")
        return

    s = deliv.summary(project, corpus)
    print("\n== DELIVERY over %d replayed tasks ==" % s["tasks"])
    print("units ever delivered : %d / %d (%.1f%%)"
          % (s["n_delivered"], len(corpus), pct(s["n_delivered"], len(corpus))))
    print("units NEVER delivered: %d (%.1f%%)"
          % (s["n_never"], pct(s["n_never"], len(corpus))))

    never_cls = Counter(r["cls"] for r in s["never"])
    print("  never, by class: %s"
          % ", ".join(f"{k} {v}/{by_cls[k]}" for k, v in sorted(never_cls.items())))

    via_only = [r for r in s["delivered"] if r.get("via") and r["via"] == r["n"]]
    print("reached ONLY by [[wikilink]] hop: %d" % len(via_only))

    print("\nTOP 15 delivered - the promotion end")
    for r in s["delivered"][:15]:
        print("  %5d  %-6s %s" % (r["n"], r.get("cls") or "?",
                                  (r.get("file") or "")[:60]))

    dark_topics = [r for r in s["never"] if r["cls"] == "topic"]
    print("\nTOPIC NOTES NEVER REACHED: %d - the demotion end" % len(dark_topics))
    for r in dark_topics[:20]:
        print("  %s" % (r.get("file") or ""))

    print("\nWritten to %s (context=%s)."
          % (deliv._stats_path(project).name, context))
    print("Reports only. Nothing was promoted, demoted, or deleted.")


if __name__ == "__main__":
    main()
    _harness.cleanup()
