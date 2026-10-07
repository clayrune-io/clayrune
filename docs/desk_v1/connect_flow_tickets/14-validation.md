# MC-1062/14 validation and review handoff

The common Add service experience is implemented on the feature branch. It is not a claim of a released update or live vendor verification. Contract: [ticket 14](14-integration.md) and [parent section 11](../CONNECT_FLOW_SIMPLIFY.md#11-rons-rule-2026-10-06-one-flow-for-every-service).

## Evidence

- Exact `tests/test_desk_connect*.py` suite: **1328 passed in 293.45 seconds**. No Python production code, discovery guard or authorization endpoint changed.
- All **29 smoke entrypoints** were run: the 20 `desk-v1-connect-*.mjs` scripts, Connections, management status, legacy guide replacement, vault gate/locked card, human proof, secrets passcode, boot and inline scope. Obsolete legacy entrypoints invoke their supported replacement adapter coverage. Native screen fixtures isolate integration/options/account wiring; the production simplify smoke loads all modules.
- Initial runs failed obsolete selectors and an inline-checker symbol collision. The subsequent full battery exposed a standalone fixture missing the optional status refresh and the retired per-service guide entrance. Both were corrected and rerun green. Final production/reference/copy, Permissions, provider guide, wizard, boot and scope reruns passed. A loading-label race in the reference fixture was fixed to wait on loading state instead of old vocabulary.
- `desk-v1-connect-simplify.mjs` walks Higgsfield key/sign-in, LinkedIn member/Page, unknown URL, X without an app, and YouTube information at **1440 and 390**. It asserts identical six-screen titles/instructions/footer actions, no internal terms outside Details, no navigation writes, grants initially off, exact account kind, human sign-in/Save proof, keyboard Save reach and Review at 200% text. The manual software walk checks immutable approval, final passcode, exact technical command preservation and tile refresh on Done.
- Saved Higgsfield tile: selection sends no probe; Check it now sends one explicit free verify request, shows Verified, and a subsequent failed check shows Check failed. No live sign-in, paid read, upload or generation is exercised.
- Boot: **7 scenarios plus all guards green**. Inline-handler scope: **142 modules clean**. Diff whitespace check passed.

## Inspected screenshots

All captures use the real shipped HTML/CSS/JS with fake API data. Fixture avatar assets outside the tested flow are absent; these are connection-layout evidence, not a full-product visual baseline. Review titles, instruction lines, Back/Save placement, technical Details and saved Check controls were visually inspected at both widths; no horizontal overflow or clipped action was found.

| Walk | Desktop | Phone |
|---|---|---|
| Higgsfield Review | [1440](../screens/connect_simplify_higgsfield_1440.png) | [390](../screens/connect_simplify_higgsfield_390.png) |
| LinkedIn Review | [1440](../screens/connect_simplify_linkedin_1440.png) | [390](../screens/connect_simplify_linkedin_390.png) |
| Unknown Review | [1440](../screens/connect_simplify_unknown_1440.png) | [390](../screens/connect_simplify_unknown_390.png) |
| Saved tile check | [1440](../screens/connect_simplify_saved_check_1440.png) | [390](../screens/connect_simplify_saved_check_390.png) |

## Limits and handoff

Unknown bare names without a maintained profile still ask for an address; no official-site guessing or fallback reader was added. Dave's permitted future lookup must confirm the found address before reading. Unsupported suggestions stay information only. Provider sign-in, Save, install approval and permission prompts remain independent; X has no free provider probe. The integrator owns merge, push, post-merge smoke and restart. This dispatch performed none of those actions.
