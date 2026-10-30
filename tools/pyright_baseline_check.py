#!/usr/bin/env python3
"""Fail CI on NEW pyright errors only; keep the known ones in a baseline.

    python tools/pyright_baseline_check.py              # run pyright, diff vs baseline
    python tools/pyright_baseline_check.py --update     # rewrite the baseline
    python tools/pyright_baseline_check.py --report r.json   # diff a saved report

A diagnostic is keyed by (repo-relative file, rule, first line of message) --
never by line number, so edits above an existing error do not make it "new".
Keys are counted, not de-duplicated: a second identical error in the same
file exceeds the baseline count and fails.

Exit codes: 0 no new errors, 1 new errors, 2 pyright produced no usable
report (never treated as a pass). Baseline entries that no longer occur are
printed as FIXED so the baseline can be ratcheted down with --update.
Regeneration steps: docs/PYRIGHT_BASELINE_PART2.md.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
DEFAULT_BASELINE = Path(__file__).resolve().parent / "pyright_baseline.json"
BASELINE_VERSION = 1
# Pyright resolves os/ctypes/signal/winreg attributes per platform, so the same
# source yields different errors on Windows and Linux (measured: 16 vs 24 apart).
# CI is ubuntu; pin the platform so a baseline made on any dev box matches CI.
DEFAULT_PLATFORM = "Linux"

Key = tuple[str, str, str]


def _rel(file: str, root: Path) -> str:
    try:
        return Path(file).resolve().relative_to(root.resolve()).as_posix()
    except (ValueError, OSError):
        return Path(file).as_posix()


def _headline(message: str) -> str:
    # Pyright appends indented detail lines (NBSP-indented) that vary with the
    # pyright version; the first line names the actual problem.
    first = message.split("\n", 1)[0]
    return " ".join(first.split())


def collect(report: dict[str, Any], root: Path = REPO) -> tuple[Counter[Key], dict[Key, list[int]]]:
    """Count errors by key; also return the 1-based lines seen per key."""
    counts: Counter[Key] = Counter()
    lines: dict[Key, list[int]] = {}
    for d in report.get("generalDiagnostics", []):
        if d.get("severity") != "error":
            continue
        key = (_rel(d.get("file", ""), root), d.get("rule") or "", _headline(d.get("message", "")))
        counts[key] += 1
        lines.setdefault(key, []).append(d.get("range", {}).get("start", {}).get("line", -1) + 1)
    return counts, lines


def diff(current: Counter[Key], baseline: Counter[Key]) -> tuple[dict[Key, tuple[int, int]], dict[Key, tuple[int, int]]]:
    """Return (new, fixed): key -> (current_count, baseline_count)."""
    new = {k: (n, baseline.get(k, 0)) for k, n in current.items() if n > baseline.get(k, 0)}
    fixed = {k: (current.get(k, 0), n) for k, n in baseline.items() if n > current.get(k, 0)}
    return new, fixed


def load_baseline(path: Path) -> Counter[Key]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("version") != BASELINE_VERSION:
        raise ValueError(f"{path}: unsupported baseline version {data.get('version')!r}")
    return Counter({(e["file"], e["rule"], e["message"]): int(e["count"]) for e in data["errors"]})


def write_baseline(path: Path, counts: Counter[Key]) -> None:
    errors = [
        {"file": f, "rule": r, "message": m, "count": n}
        for (f, r, m), n in sorted(counts.items())
    ]
    path.write_text(
        json.dumps({"version": BASELINE_VERSION, "errors": errors}, indent=1, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def parse_report(text: str) -> dict[str, Any]:
    start = text.find("{")  # tolerate banner lines before the JSON
    if start < 0:
        raise ValueError("no JSON object in pyright output")
    report = json.loads(text[start:])
    if "generalDiagnostics" not in report:
        raise ValueError("pyright JSON has no generalDiagnostics")
    return report


def run_pyright(root: Path, platform: str = DEFAULT_PLATFORM) -> dict[str, Any]:
    exe = shutil.which("pyright")
    cmd = [exe] if exe else [sys.executable, "-m", "pyright"]
    # pyright exits 1 when it finds errors, so the exit code says nothing here.
    proc = subprocess.run(
        cmd + ["--pythonplatform", platform, "--outputjson"], cwd=root, capture_output=True, text=True, encoding="utf-8", check=False
    )
    try:
        return parse_report(proc.stdout)
    except ValueError as e:
        raise RuntimeError(f"pyright gave no usable report ({e}); rc={proc.returncode}; stderr: {proc.stderr[-500:]}")


def _fmt(key: Key) -> str:
    return f"{key[0]}  [{key[1] or 'no-rule'}]  {key[2]}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    ap.add_argument("--report", type=Path, help="read a saved `pyright --outputjson` file instead of running pyright")
    ap.add_argument("--root", type=Path, default=REPO, help="repo root used to relativise file paths")
    ap.add_argument("--platform", default=DEFAULT_PLATFORM, help="pyright --pythonplatform (default: %(default)s, matches CI)")
    ap.add_argument("--update", action="store_true", help="rewrite the baseline from the current errors")
    args = ap.parse_args(argv)

    try:
        if args.report:
            report = parse_report(args.report.read_text(encoding="utf-8"))
        else:
            report = run_pyright(args.root, args.platform)
    except (RuntimeError, ValueError, OSError) as e:
        print(f"pyright-baseline: {e}", file=sys.stderr)
        return 2

    current, lines = collect(report, args.root)
    total = sum(current.values())

    if args.update:
        write_baseline(args.baseline, current)
        print(f"pyright-baseline: wrote {args.baseline} ({total} errors, {len(current)} keys)")
        return 0

    try:
        baseline = load_baseline(args.baseline)
    except (OSError, ValueError, KeyError) as e:
        print(f"pyright-baseline: cannot read baseline: {e}", file=sys.stderr)
        return 2

    new, fixed = diff(current, baseline)
    for key, (now, was) in sorted(fixed.items()):
        print(f"FIXED  {_fmt(key)}  (baseline {was}, now {now})")
    if fixed:
        print(f"pyright-baseline: {sum(w - n for n, w in fixed.values())} baseline error(s) fixed; "
              f"run with --update to ratchet the baseline down")
    for key, (now, was) in sorted(new.items()):
        print(f"NEW    {_fmt(key)}  (baseline {was}, now {now}; lines {sorted(lines[key])})")
    if new:
        print(f"pyright-baseline: FAIL, {sum(n - w for n, w in new.values())} new error(s) "
              f"({total} total vs {sum(baseline.values())} in baseline)")
        return 1
    print(f"pyright-baseline: ok, {total} errors, none new (baseline {sum(baseline.values())})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
