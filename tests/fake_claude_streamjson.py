"""Stand-in `claude` CLI for tests/test_agent_spawn_interactive.py (MC-1150 P3).
Not a test module (pytest only collects test_*.py).

Speaks the slice of Claude's stream-json Mode B protocol Clayrune's reader needs:
one `system/init` line at start, then for every `{"type":"user",...}` line on
stdin an `assistant` text line echoing it and a `result` line (the turn
boundary). Exits 0 on stdin EOF, like the real CLI. Every other argument
(`-r <id>`, `--append-system-prompt-file <p>`, flags) is ignored, except:

    --hang        never read stdin, print nothing (a CLI stuck at startup)
    --pid-file F  write this process's pid to F first
"""
import json
import os
import sys
import time


def main(argv):
    sys.stdout.reconfigure(newline='\n', encoding='utf-8')
    if '--pid-file' in argv:
        with open(argv[argv.index('--pid-file') + 1], 'w', encoding='utf-8') as f:
            f.write(str(os.getpid()))
    if '--hang' in argv:
        time.sleep(600)
        return 0
    sid = f'fake-sid-{os.getpid()}'
    print(json.dumps({'type': 'system', 'subtype': 'init', 'session_id': sid}), flush=True)
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            msg = json.loads(raw)
        except ValueError:
            print(f'not-json:{raw[:60]}', flush=True)
            continue
        content = (msg.get('message') or {}).get('content', '')
        print(json.dumps({'type': 'assistant', 'session_id': sid, 'message': {
            'role': 'assistant', 'content': [{'type': 'text', 'text': f'echo: {content}'}]}}),
            flush=True)
        print(json.dumps({'type': 'result', 'subtype': 'success', 'session_id': sid,
                          'result': 'ok', 'num_turns': 1, 'total_cost_usd': 0,
                          'usage': {'input_tokens': 1, 'output_tokens': 1}}), flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
