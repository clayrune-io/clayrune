# Local LLM support: contributor brief

Status: **open for an outside contributor** (2026-10-05). Not on the core
roadmap; this file holds what we already know so whoever picks it up does not
have to rediscover it. Read `CONTRIBUTING.md` first, and open an issue or a
draft PR early, before building the whole thing.

## Goal

A user with a capable GPU can pick a local model (Ollama, LM Studio, or any
OpenAI-compatible endpoint) as the engine for one agent, and that agent does
real work: reads files, edits them, runs commands, reports back. When the model
cannot do that, Clayrune says so plainly instead of reporting success.

## What exists today

There is no local engine. The only path is a manual hack: point the Qwen Code
CLI's own settings (`~/.qwen/settings.json`, `modelProviders.openai.baseUrl`)
at `http://localhost:11434/v1`. That reroutes **every** Qwen agent on the
machine, not one, and nothing in Clayrune knows it happened.

## What one test showed

A single feasibility run on an 8 GB consumer GPU:

| | |
|---|---|
| Model | `qwen3:8b`, context raised to 32k via a Modelfile |
| Memory | 10 GB at 32k context; 41% of the model spilled to CPU |
| Speed | a 12.6k-token agent prompt took 103 s; 6.6 tokens/s generation |
| Task | write `fizzbuzz.py` + 3 pytest tests, run them, report output |
| Result | **failed**: the model reasoned correctly for 2 minutes, emitted a todo list, and made **zero tool calls**. No files, no reply. |
| Clayrune | marked the session `completed` anyway (a separate bug, below) |

Two lessons: a bigger GPU fixes the speed, but **tool use is a property of the
model, not the hardware**; and a Clayrune agent's system prompt is roughly
12-16k tokens before the task, so **32k context is the practical minimum**.

## The work, in order

1. **Honest status first.** A turn that ends with no reply text and no tool
   calls must not read `completed`. Engine-agnostic; it bites every provider.
2. **A per-agent local engine.** Base URL + model chosen per agent (roster /
   settings), never a global edit of a CLI's settings file. Two candidate
   routes, pick one in the issue before building:
   - ride `QwenRuntime` (`mc/agent_runtime.py`), which already speaks
     OpenAI-compatible and gets a per-launch settings file via
     `QWEN_CODE_SYSTEM_SETTINGS_PATH`;
   - or use a runtime whose CLI already supports local models natively
     (`OpenCodeRuntime`, `GooseRuntime`, `AiderRuntime` live in the same file).
   Whichever you choose, put new code in **its own module** under `mc/`; do not
   grow `mc/agent_runtime.py` (see `docs/ENTANGLEMENT_AUDIT.md`).
3. **Preflight at pick/dispatch.** Endpoint reachable, model present, context
   window >= 32k. Refuse with a plain reason otherwise.
4. **Tool-use probe at setup.** One cheap round trip that requires a tool call.
   A model that never emits one is refused or clearly flagged.
5. **A validation table.** Run 2-3 coding models (30B-class and up) through the
   fizzbuzz task above plus one real repository task, and record in this file
   which pass, on what GPU, at what speed.

Optional: a leaner system prompt for local engines, to cut the context floor.

## Pointers

- Provider architecture: `docs/MULTI_PROVIDER_DESIGN.md`,
  `docs/PROVIDER_EXECUTION_CONTRACT.md`, `docs/PROVIDER_ARCHITECTURE_GUARD.md`.
- Engine resolution: `mc/engine_selection.py` (`resolve_engine`).
- Runtime registry: `get_runtime` / `_RUNTIMES` in `mc/agent_runtime.py`.

## Rules that apply

- No credential in a command line or config file; see `docs/SECRETS.md`.
- Nothing specific to your machine in a commit (paths, hostnames, keys).
- Tests that fail before your change and pass after; run the smokes under
  `tools/smoke/` for any `static/` change.
