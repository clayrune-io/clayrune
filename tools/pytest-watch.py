"""Live progress tracker for a pytest run that writes to a log file.

Tells a stuck run from a slow one at a glance: done/total, rate, ETA,
failures so far, and how long since the count last moved.

The run must stream to the file and print counts, e.g.:

    python -u -m pytest -q -o console_output_style=count > run.log 2>&1

Piping pytest through `tail` holds all output until the end, which is how a
hung run looked identical to a busy one for 40 minutes (2026-10-30).

Usage:
    python tools/pytest-watch.py run.log [--stall 120] [--interval 5]
"""
import argparse
import os
import re
import sys
import time

COUNT_RE = re.compile(r"\[\s*(\d+)/(\d+)\]")
SUMMARY_RE = re.compile(r"=+ .*\b(passed|failed|error|errors|no tests ran)\b.* in [\d.]+s.*=+")
RESULT_CHARS = set(".FEsxX")


def read_progress(path):
    """Return (done, total, failures, errors, summary_line_or_None)."""
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    lines = text.splitlines()
    done = total = 0
    fails = errs = 0
    for line in lines:
        m = COUNT_RE.search(line)
        marks = (line[:m.start()] if m else line).strip()
        if not marks or not set(marks) <= RESULT_CHARS:
            continue
        fails += marks.count("F")
        errs += marks.count("E")
        if m:
            done, total = int(m.group(1)), int(m.group(2))
        elif line is lines[-1]:
            done += len(marks)  # partial line still being written
    summary = None
    for line in reversed(text.splitlines()[-40:]):
        if SUMMARY_RE.search(line):
            summary = line.strip()
            break
    return done, total, fails, errs, summary


def fmt_secs(s):
    s = int(s)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m{s % 60:02d}s"
    return f"{s // 3600}h{(s % 3600) // 60:02d}m"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("log")
    ap.add_argument("--stall", type=int, default=120, help="seconds without progress before flagging STUCK")
    ap.add_argument("--interval", type=float, default=5.0)
    args = ap.parse_args()

    start, done0 = time.time(), None  # rate is measured over what we watched
    last_done, last_move = -1, time.time()
    while True:
        if not os.path.exists(args.log):
            print(f"\rwaiting for {args.log} ...", end="", flush=True)
            time.sleep(args.interval)
            continue
        done, total, fails, errs, summary = read_progress(args.log)
        now = time.time()
        if done0 is None:
            done0 = done
        if done != last_done:
            last_done, last_move = done, now
        idle = now - last_move
        elapsed = now - start
        rate = (done - done0) / elapsed if elapsed > 0 else 0.0
        eta = (total - done) / rate if rate and total else 0
        pct = 100.0 * done / total if total else 0.0
        bad = f"\033[31mF={fails} E={errs}\033[0m" if (fails or errs) else "F=0 E=0"
        state = (f"\033[41;97m STUCK? no progress for {fmt_secs(idle)} \033[0m" if idle >= args.stall
                 else f"\033[32mmoving\033[0m (last {fmt_secs(idle)} ago)")
        line = (f"{done}/{total or '?'} {pct:5.1f}% | {rate:5.1f}/s | ETA {fmt_secs(eta) if eta else '?'}"
                f" | {bad} | {state}")
        print("\r\033[K" + line, end="", flush=True)
        if summary:
            print("\n\n" + summary, flush=True)
            return 1 if (fails or errs or "failed" in summary or "error" in summary) else 0
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
