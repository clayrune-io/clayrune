# Mid-turn Send (MC-1086)

Send to a session whose turn is running no longer kills and respawns the
process by default.

| Session state | Plain Send | Interrupt button |
|---|---|---|
| Claude, Mode B (`mode_b_kind='native'`), live process | **steer**: user message written to the live process's stdin, folded into the running turn | interrupt-and-resume (old behaviour) |
| Vendor with no live process (`mode_b_kind` `none`/`synthetic`: Codex, Gemini, Qwen, OpenCode, Goose, Aider, Kiro) | **queue**: `pending_followups` + a `> [queued]` chat line; sent when the turn ends | interrupt-and-resume |
| Claude Mode A (prompt is a `-p` argument, no stdin channel) | **queue** | interrupt-and-resume |
| Waiting on a question/plan card, respawn in flight, `proc` dead, or capability unknown | interrupt-and-resume (unchanged) | interrupt-and-resume |

Esc / Stop are untouched.

## Where it lives

- `mc/mid_turn_send.py` — routing decision (`decide`), the steer write, the
  queue append, the vendor queue drain. No Flask, no `mc.blueprints` imports.
- `mc/blueprints/agent_routes.py` — wiring only: `agent_send` calls `decide`
  where it used to hardcode `'interrupt'`; `_runtime_log_completion` calls
  `drain_next` when a vendor turn ends; the guardian's stuck-queue replay skips
  vendor sessions (it spawns `claude -r`).
- `static/js/send-intent.js` — composer wording + Interrupt button;
  `conversation.js` passes `send_mode:'interrupt'` and lifts the
  `_sendInFlight` gate on a steer/queue response (no `turn_start` follows one).
- Request field: `send_mode` = `'interrupt'` | absent. Response carries
  `route: 'steer'|'queue'` (queue also `queued: true`).

## Evidence: stdin steering on Claude Code 2.1.296

Probe: `claude --print --verbose --output-format stream-json --input-format
stream-json --dangerously-skip-permissions --model haiku`, stdin kept open.
Turn 1 = "make six sequential Bash calls, each `sleep 6`". A second stream-json
user message (`{"type":"user","message":{"role":"user","content":...}}`) was
written 11.0s in. Timestamps are seconds since the first message.

**Run A — steer to stop.** Message: "CHANGE OF PLAN: do not run any further
Bash calls. Reply right now with the single word STEERED."

```
[ 4.4s] assistant TOOL_USE call 1 (sleep 6)
[11.0s] >>> SENT steer
[11.1s] user tool_result "1"          <- tool boundary
[12.9s] assistant TEXT: STEERED       <- no call 2
[13.1s] result/success "STEERED" num_turns=2
```

**Run B — steer to append.** Message: "when you answer, also append the exact
word PINEAPPLE_STEER_OK on its own line." Sent at 11.0s, just after call 2 was
issued; the turn ran on through all six calls and the final answer carried
the word:

```
[10.5s] assistant TOOL_USE call 2
[11.0s] >>> SENT steer
...
[46.6s] assistant TEXT: ALL SIX DONE.\nPINEAPPLE_STEER_OK
[46.7s] result/success num_turns=7        <- one result for the whole turn
```

In both runs: one `result`, process exit code 0 only after stdin close, no kill,
no respawn. The steer is consumed at the next tool boundary, not instantly
mid-tool.

**Caveat found by Run C** (earlier probe, `sleep 25` as a backgrounded task):
a message written while the turn is only *waiting on a background task* is
delivered as its own new turn (`result` num_turns=3, then a second `result`
num_turns=1), because there is no tool boundary to fold into. The message is
still acted on; it just is not folded. Not a failure, but "steer" there means
"answered immediately as a second turn".

Probe scripts and logs were kept under `_scratch/steer_probe/` (gitignored).
To re-run: write the probe's first message, `sleep`, write the second.

## Tests

- `tests/test_mid_turn_send.py` — routing matrix (provider x state x intent),
  steer/queue actions, `/agent/send` wiring, vendor drain.
- `tools/smoke/mid-turn-send-ui.mjs` — composer: Interrupt button, wording,
  `send_mode` on the wire, in-flight gate lifted.

## Not verified

- Live end-to-end through a running Clayrune server (server not restarted, per
  brief). Verified against the CLI directly and against the Flask test client.
- A real vendor (Codex/Gemini/...) turn draining its queue on completion:
  covered by unit tests with a stubbed `/agent/followup`.
