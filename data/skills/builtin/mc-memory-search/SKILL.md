---
name: mc-memory-search
description: Search this Clayrune project's accumulated memory — prior decisions, conventions, gotchas, and past session outcomes — that are NOT in the auto-loaded MEMORY.md index. TRIGGER when you hit an unknown about this project's history ("have we done X before?", "why is Y this way?", "what was decided about Z?"), when you're about to touch a subsystem and want prior art, or when the user references earlier work you have no context on. Use this BEFORE guessing or re-deriving something that may already be solved.
---

# Project Memory Search

The auto-loaded `MEMORY.md` is only a curated pointer index — the detail
(topic files, the session log, the archive) is NOT in your context. This skill
does a ranked search over that full corpus and is the reliable way to recover
prior project knowledge mid-task.

Relevant memory for your current task is also auto-surfaced in your system
context under **"RELEVANT MEMORY"** — check there first. Use this skill when
that's absent or insufficient, or when you need to dig into a *different*
topic than the task started on.

## How to call it

You know your Clayrune project id and port from the SYSTEM block (the same id
used by the backlog/process curls). Then:

```
curl -s "http://localhost:5199/api/project/<PROJECT_ID>/memory/search?q=<QUERY>&k=5"
```

- `q` — space-separated keywords (URL-encode spaces as `%20` or `+`). Use the
  concrete nouns of what you're unsure about: subsystem name, file, feature,
  decision topic. Terms shorter than 3 chars are ignored.
- `k` — max results (default 3; use 5–8 when exploring).

Returns a JSON array, ranked best-first — curated corpus hits, THEN a cold
tier of real session-transcript excerpts appended underneath:

```json
[{"file": "arch_sse_slot_management.md", "score": 7,
  "snippet": "Chromium caps 6 connections per origin; close SSE on …"},
 {"tier": "cold", "file": "session:c4215ce4-…", "session_id": "c4215ce4-…",
  "role": "assistant", "timestamp": "2026-08-31T23:22:04Z",
  "snippet": "…the actual conversation text…", "score": -3.21}]
```

## How to use the results

1. The `snippet` is a preview, not the whole story. If a hit looks relevant,
   **open the referenced file** for full detail:
   - `<file>.md` → a topic file in the project's memory dir — Read it.
   - `MEMORY.md#managed` → a session-log entry inside MEMORY.md's managed
     region (you already have MEMORY.md loaded — re-read that section).
   - `MEMORY_ARCHIVE.md` → an archived older entry — Read that file.
   - `tier: "cold"` → a real message from a past session, NOT a curated note
     — a search over transcripts you already have, no promotion step. Use
     `session_id` to look the conversation up (e.g.
     `claude -r <session_id>`, or `/api/project/<id>/transcript/<session_id>`)
     if you need the surrounding context, not just the excerpt.
2. Treat hits as breadcrumbs: confirm in the file before acting on a snippet.
3. No results (empty array) means the corpus has nothing on those terms — try
   different keywords once, then proceed; don't loop.
4. Cold hits are unreviewed — they are whatever was actually said, not
   something a human curated. Weight them as "this came up before," not as an
   endorsed decision the way a topic file or position is.

## When NOT to use

- For the *current* task's obvious context — that's already auto-surfaced.
- As a substitute for reading the code: memory holds decisions and gotchas,
  not the source of truth for what the code currently does.
