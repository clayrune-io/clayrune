# Applying the four human-owned patches (MC-1179 P64, P65, P96)

Four patches change files an agent is not allowed to edit (`steward/fence.py`,
`steward/core.py`, `data/skills/`). Each one is a `git apply` you run yourself.
Every command below runs **from the repo root**, in an ordinary terminal (not
inside an agent chat: the fence blocks the same commands there, and that is
correct).

| # | Patch | What it does | Review row |
|---|-------|--------------|------------|
| 1 | `steward-role-core-adapter.patch` | `steward/core.py`: the cycle builder accepts a role snapshot, so a role-bound steward can fire | P96 |
| 2 | `steward-role-directive.patch` | steward skill: new `CYCLE_DIRECTIVE.md`, and the skill stops telling a cycle to post backlog notes | P65 (MC-1140), P96 |
| 3 | `fence-timeout-seconds.patch` | `steward/core.py`: fence hook timeout 10000 s becomes 10 s | (follow-up) |
| 4 | `fence-hivemind-reviewer-armed.patch` | `steward/fence.py` and `mc/launch_marker.py`: the fence arms for a Hivemind reviewer session | P64 |

**Order: 1, 2, 3, 4.** Patches 1 and 2 are a pair (the role dispatcher needs both).
All four were checked against `master` at `bba8ad42`, individually and together, and
no two of them touch the same lines (1 and 3 both edit `steward/core.py`, about 110
lines apart). They also apply in the reverse order.

If a step prints anything other than what is shown, **stop and do not continue**.
Nothing has been changed until step 6.

---

## Before you start

**1. Be on `master` with this work merged.**

```
git rev-parse --abbrev-ref HEAD
```
You should see: `master`

**2. Confirm the four patch files are present.**

```
ls docs/patches/steward-role-core-adapter.patch docs/patches/steward-role-directive.patch docs/patches/fence-timeout-seconds.patch docs/patches/fence-hivemind-reviewer-armed.patch
```
You should see all four names echoed back, no "No such file". (If one is missing, the
branch with the P64 patch is not merged yet: stop.)

**3. Confirm the files the patches touch have no local edits.**

```
git status --short steward mc/launch_marker.py data/skills/builtin/mc-steward
```
You should see: nothing (empty output). If it lists files, commit or stash them first,
otherwise a patch can fail or mix with your edits.

## Dry run (changes nothing)

**4. Check that all four apply, in order.**

```
git apply --check docs/patches/steward-role-core-adapter.patch docs/patches/steward-role-directive.patch docs/patches/fence-timeout-seconds.patch docs/patches/fence-hivemind-reviewer-armed.patch
```
You should see: nothing, and the prompt comes back. Any `error: patch failed` line means
stop (master has moved under the patch; ask for it to be regenerated).

## Apply

**5. Apply patches 1 and 2 (steward cycle).**

```
git apply docs/patches/steward-role-core-adapter.patch docs/patches/steward-role-directive.patch
```
You should see: nothing.

**6. Apply patch 3 (fence timeout).**

```
git apply docs/patches/fence-timeout-seconds.patch
```
You should see: nothing.

**7. Apply patch 4 (arm the fence for the reviewer).**

```
git apply docs/patches/fence-hivemind-reviewer-armed.patch
```
You should see: nothing.

## Check each one took effect

**8. See which files changed.**

```
git status --short
```
You should see these five lines (plus whatever else was already dirty on your side):
```
 M data/skills/builtin/mc-steward/SKILL.md
 M mc/launch_marker.py
 M steward/core.py
 M steward/fence.py
?? data/skills/builtin/mc-steward/CYCLE_DIRECTIVE.md
```

**9. Patch 1 (core adapter).**

```
git grep -n "cycle_snapshot" -- steward/core.py
```
You should see two lines, `steward/core.py:138:` (the new `cycle_snapshot=None`
parameter) and `steward/core.py:146:`.

**10. Patch 2 (directive).**

```
ls data/skills/builtin/mc-steward
```
You should see: `CYCLE_DIRECTIVE.md` and `SKILL.md`.

**11. Patch 3 (timeout).**

```
git grep -n "FENCE_HOOK_TIMEOUT_SEC" -- steward/core.py
```
You should see two lines: `steward/core.py:265:FENCE_HOOK_TIMEOUT_SEC = 10` and
`steward/core.py:274:` using it.

**12. Patch 4 (reviewer armed).**

```
git grep -n "hivemind_reviewer" -- steward/fence.py mc/launch_marker.py
```
You should see: `mc/launch_marker.py:44:    'hivemind_reviewer',` and, in
`steward/fence.py`, the comment at line 5174 and the set entry
`steward/fence.py:5181:    'hivemind_reviewer',`.

**13. Run the tests that were waiting for these patches.**

Use the same Python that starts Clayrune (on Windows `.venv\Scripts\python`; the
commands below call it `python`).

