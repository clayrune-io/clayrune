# MC-1166 unit 7e-3: layout, packaging and draft disclosures

Local review candidate based on master `a6362409`. Branch
`clayrune/mc1166-u7e3-polish`. No merge, push, deployment, operational keys,
configuration/flag change, real-service call or website/store edit.

## Changes and evidence

- `static/css/agent-mail-setup.css`: scope the wizard's flex/scroll layout to
  the agent-mail step. The form scrolls with `min-height: 0`; the title and
  Back/Next remain visible. Both 390px and 1000px widths are exercised at their
  normal heights and at 400px height.
- `static/css/agent-mail-status.css`: mobile Refresh has the same 40px minimum
  height as the existing mail action buttons.
- `tools/smoke/_agent-mail-layout.mjs`: shared clipping, bounds, touch-target and
  hit-testing checks. Each mail control is scrolled into view and checked against
  the actual topmost element. Wizard navigation is checked before scrolling and
  after scrolling the form. Screenshots retain the real viewport height.
- `tools/smoke/agent-mail-{status,actions,setup}.mjs`: apply those checks to all
  existing fixture states, action outcomes/refusals and setup/manifest failures.
  Shared passcode screens get reachability checks, including wrong-passcode and
  connection-failure states. Their existing generic touch-target sizes are not
  changed by this mail-specific unit.
- `tests/test_agent_mail_installer_assets.py`: check all 43 app Python modules,
  nine static files and the source-only offline signer against their actual
  delivery paths. No packaging omission was found, so no installer changed.
- `docs/AGENT_MAIL_PRIVACY_DRAFT.md`, the README phone-home disclosure and
  `docs/USER_GUIDE.md` Settings section: each clearly marked DRAFT. Include owner
  address/evidence, project/conversation text, Cloudflare/mail/model recipients,
  Claude extraction, normal target-session memory, provisional retention,
  immediate local disable, pending remote revocation and erasure limitations.
  `CHANGELOG.md` records the disabled candidate and these changes.

### Red-before evidence

With the new checks and old CSS:

- All 15 status fixtures at 390px fail the 40px touch-target check: Refresh is
  36px high. Their 1000px counterparts pass.
- At 390×400 the wizard navigation is below the visible card: footer top
  435.20px, bottom 466.20px, card bottom 384px. Hit testing fails. A scrollable
  whole card made the controls reachable only after scrolling and hid navigation
  while reading the form. The new split keeps navigation visible throughout.

Those checks pass with the CSS changes. No actions-model, setup mutation, human
proof or enrollment authority logic was changed.

## Installer audit

The macOS spec recursively includes `static/` in `datas`; all six mail scripts
and three stylesheets are also referenced by the real dashboard. All 43
`mc/**/agent_mail*.py` modules are in the app's statically discoverable import
closure, including imports inside functions. The regression also checks the
spec actually passes the audited entrypoint, datas and hiddenimports to Analysis.

Windows' executable bootstraps `installer/install.ps1`, which clones the full
repository; the Unix source installer does the same. Tests inspect the actual
Git archive, so an untracked/excluded future module or asset fails the check.
The offline `tools/agent-mail-manifest-sign.py` is source-distributed operator
tooling, outside the frozen-client import/data set; clients consume its signed
output and do not run the signer. Worker and control-plane code are separate
service deployment artifacts, not client runtime files.

This is source-level asset/import coverage. No macOS frozen artifact was built,
no existing release binary was inspected, and no Windows/Linux clean-VM install
was run. Installer files are unchanged. No operational root/private key is
needed or used by these checks.

## Verification

Every execution used the installation `tools/test-run.py` launcher, the project
Python interpreter and this worktree's cwd. Multi-script runs used explicit
ordered manifests under `_scratch/7e3/`, through Clayrune-owned registered jobs.

- Related pytest: **290 passed** across status, enrollment, enrollment routes/
  review, manifest fetch/service manifest and existing build-info/Windows
  installer-security tests. Initial combined run: **395 passed** including the
  first 105 packaging cases. Final packaging suite: **107 passed** (397 unique
  pytest cases across the final coverage; repeated runs are not added together).
- Mail status smoke: **241 checks**, actions smoke: **498 checks**, setup smoke:
  **209 checks**, all passing at 390px and 1000px. Actions were rerun after adding
  passcode-screen reachability coverage.
- Boot smoke, first-run setup-gate and first-run step-anatomy: PASS.
- Source checks: `git diff --check` clean. No `mc/` production Python edit, so
  pyright is not applicable.
- The test reporting channel returned HTTP 403 and printed progress/capture
  incomplete warnings (also node enrollment/event flush diagnostics). Both
  completed multi-unit job exits were 0; the actual pytest/smoke logs supply
  these counts. Reporting was not repaired or bypassed.

Screenshots and full logs are ignored artifacts under `_scratch/7e3/` in the
builder worktree. Screenshots are synthetic fixtures, not live enrollment.
The Android WebView/physical keyboard was not exercised; the 400px check models
reduced viewport space, not a verified keyboard lifecycle. Shared passcode
buttons retain their pre-existing generic sizing; their reachability passes.

## Remaining gates and decisions

No implementation design choice blocks this local candidate. Before publishing
the disclosures, the operator must confirm processor/operator details, regions/
terms, retention/capacity commitments, service-wide erasure and backup lag. The
draft explicitly leaves those unresolved; it does not turn code defaults into
an approved policy. Session/project deletion revokes local bindings and queued
local work, not the entire remote registration or all remote data.

Recommend a separate pre-publication policy/operations review using the draft's
publication-blocker list. Publishing now would leave those commitments undefined;
changing retention/erasure behavior first would require a separate build and
acceptance scope. Neither path is selected or authorized here.

Public privacy policy, website and Play Store copy are unchanged. Feature flags
remain untouched. No real mail or service-side behavior was tested.

## Commit blocker: junction cleanup refused

Automatic PreToolUse approval review rejected the prescribed non-recursive
`cmd /c rmdir tools\smoke\node_modules` before execution, classifying it as
"destructive delete outside scratch (irreversible)" and explicitly prohibiting
retry in this guarded session. Both created junctions had been inspected as
Directory/ReparsePoint Junctions targeting the main checkout. Neither main
dependency directory was changed. The second unlink was not attempted after the
first rejection. No alternate deletion mechanism or permission change was tried.

The dispatch requires unlinking before committing, so **no commit was made**.
Only this unit's explicit paths are staged for review. The final candidate patch
is also saved to ignored `_scratch/7e3/changes.patch`; all work remains in the
assigned branch/worktree. A permitted fresh session must unlink both junctions
non-recursively, verify main dependencies remain, and commit the staged paths.
No merge or push is authorized by this dispatch.

The earlier attempt to write the required plan under the home `.claude/plans/`
directory was also refused as global configuration outside project scope. It was
not retried. No skill or rule was changed to bypass either refusal.
