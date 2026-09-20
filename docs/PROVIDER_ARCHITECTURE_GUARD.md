# Provider architecture guard

`tests/test_provider_architecture_guard.py` protects the boundary that is
easy to regress while migrating Clayrune features: feature modules may call a
provider-neutral runtime/service, but may not construct an inference process,
import a concrete runtime, or decode a vendor wire format themselves.

The guard is deliberately AST-based. It follows assignments containing a
provider executable resolver (for example `_resolve_claude()`) into
`subprocess.Popen/run/call/check_output/check_call`, and rejects imports of
`ClaudeRuntime`, `CodexRuntime`, `GeminiRuntime`, `QwenRuntime`, or the raw
vendor capture modules. It does not reject ordinary JSON, git, ffmpeg,
browser, terminal, mail, or installer subprocesses.

## Existing exceptions

The allowlist is a shrinking migration ledger, not a permission to add a
provider bypass. Each entry in the test names its exact source line, owner,
and removal gate. A new call fails CI even if it is added inside an already
allowlisted file or function. A moved/deleted call leaves a stale entry and
also fails, forcing the ledger to stay truthful.

| Area | Current exception | Owner | Removal gate |
| --- | --- | --- | --- |
| agent dispatch/recovery | legacy Claude auth, dispatch, follow-up, revival and respawn subprocesses | agent-runtime owner | move each path behind the runtime and durable lifecycle |
| Memory | legacy agent-condense subprocess | memory owner | replace it with the provider-neutral publication service |

The installer, git sync, media, browser, terminal, remote-access, system,
mail, and backup subprocesses are outside the inference boundary by design.
That classification must not be used to hide a model invocation: a new
inference path belongs in the guarded feature set and needs a real runtime
service, not another exception.

Adding an allowlist row requires all of the following in the same change:

1. the exact source line and coupling kind;
2. a named owning subsystem;
3. a concrete removal gate; and
4. a test proving the new path is still an inference exception rather than a
   false positive from an installer/git/media operation.

The intended direction is monotonic: remove rows as vertical feature paths
move behind the provider-neutral execution boundary. Do not broaden the
scanner to make a failing migration test pass.
