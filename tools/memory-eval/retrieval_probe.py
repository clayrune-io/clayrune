"""Retrospective memory-retrieval measurement (2026-08-05).

Answers, from transcripts we already have on disk, the question the steward's
access-counter proposal cannot: do the notes on the front page actually get
found and opened, and do memory searches lead anywhere?

Read-only. Writes nothing.
"""
import os
import glob
import json
import collections

ROOT = os.path.join(os.path.expanduser("~"), ".claude", "projects")
SEP = os.sep


def norm(p):
    return (p or "").replace("\\", "/").lower()


def main():
    dirs = [x for x in os.listdir(ROOT)
            if "mission-control" in x and "scratch" not in x and "demo" not in x]
    files = []
    for x in dirs:
        files += glob.glob(os.path.join(ROOT, x, "*.jsonl"))

    topic_reads = collections.Counter()
    searches = []
    grep_mem = 0
    sessions_with_topic_read = set()
    sessions_with_search = set()
    tool_counts = collections.Counter()

    for f in files:
        sid = os.path.basename(f)[:8]
        try:
            fh = open(f, "r", encoding="utf-8", errors="replace")
        except Exception:
            continue
        with fh:
            for line in fh:
                if '"tool_use"' not in line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                msg = rec.get("message") or {}
                content = msg.get("content")
                if not isinstance(content, list):
                    continue
                for blk in content:
                    if not isinstance(blk, dict) or blk.get("type") != "tool_use":
                        continue
                    name = blk.get("name", "")
                    inp = blk.get("input")
                    if not isinstance(inp, dict):
                        inp = {}
                    tool_counts[name] += 1
                    if name == "Read":
                        p = norm(inp.get("file_path"))
                        if "/memory/" in p and p.endswith(".md"):
                            topic_reads[p.rsplit("/", 1)[-1]] += 1
                            sessions_with_topic_read.add(f)
                    elif name in ("Grep", "Glob"):
                        blob = norm(inp.get("path")) + " " + norm(inp.get("pattern"))
                        if "/memory" in blob or "memory/" in blob:
                            grep_mem += 1
                    elif name == "Skill" and "memory-search" in str(inp.get("skill", "")):
                        searches.append((sid, str(inp.get("args", ""))[:90]))
                        sessions_with_search.add(f)
                    elif name.endswith("mem_search"):
                        searches.append((sid, str(inp.get("query", ""))[:90]))
                        sessions_with_search.add(f)

    n = len(files)
    print("sessions scanned:                       %d" % n)
    print("sessions that READ a memory topic file: %d  (%.0f%%)"
          % (len(sessions_with_topic_read), 100.0 * len(sessions_with_topic_read) / max(1, n)))
    print("sessions that ran a memory SEARCH:      %d  (%.0f%%)"
          % (len(sessions_with_search), 100.0 * len(sessions_with_search) / max(1, n)))
    print("distinct memory .md files ever opened:  %d  (total opens %d)"
          % (len(topic_reads), sum(topic_reads.values())))
    print("Grep/Glob against the memory dir:       %d" % grep_mem)
    print("memory searches total:                  %d" % len(searches))
    print()
    print("TOP OPENED:")
    for k, v in topic_reads.most_common(15):
        print("   %4d  %s" % (v, k))

    # The number that matters: front-page pointers that NOTHING ever opened.
    memdir = os.path.join(ROOT, "C--Users-levir-Documents--claude-mission-control",
                          "memory")
    if os.path.isdir(memdir):
        on_disk = {x.lower() for x in os.listdir(memdir) if x.endswith(".md")}
        never = sorted(on_disk - set(topic_reads))
        print()
        print("topic files on disk:                    %d" % len(on_disk))
        print("NEVER opened in %d sessions:             %d" % (n, len(never)))
        for x in never[:40]:
            print("   -  %s" % x)
        if len(never) > 40:
            print("   ... and %d more" % (len(never) - 40))

    print()
    print("SAMPLE SEARCH QUERIES (what agents actually asked):")
    for sid, q in searches[:20]:
        print("   [%s] %s" % (sid, q))


if __name__ == "__main__":
    main()
