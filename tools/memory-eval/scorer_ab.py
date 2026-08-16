"""A/B the memory read-floor scorer: old TF-only vs new BM25. (2026-08-05)

Replays both scorers over the same set of real dispatched tasks (the first user
message of each past session) and reports corpus reachability.

Two corrections over the first pass:
  * Trivial follow-ups are excluded. An earlier run reported "29% of tasks
    surface nothing" — those turned out to be sessions whose first user message
    was the literal string "ok". They are not dispatched tasks and were
    inflating the failure rate for both scorers equally.
  * The old scorer is reimplemented here verbatim rather than recalled, so the
    comparison is measured, not asserted.

CAVEAT both columns share: the corpus is replayed at its CURRENT state, so
recently-written notes could not have been surfaced at the time. That biases
both scorers identically, so the COMPARISON is sound even though each absolute
number is an upper bound.

Read-only.
"""
import os
import sys
import glob
import json
import argparse
import collections

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _harness

ROOT = os.path.join(os.path.expanduser("~"), ".claude", "projects")
MIN_TASK_CHARS = 25


def first_user_task(path):
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


def old_search(m, project, query, topk=3):
    """The pre-2026-08-05 scorer, verbatim: raw term frequency, no length
    normalization, flat +2 filename bonus."""
    import re
    terms = [t for t in re.findall(r"[a-z0-9_]+", (query or "").lower())
             if len(t) >= 3]
    if not terms:
        return []
    mem_path = m._get_memory_path(project)
    mem_dir = mem_path.parent
    if not mem_dir.is_dir():
        return []
    mem_name = mem_path.name
    arch_name = m._get_archive_path(project).name
    units = []
    for f in sorted(mem_dir.glob("*.md")):
        try:
            txt = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if f.name == mem_name:
            for e in m._mem_split(txt)[1]:
                units.append((f"{f.name}#managed", e))
        elif f.name == arch_name:
            for ln in txt.splitlines():
                if ln.strip().startswith("- ["):
                    units.append((f.name, ln.strip()))
        else:
            units.append((f.name, txt))
    scored = []
    for label, text in units:
        low = text.lower()
        score = sum(low.count(t) for t in terms)
        if score <= 0:
            continue
        if any(t in label.lower() for t in terms):
            score += 2
        scored.append({"file": label, "score": score})
    scored.sort(key=lambda r: r["score"], reverse=True)
    return scored[:max(1, topk)]


def report(name, results, on_disk, n_tasks):
    surfaced = collections.Counter()
    empty = 0
    for hits in results:
        if not hits:
            empty += 1
            continue
        for h in hits:
            surfaced[h["file"].split("#")[0].lower()] += 1
    touched = {k for k in surfaced if k in on_disk}
    slots = sum(surfaced.values())
    top3 = surfaced.most_common(3)
    conc = sum(v for _, v in top3) / max(1, slots)
    print("  %-9s reachable %2d/%d topic files | dark %2d | zero-result %d/%d "
          "(%.0f%%) | top-3 units hold %.0f%% of slots"
          % (name, len(touched), len(on_disk), len(on_disk) - len(touched),
             empty, n_tasks, 100.0 * empty / max(1, n_tasks), 100.0 * conc))
    return touched, surfaced


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus-snapshot", default=None,
                    help="read a frozen corpus (tools/memory-snapshot.py) so both "
                         "arms of a paired run see identical notes")
    ap.add_argument("--topk", type=int, default=None,
                    help="override the live read_floor_topk")
    args = ap.parse_args()

    # NEVER import `server` here. It autostarts the tunnel supervisor, whose
    # orphan reaper kills the operator's cloudflared — verified end to end.
    # See tools/memory-eval/_harness.py.
    m, project = _harness.wire(corpus_snapshot=args.corpus_snapshot)
    topk, expand = _harness.live_signature()
    if args.topk is not None:
        topk = args.topk
    mem_dir = m._get_memory_path(project).parent
    on_disk = {x.lower() for x in os.listdir(mem_dir) if x.endswith(".md")}

    dirs = [x for x in os.listdir(ROOT)
            if "mission-control" in x and "scratch" not in x and "demo" not in x]
    files = []
    for x in dirs:
        files += glob.glob(os.path.join(ROOT, x, "*.jsonl"))

    tasks = []
    trivial = 0
    for f in files:
        t = first_user_task(f)
        if not t:
            continue
        if len(t) < MIN_TASK_CHARS:
            trivial += 1
            continue
        tasks.append(t)

    print("sessions: %d | real dispatched tasks: %d | trivial follow-ups "
          "excluded: %d" % (len(files), len(tasks), trivial))
    print("topic files on disk: %d" % len(on_disk))
    # Production passes BOTH of these per context build. Omitting `expand=` and
    # hardcoding topk=3 is what made an earlier pass report 27-30 dark files
    # against a live figure of 15 — it was measuring a system nobody runs.
    print("signature: topk=%d expand=%d%s" % (
        topk, expand, "  (frozen corpus)" if args.corpus_snapshot else ""))
    print()

    old = [old_search(m, project, t, topk) for t in tasks]
    new = [m._memory_search(project, t, topk, expand=expand) for t in tasks]

    t_old, s_old = report("OLD (tf)", old, on_disk, len(tasks))
    t_new, s_new = report("NEW BM25", new, on_disk, len(tasks))

    print()
    gained = sorted(t_new - t_old)
    lost = sorted(t_old - t_new)
    print("NEWLY REACHABLE (%d):" % len(gained))
    for x in gained:
        print("   +  %s" % x)
    if lost:
        print("REGRESSED — reachable before, dark now (%d):" % len(lost))
        for x in lost:
            print("   -  %s" % x)
    else:
        print("REGRESSED: none")

    still = sorted(on_disk - t_new)
    print()
    print("STILL DARK (%d):" % len(still))
    for x in still:
        print("   .  %s" % x)


if __name__ == "__main__":
    main()
