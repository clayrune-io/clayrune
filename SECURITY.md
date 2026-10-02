# Security policy

Clayrune runs agents that can read and write files and run commands on your
machine, so a security bug here can be a serious one. Reports are wanted, and a
reply is the normal outcome. Clayrune is maintained by one person, so please
allow a few days.

## Reporting a vulnerability

**Do not open a public issue for a vulnerability.** Report it privately:

1. Open the [Security tab](https://github.com/clayrune-io/clayrune/security).
2. Choose **Report a vulnerability**.
3. Describe what you found, how to reproduce it, and what it lets an attacker do.

That form is visible only to the maintainer, and it lets us talk to you, and
credit you if you want credit, without anything being public before a fix ships.

**If you do not see the "Report a vulnerability" button**, email
[hello@clayrune.io](mailto:hello@clayrune.io) with "Security" in the subject.
Please do not put vulnerability details in a public issue.

## What to expect

- An acknowledgement, usually within a few days.
- A decision on whether it is in scope, and a rough timeline for a fix.
- A fix on `master` and in a new tagged release, with a note in
  [CHANGELOG.md](CHANGELOG.md). We will not disclose the details before a fix is
  available, and we ask you not to either.

## Supported versions

`master` is the release channel. Installed copies update by pulling `master`,
so there are no long-lived release branches and no backports.

| Version | Supported |
|---|---|
| `master` | Yes |
| Latest tagged release | Yes |
| Any older tag | No, update to the latest |

## What is in scope

Anything that lets someone who should not have access to a Clayrune install, its
agents, or its stored credentials get it. For example:

- The remote-access path (the phone app and the clayrune.io tunnel) letting an
  unauthenticated party reach a dashboard.
- The secrets vault (`mc/secrets_store.py`) exposing a stored value, or any route
  returning one. [`docs/SECRETS.md`](docs/SECRETS.md) describes the intended
  design.
- Third-party content, such as a web page, an email, or a file an agent reads,
  making an agent do something its owner did not ask for. The surfaces we know
  about are mapped in
  [`docs/UNTRUSTED_INPUT_SURFACE.md`](docs/UNTRUSTED_INPUT_SURFACE.md).
- The block on irreversible commands (`git push`, `npm publish`, and the rest of
  the list in the README) being bypassable.
- The installer scripts fetching or running something other than what they
  claim to.
- A path-traversal, injection, or file-read flaw in the local server.

## What is out of scope

- Problems in Claude, in the Claude CLI, or in another provider's CLI. Report
  those to the vendor.
- A local user who already has full access to your account and files. Clayrune
  runs as you, on your machine, against your own agents.
- Anything that needs you to turn a safety setting off first and then follow an
  agent's instructions to the letter.
- The simulated demo at <https://clayrune.io/demo>. It runs in your browser and
  holds no data.

If you are not sure which side of the line something falls on, report it. A
report that turns out to be out of scope costs nothing.

Not a vulnerability? [SUPPORT.md](SUPPORT.md) says where to take it.
