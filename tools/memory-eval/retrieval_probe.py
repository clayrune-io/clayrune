"""Retrospective memory-retrieval measurement (2026-08-05).

Answers, from transcripts we already have on disk, the question the steward's
access-counter proposal cannot: do the notes on the front page actually get
found and opened, and do memory searches lead anywhere?

Read-only. Writes nothing.
"""
import os
import sys
import json
import collections

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _checkout  # noqa: E402

ROOT = os.path.join(os.path.expanduser("~"), ".claude", "projects")
SEP = os.sep


def norm(p):
    return (p or "").replace("\\", "/").lower()


def memory_dir_for(root):
    """This checkout's own `memory/` dir under `root`, or None. Derived from the
    checkout's path (never a literal), trying the CLI's flattened variants.
    Where several exist, the one whose MEMORY.md changed last wins, the same
    rule `mc.memory._native_memory_path` applies (a stale `_` variant can sit
    beside the live one)."""
    best, best_mtime = None, -1.0
    for enc in _checkout.encoded_variants(_checkout.default_checkout()):
        d = os.path.join(root, enc, "memory")
        if not os.path.isdir(d):
            continue
        idx = os.path.join(d, "MEMORY.md")
        mtime = os.path.getmtime(idx) if os.path.isfile(idx) else 0.0
        if mtime > best_mtime:
            best, best_mtime = d, mtime
    return best


def main():
    files = _checkout.transcript_files(ROOT)

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
    memdir = memory_dir_for(ROOT)
    if memdir is None:
        print()
        print("never-opened block skipped: no memory dir for this checkout "
              "(looked for %s/memory under %s)"
              % (_checkout.encoded_variants(_checkout.default_checkout())[0], ROOT))
    else:
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
