# Clayrune v2.4.8

17 commits since v2.4.7 (2026-11-03). A fix release; full detail is in `CHANGELOG.md` and the merge commits.

## Agents
- A routine context reset no longer marks a working agent as failed. The replaced process was being reported as a crash, so the run showed as an error and dropped off the Floor while it kept working. The status now returns to running once the new process starts.
- Resuming a conversation from the conversations list keeps its persona after a context reset, instead of falling back to the default agent.

## Desk
- Campaign chat can dispatch the project's hired agents.
- Desk agents can search the web and read their own project, read-only, and ground product claims in the project's materials.
- A sent campaign message no longer reappears in the compose box.
- Popped-out conversations can be dragged by their strip.

## Security
- A `node.exe` planted inside an approved repo is never started by the secrets helper or the custom MCP gate.

## Installers
No installer changes since v2.4.7; the Windows installer and zip are unchanged. The Mac app is rebuilt, signed and notarized for this release.
