"""The frontmatter block of a newly minted topic note.

`write_topic_note` (mc/memory.py) used to build this block by string
concatenation, escaping only `"` in the description. The automated mint
passes up to 160 characters of a backlog text or a Hivemind title with its
newlines kept, so a description of `first line\\n---\\nrest` closed the block
early: everything after it, the server's `origin:` stamp included, became
body text, the parsed origin was empty, and the note was delivered to an
unattended consumer (MC-1179 pass 3, ML13; learning-system rail 2).

The caller-reachable fields now go through `skills.dump_skill_md`, the same
dumper `write_position` uses: a multi-line value becomes an indented block
scalar, which can hold neither a fence nor a top-level key.

Three things here are deliberate, and each closes a way back in:

* **Every line separator counts, not only `\\n`.** The vault parser splits
  the block with `str.splitlines()`, which also breaks on `\\r`, `\\x0b`,
  `\\x0c`, `\\x1c`-`\\x1e`, `\\x85`, U+2028 and U+2029 (the same reason
  `_rewrite_frontmatter_field` refuses a multi-line value, ML5). The dumper
  tests `\\n` alone, so each value is re-joined on `\\n` first.
* **`origin` is the last scalar key.** The parser keeps the last value of a
  repeated key, so the server's stamp wins over anything above it.
* **`metadata:` and `generated:` are nested maps**, which the dumper cannot
  express and several readers match by their indented lines. They are written
  after the dumped block with each value forced onto one line.

Readers of this block are pinned, old layout and new, by
tests/test_topic_note_frontmatter_readers.py.
"""
from mc import skills as _skills

__all__ = ['topic_note_head']

_FENCE = '---\n'


def _parser_lines(value):
    """`value` with every separator the vault parser splits on turned into
    `\\n`, so the dumper sees the same line count the parser will."""
    return '\n'.join(str(value).splitlines())


def _one_line(value):
    """`value` on a single line: any run of whitespace, line separators
    included, becomes one space."""
    return ' '.join(str(value).split())


def topic_note_head(*, name, description, note_type, origin, generated_by,
                    generated_at, triggers='', supersedes='', mint_candidates=''):
    """-> the `---` ... `---\\n` block, ready to have the body appended.

    Empty `triggers`, `supersedes` and `mint_candidates` are left out, as
    before. Never raises on any string input.
    """
    front = {'name': name, 'description': description}
    if triggers:
        front['triggers'] = triggers
    if supersedes:
        front['supersedes'] = supersedes
    if mint_candidates:
        front['mint_candidates'] = mint_candidates
    front['origin'] = origin        # last: the parser keeps the last value
    block = _skills.dump_skill_md(
        {k: _parser_lines(v) for k, v in front.items()}, '')
    if not block.endswith(_FENCE):  # the dumper's contract; never guess past it
        raise ValueError('frontmatter dump did not end with a closing fence')
    server = ('metadata:\n'
              f'  type: {_one_line(note_type)}\n'
              'generated:\n'
              f'  by: {_one_line(generated_by)}\n'
              f'  at: {_one_line(generated_at)}\n')
    return block[:-len(_FENCE)] + server + _FENCE
