# Campaign chat: sent-draft regression (2026-11-03)

The candidate fixes a demonstrated draft-lifecycle failure. The original
typed-message trigger remains unconfirmed; do not describe it as reproduced.

## Reproduction evidence

Against the real app at `http://localhost:5199`, four corrected probes used
real chat POSTs/model replies: inline Enter with a trailing space, inline Send
with a trailing newline, large-view Enter with a trailing newline, and
large-view Send with a trailing space. Each returned HTTP 200 without saved
suggestions and left the input empty. Newlines were inserted with Shift+Enter.

An earlier probe used Playwright `pressSequentially` with a newline. That
pressed Enter prematurely and did **not** test the intended Send/newline case;
it was replaced by the corrected probes above. Four test exchanges from that
first probe also reached the real chat. No test exchanges were deleted.

The reproducible case used the real localhost page/assets/workspace with only
the chat POST response intercepted in the test browser:

1. Type a message, a space and Shift+Enter. Send with Enter or the button.
2. Hold the response while the pending You turn appears.
3. **Inject** an `input` event whose textarea value is the original message,
   including its trailing whitespace. This is a controlled event, not an
   observed browser-generated event or evidence of dictation.
4. Release a successful response, either plain prose or a saved-suggestions
   response. The latter exercises the actual `onSuggested` callback.
5. Inspect the input, then fully remount the Desk with `deskV1Render()` and
   inspect it again.

Original code: all eight injected combinations (Enter/Send × inline/large ×
plain/suggestions) restored the sent text, including after remount. Eight
keyboard-only controls stayed empty. Candidate: all sixteen stayed empty.
Candidate verification used a browser-only override of the campaign-chat JS
asset; the server and other browser sessions were unchanged. Suggestion
responses were controlled and did not write live campaign suggestions.

`onSuggested` currently repaints the Posts body, not the chat column. Merely
switching campaign panels also keeps that column. The smoke therefore uses a
full Desk render to verify a genuinely new textarea; a detached-node assertion
checks this explicitly.

## Change

- `static/js/desk-v1-campaign-chat.js`: only the current connected textarea
  writes the draft, and only outside a pending send. Success retires the draft
  before reply callbacks. Completion synchronizes the live input from draft
  state, rather than comparing a raw input value with the trimmed request.
- Failure restores the request even if a stale event supplied different text.
  Completion updates only the sending campaign's live box. New unsent drafts
  retain their existing repaint behavior.
- `tools/smoke/desk-v1-campaign-chat.mjs`: 32-case keyboard/late-event matrix
  varies trailing space/newline, Enter/Send, inline/large and plain/suggestion
  replies. Additional checks cover retired textarea events, a new unsent draft,
  pending remount plus failure, and campaign switching during a request.

## Verification

Run from the repository root, using the existing smoke Playwright installation:

```text
node tools/smoke/desk-v1-campaign-chat.mjs --drafts-only
node tools/smoke/desk-v1-campaign-chat.mjs
node tools/smoke/boot-smoke.mjs
```

The final regression against the original JS at base `dac003fb` exited **1**:
`FAIL — 18 case(s)` (16 injected whitespace cases, detached textarea, failed
request overwritten by stale input). The same regression against the candidate
exited **0**: `PASS — sent drafts stay empty; failed requests and new drafts
survive repaints.` Keyboard-only controls passed on both versions.

The full campaign smoke exited **0**: `PASS — campaign conversation: draft
lifecycle, turns, suggestions, scope, refused sends, reload, Suggest, no-agent,
chrome and demo.` Frontend boot smoke exited **0**, with all seven boot
scenarios and its additional guards green. The shared chat chrome and its
separate pop-out smoke are untouched.
This is a branch-only candidate: no merge, push, server restart or deployment.
The source of the original late value is still unknown; the proven fix is to
the controlled draft resurrection path, not a claim about that source.

## Follow-up 2026-11-05: new campaign, sent text back after the pop-out (NOT reproduced)

Report: new campaign, message to Posy, status "Posy answered. Nothing was changed.",
pop-out clicked, the sent text is in the box (screenshot `data/uploads/agent_2f441368ff.png`,
caret mid-text at character 136 of 251).

**Ruled out, with evidence**

- Stale tab: the screenshot shows "Reads: README.md, CHANGELOG.md. Can search the web and
  read the project folder". That line came in with 102a9236, merged at 917cbd22, which
  contains 866a479b. The tab was running the fixed code. The server also serves the fixed
  file (`Retire the draft BEFORE` present, Last-Modified 2026-11-03).
- Double send: the real thread for `camp-draft-1793926282468-8yo4` has one user turn
  (00:52:45Z) and one Posy turn (00:52:59Z).
- Plain new-campaign path: `tools/smoke/desk-v1-campaign-chat-new.mjs` creates the campaign through
  `deskV1NewCampaignInProject` (draft id, POST/PATCH, workspace), types with real keystrokes, sends
  inline and from the large view, and reads the box after the answer, after pop-out, after 1.5 s and
  after a full re-render. Always empty.
- Slow reply with a full Desk render, a pop-out, a render + pop-out, or a panel switch during the wait
  (scratch probe): always empty.
- Input-method composition open at Send (CDP `Input.imeSetComposition`): button Send commits on blur
  before the clear and the box stays empty; Enter during composition inserts a newline and does not
  send (by design, `isComposing`).
- The live page at :5199 on the real campaign, only the chat POST stubbed: empty after answer, pop-out
  and 1.5 s.
- Nothing else in `static/js` writes this textarea: `desk-v1-how.js` Suggest sets its own fixed line;
  `composer-extras.js` only syncs the mic/send slot; `refreshModalById` is the project modal, not the Desk.

**What is left.** The only way code in this file leaves the box holding the sent text is an `input`
event on the live, enabled composer whose value is the whole text (the guard from 866a479b cannot tell
that from typing), or a value written with no event at all. Both need something outside the page:
an input method or Windows voice typing re-committing on focus, or a browser extension that re-inserts
text. The caret in the screenshot sits mid-text, which fits a value inserted by something other than
this file, but that is a reading, not proof.

**Probe added (changes no behaviour).** `_noteReentry` in `desk-v1-campaign-chat.js` records, when the box
goes from empty to exactly the text just sent, how it arrived: `{how: 'input'|'focus', inputType,
isTrusted, isComposing, focused, popped}` in `window.__deskChatReentry` and `console.warn`. Typing the
same words again and a failed send (request restored on purpose) do not trigger it. After a hard reload,
the next occurrence can be read in the console with `window.__deskChatReentry`.
A guard that ignores such a whole-text jump would be a fix for a cause not yet seen; it is not shipped.

Verification: `node tools/smoke/desk-v1-campaign-chat-new.mjs`, `desk-v1-campaign-chat.mjs`,
`desk-v1-campaign.mjs`, `desk-v1-chat-popout.mjs`, `desk.mjs`, `boot-smoke.mjs` all exit 0.
