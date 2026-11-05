"""Stand-in engine CLI for tests/test_agent_spawn.py (MC-1150 P2). Not a test
module (pytest only collects test_*.py).

    fake_engine_cli.py <engine> [--lines N] [--interval S] [--rc R]

Reads the prompt from stdin when stdin is a pipe/file (an engine that takes its
prompt on stdin), prints a few lines the way an engine would (default stdout
newline handling, so on Windows each line ends "\\r\\n"), then exits R.
"""
import os
import sys
import time


def main(argv):
    engine = argv[0] if argv else 'fake'
    lines, interval, rc = 2, 0.0, 0
    i = 1
    while i < len(argv):
        if argv[i] == '--lines':
            lines = int(argv[i + 1]); i += 2
        elif argv[i] == '--interval':
            interval = float(argv[i + 1]); i += 2
        elif argv[i] == '--rc':
            rc = int(argv[i + 1]); i += 2
        else:
            i += 1
    prompt = ''
    try:
        if not os.isatty(0):
            prompt = sys.stdin.read()
    except (OSError, ValueError):
        prompt = ''
    print(f'{engine}: prompt-bytes={len(prompt.encode("utf-8"))}', flush=True)
    first = prompt.splitlines()[0] if prompt.strip() else ''
    print(f'{engine}: prompt-head={first[:40]}', flush=True)
    for n in range(lines):
        print(f'{engine}: line {n}', flush=True)
        if interval:
            time.sleep(interval)
    return rc


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
