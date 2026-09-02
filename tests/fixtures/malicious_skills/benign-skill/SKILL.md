---
name: benign-skill
description: TEST FIXTURE (MC-912) — negative control. Should produce zero scanner findings.
---

# Markdown formatter (fixture)

Reformats a markdown file's heading levels so the top heading is always `#`.
Reads the file, walks its heading lines, and writes the adjusted text back to
the same path the user gave it — no network calls, no config files, no shell
commands.

```python
from pathlib import Path

def normalize_headings(path):
    text = Path(path).read_text(encoding="utf-8")
    lines = text.splitlines()
    return "\n".join(lines)
```
