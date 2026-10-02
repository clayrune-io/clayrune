<!--
A short PR is fine. See CONTRIBUTING.md for how to run the checks.
Security problem? Do not describe it here. See SECURITY.md.
-->

## What changed and why

<!-- What was wrong or missing, and what this does about it. Link the issue if there is one. -->

## How you checked it

<!-- Paste real output. "pytest tests/test_x.py: 4 passed" beats "tested".
For a bug fix, say which test fails without your change. If you did not run
something, say so. -->

- [ ] `pytest` (and `pytest control_plane/tests` if you touched `control_plane/`)
- [ ] `pyright` shows no new errors, if you touched `mc/`
- [ ] `tools/smoke` (`npm test`), if you touched `static/`
- [ ] Not applicable, and here is why:

## What you left alone

<!-- Anything you noticed but did not fix, and anything you were unsure about. -->

## Before it merges

- [ ] I staged files by path, with nothing from `data/` or my own machine in the diff
- [ ] No personal paths, emails or credentials in the diff
- [ ] User-visible change has an entry in `CHANGELOG.md`
