"""Small, stdlib-only Markdown renderer for operator email, not full CommonMark.

Raw HTML is always text. Only the explicitly supported syntax creates markup;
all presentation is inline because email clients may discard stylesheets.
"""
from __future__ import annotations

import html
import re


_INLINE = re.compile(
    r"`([^`\n]+)`|(?<!!)\[([^\]\n]+)\]\((https?://[^\s)]+)\)"
    r"|\*\*([\s\S]+?)\*\*|(?<!\*)\*([^*\n]+)\*(?!\*)"
)
_HEADING = re.compile(r"^(#{1,3})\s+(.+)$")
_LIST = re.compile(r"^\s*(?:([-*])\s+|(\d+)\.\s+)(.+)$")
_FENCE = re.compile(r"^\s*(`{3,})[^`]*$")
_BOLD_ONLY = re.compile(r"\*\*((?:(?!\*\*)[\s\S])+)\*\*")
_CELL_STYLE = "border:1px solid #d8dee6;padding:7px 8px;vertical-align:top;"


def _inline(escaped: str) -> str:
    """Render already-escaped text; code spans never get interpreted again."""
    parts = []
    end = 0
    for match in _INLINE.finditer(escaped):
        parts.append(escaped[end:match.start()].replace("\n", "<br>\n"))
        code, label, url, bold, italic = match.groups()
        if code is not None:
            parts.append('<code style="font-family:monospace;background:#f1f3f5;'
                         f'padding:1px 4px;border-radius:3px;">{code}</code>')
        elif label is not None:
            parts.append(f'<a href="{url}" style="color:#185abc;'
                         f'text-decoration:underline;">{_inline(label)}</a>')
        elif bold is not None:
            parts.append(f"<strong>{_inline(bold)}</strong>")
        else:
            parts.append(f"<em>{_inline(italic)}</em>")
        end = match.end()
    parts.append(escaped[end:].replace("\n", "<br>\n"))
    return "".join(parts)


def _cells(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|") and not line.endswith(r"\|"):
        line = line[:-1]
    # A pipe in a code span or an escaped pipe is cell content, not a boundary.
    cells = []
    start = 0
    for match in re.finditer(r"`[^`]*`|\\\||\|", line):
        if match.group() == "|":
            cells.append(line[start:match.start()].strip().replace(r"\|", "|"))
            start = match.end()
    cells.append(line[start:].strip().replace(r"\|", "|"))
    return cells


def _table_at(lines: list[str], index: int) -> bool:
    if index + 1 >= len(lines) or "|" not in lines[index]:
        return False
    header, divider = _cells(lines[index]), _cells(lines[index + 1])
    return len(header) == len(divider) and all(
        re.fullmatch(r":?-{3,}:?", cell) for cell in divider)


def _starts_block(lines: list[str], index: int) -> bool:
    line = lines[index]
    return bool(_HEADING.match(line) or _LIST.match(line)
                or _FENCE.match(line) or line.strip() == "---"
                or _table_at(lines, index))


def render(md: str) -> str:
    """Return a complete HTML email with an escaped, supported Markdown body.

    Supports headings 1-3, paragraphs, emphasis, HTTP(S) links, flat lists,
    pipe tables, rules and backtick fences. Other syntax stays visible text.
    """
    # Escape before parsing so neither raw HTML nor attribute text can inject
    # tags. HTML generated below is never fed back through the Markdown parser.
    lines = html.escape(md).splitlines()
    blocks = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        fence = _FENCE.match(line)
        heading = _HEADING.match(line)
        item = _LIST.match(line)
        if fence:
            marker = fence.group(1)
            i += 1
            code = []
            while i < len(lines) and not re.fullmatch(
                    rf"\s*`{{{len(marker)},}}\s*", lines[i]):
                code.append(lines[i])
                i += 1
            if i < len(lines):
                i += 1
            blocks.append('<pre style="font-family:monospace;font-size:13px;'
                          'background:#f1f3f5;padding:12px;border:1px solid #d8dee6;'
                          'border-radius:4px;white-space:pre-wrap;overflow-wrap:anywhere;">'
                          '<code>' + "\n".join(code) + '</code></pre>')
        elif heading:
            level = len(heading.group(1))
            size = {1: 24, 2: 20, 3: 16}[level]
            blocks.append(f'<h{level} style="font-size:{size}px;line-height:1.3;'
                          f'margin:20px 0 10px;">{_inline(heading.group(2))}</h{level}>')
            i += 1
        elif line.strip() == "---":
            blocks.append('<hr style="border:0;border-top:1px solid #d8dee6;margin:20px 0;">')
            i += 1
        elif _table_at(lines, i):
            header = _cells(line)
            table = ['<div style="overflow-x:auto;margin:16px 0;">',
                     '<table style="border-collapse:collapse;font-family:Arial,Helvetica,sans-serif;'
                     'font-size:13px;line-height:1.4;text-align:left;">', '<thead><tr>']
            for cell in header:
                table.append(f'<th scope="col" style="{_CELL_STYLE}background:#e9eef5;'
                             f'font-weight:600;white-space:nowrap;">{_inline(cell)}</th>')
            table.append('</tr></thead><tbody>')
            i += 2
            row = 0
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                cells = _cells(lines[i])
                if len(cells) != len(header) or _starts_block(lines, i):
                    break  # Preserve malformed rows as text, never drop cells.
                color = "#ffffff" if row % 2 == 0 else "#f5f7fa"
                table.append('<tr>')
                for cell in cells:
                    table.append(f'<td style="{_CELL_STYLE}background:{color};">'
                                 f'{_inline(cell)}</td>')
                table.append('</tr>')
                row += 1
                i += 1
            table.append('</tbody></table></div>')
            blocks.append("".join(table))
        elif item:
            ordered = item.group(2) is not None
            tag = "ol" if ordered else "ul"
            start = f' start="{int(item.group(2))}"' if ordered else ""
            items = [f'<{tag}{start} style="margin:12px 0;padding-left:26px;">']
            while i < len(lines):
                current = _LIST.match(lines[i])
                if not current or (current.group(2) is not None) != ordered:
                    break
                items.append('<li style="margin:4px 0;">'
                             f'{_inline(current.group(3))}</li>')
                i += 1
            items.append(f'</{tag}>')
            blocks.append("".join(items))
        else:
            paragraph = [line]
            i += 1
            while i < len(lines) and lines[i].strip() and not _starts_block(lines, i):
                paragraph.append(lines[i])
                i += 1
            text = "\n".join(paragraph)
            bold = _BOLD_ONLY.fullmatch(text)
            if bold and len(html.unescape(bold.group(1))) <= 80:
                blocks.append('<h3 style="font-size:16px;line-height:1.3;'
                              f'margin:20px 0 10px;">{_inline(bold.group(1))}</h3>')
            else:
                blocks.append(f'<p style="margin:0 0 14px;">{_inline(text)}</p>')
    return ('<!doctype html><html><head><meta charset="utf-8"></head>'
            '<body style="margin:0;padding:0;background:#ffffff;color:#202124;">'
            '<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;'
            'line-height:1.55;max-width:860px;margin:0 auto;padding:24px;">'
            + "\n".join(blocks) + '</div></body></html>')
