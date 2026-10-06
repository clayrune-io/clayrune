# MC-1062 / 04: Offer saved, new and manual login after type selection

Status: proposed, not dispatched. [Parent design](../CONNECT_FLOW_SIMPLIFY.md).

## Depends on

02, 03.

## Ownership

New `static/js/desk-v1-connect-login-step.js`, corresponding CSS and `tools/smoke/desk-v1-connect-login-step.mjs`. Existing desk-v1-connect-signin.js remains the reusable login/fill component, with only narrow interface changes.

Future workers are not alone in the repository: do not revert others' edits; keep wiring narrow and adapt to concurrent changes.

## Work

Render one of Saved login, Username/password, In browser after Sign in is selected. Bind by route ID, including x-browser/linkedin-browser, rather than OAuth method matching. Use metadata-only login and profile pickers. Keep account label, vault naming and unusual options in Details. A typed new login stays in its input until final save; its subsequent fill is separately passcode-gated.

## Acceptance

Fresh X/no-app and both LinkedIn kinds; existing profile reuse; wrong-account and locked-vault states; CAPTCHA/2FA handoff; no automatic retry after wrong password. Back preserves input without storing it in JS state/storage; type/account change and Close clear it. Existing fill gate and origin/isolated-world protections unchanged.

## Boundary

Small UI slice. Manual cookie sign-in is not the held-OAuth flow; never route it through start-held.