```
python -m pytest tests/test_fence_hivemind_reviewer_armed.py tests/test_fence_fail_closed_unattended.py tests/test_launch_marker.py tests/test_claude_retry_watchdog.py -p no:cacheprovider
```
You should see a last line with **no `failed` and no `xfailed`**, ending like
`155 passed, 13 skipped`. (Before the patches the same command reports 5 `xfailed`;
after them those five turn into passes, and the 13 skips are the "throwaway copy"
tests that only make sense before a patch is applied.)

## Commit it

**14. Commit exactly the five patched files** (so the fix is on `master`, the release
channel):

```
git add steward/core.py steward/fence.py mc/launch_marker.py data/skills/builtin/mc-steward/SKILL.md data/skills/builtin/mc-steward/CYCLE_DIRECTIVE.md
git commit -m "Apply human-owned patches: steward role adapter + directive, fence timeout, fence armed for hivemind reviewer (MC-1179 P64 P65 P96)"
```
You should see: a commit summary listing 5 files changed. Pushing is your call
(`git push origin master`); `master` is what other installs update from.

## Restart the server

**15. Restart Clayrune** so the running server loads the new `steward/core.py` and
`mc/launch_marker.py`: sidebar **Power**, then **Restart**. It warns if sessions or
Hiveminds are running and may ask for your passcode. Wait for the dashboard to
reconnect.

What needs the restart and what does not:

- `steward/fence.py` is run fresh by Claude Code on every tool call, so a new
  session picks it up immediately.
- `mc/launch_marker.py` and `steward/core.py` live in the server process: **without
  the restart the server still has the old lists**, which means the reviewer launch
  does not get the fallback arming marker, the fail-closed launch, or the unattended
  retry budget.
- Per-project `.claude/settings.json` keeps `"timeout": 10000` until the steward is
  re-enabled or the project path changes (`install_fence_to_project` rewrites it). The
  unattended launch entry carries its own timeout, so the fence is still bounded for
  the sessions that matter.

## How you know the fence is armed for a reviewer

**16. Fresh-process check of both lists** (run after the restart; it reads the files on
disk, so it proves what the next server start and the next hook call will see):

```
python -c "from steward import fence; from mc import launch_marker as m; print('hivemind_reviewer' in fence._UNATTENDED_TRIGGER_TYPES, m.launch_env('hivemind_reviewer', base={}).get('CLAYRUNE_LAUNCHED_UNATTENDED'))"
```
You should see: `True 1`
(Before the patches it printed `False None`.)

**17. End-to-end proof on a real reviewer run.** Only a running reviewer proves the
whole chain. When the next Hivemind reviewer session runs, a push or delete it tries
shows `STEWARD FENCE blocked this action` in its transcript, and its session record
carries `trigger_type: hivemind_reviewer`. Until then, steps 13 and 16 are the strongest
evidence available: the tests run the patched fence's real `main()` with a reviewer
session and a `git push` event and require exit code 2 (blocked), and the same event
against the unpatched copy exits 0.

## Two things to expect

- **The Steward panel will go quiet for a steward that follows the new directive.**
  The panel (`loop_health()` in `steward/core.py`, shown by `static/js/steward.js`)
  counts `DECISION NEEDED` / `BLOCKED` notes on the charter item and takes "last
  activity" from the newest note. After patch 2 a cycle writes a journal file in
  `docs/_journal/` and no notes, so the panel shows 0 to approve and "no activity
  yet" for a healthy steward. Re-pointing the panel at the journal is a separate,
  not yet built change (it is in a human-owned file, so it will arrive as another patch).
- **A role-bound steward schedule can now fire**: before patch 1 every such fire was
  refused with "SR-2 core adapter patch is unapplied".

## Undo

Each patch reverses on its own with `git apply -R`. Undo in the **reverse** order, and
only if you have **not committed** yet (if you did, use `git revert <commit>` instead).
Each command prints nothing on success.

**U1. Undo patch 4.**
```
git apply -R docs/patches/fence-hivemind-reviewer-armed.patch
```

**U2. Undo patch 3.**
```
git apply -R docs/patches/fence-timeout-seconds.patch
```

**U3. Undo patch 2** (this also removes `CYCLE_DIRECTIVE.md`).
```
git apply -R docs/patches/steward-role-directive.patch
```

**U4. Undo patch 1.**
```
git apply -R docs/patches/steward-role-core-adapter.patch
```

**U5. Confirm nothing is left, then restart the server (step 15) again.**
```
git status --short steward mc/launch_marker.py data/skills/builtin/mc-steward
```
You should see: nothing.

Undoing patch 4 alone makes the reviewer unfenced again (the gap the review
found). Undoing patch 2 without patch 1 (or the reverse) leaves a role-bound steward
fire refused, as it is on `master` today.

Other files in this folder (`fence-attend-once-per-call`, `fence-browser-read-digest`,
`fence-disk-wipes`, `fence-mod-plant`, `fence-unlock-limits`) are separate patches and
are not covered by this page.
