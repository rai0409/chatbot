"""Joining the physical lines of a PDF / OCR page that belong to one wrapped sentence.

A PDF page has a line break where the layout wrapped, not where the writer ended a sentence; a Japanese sentence
broken like that is joined without a space. The rule is deliberately cautious, because a wrong join destroys
structure (a title glued to the first article, a list of names turned into one word):

- a line that ends a sentence (。.!?！？) is not joined to the next one
- bullet / step markers (・, ①, 1., ※ ...) and question / answer / article markers (Q1., 回答：, 第3条) start a new line
- numeric lines and table-like lines (columns of spaces, tabs, pipes) are never joined
- a short line (at most SHORT_LINE_CHARS) is a heading or an item, not the first half of a wrapped line,
  unless it ends in a particle or a comma ("申請の流れを" is unfinished)

Office formats (docx, pptx, xlsx, csv) keep their paragraphs as lines: joining is only for PDF / OCR text.
"""

from __future__ import annotations

import re
from typing import Optional

BULLET_RE = re.compile(r"^(\s*([0-9]+[\.)]|[①-⑳㉑-㉟㊱-㊿]|[-•・※▶]))")
_JA_CHAR = r"[ぁ-んァ-ヴー一-龥々〆〤、，「」『』（）]"
JA_EDGE_RE = re.compile(_JA_CHAR)
# A line that starts a question, an answer or an article starts something new: never the continuation of the
# line above (an answer without a final 。 used to swallow the next "Q2." line).
_NEW_BLOCK_RE = re.compile(
    r"^\s*(?:(?:Q|A|質問|回答|問|答)\s*\d{0,5}\s*[:.、)：．）]|(?:質問|回答|問|答)\s*\d{0,5}\s+|第\s*[0-9一二三四五六七八九十百]+\s*[章節条項款])",
    re.IGNORECASE,
)
# A line made only of numbers, units and separators ("100", "2024/4/1", "12,000円").
_NUMERIC_LINE_RE = re.compile(r"^[\d,.\-−+%/:()（）年月日円万億 ]+$")
# Two or more inner spaces or a tab usually mean table columns.
_TABLE_LINE_RE = re.compile(r"\S(?: {2,}|\t)\S|\|")
SHORT_LINE_CHARS = 12
SENTENCE_END = "。.!?！？"
# A line that ends in a particle or a comma is unfinished, however short it is: headings and list items do not.
CONTINUATION = "をにはがのとでへもやか、，"


def is_numeric_line(line: str) -> bool:
    return bool(_NUMERIC_LINE_RE.fullmatch(line.strip()))


def join_separator(prev: str, nxt: str) -> Optional[str]:
    """Separator used to join two physical lines, "\\0" to join and drop a hyphen, or None to keep them apart.

    Japanese on both sides joins without a space; anything else joins with a space so "100" and "200" never
    become "100200"."""
    if not prev or not nxt:
        return None
    if prev[-1] in SENTENCE_END:
        return None
    if BULLET_RE.match(nxt) or _NEW_BLOCK_RE.match(nxt):
        return None
    if is_numeric_line(prev) or is_numeric_line(nxt):
        return None
    if _TABLE_LINE_RE.search(prev) or _TABLE_LINE_RE.search(nxt):
        return None
    if len(prev) <= SHORT_LINE_CHARS and prev[-1] not in CONTINUATION:
        return None
    if JA_EDGE_RE.fullmatch(prev[-1]) and JA_EDGE_RE.fullmatch(nxt[0]):
        return ""
    if prev.endswith("-") and len(prev) >= 2 and prev[-2].isascii() and prev[-2].isalpha() and nxt[0].isascii() and nxt[0].isalpha():
        return "\0"  # English word broken by a hyphen: drop the hyphen
    return " "


def join_wrapped_lines(text: str) -> tuple[str, int]:
    """Join the wrapped lines inside every paragraph of `text`; returns (text, number of joins)."""
    joins = 0
    out_lines = []
    buf = ""
    for raw in text.split("\n"):
        line = raw.strip()
        if not line:
            if buf:
                out_lines.append(buf)
                buf = ""
            out_lines.append("")
            continue
        if not buf:
            buf = line
            continue
        sep = join_separator(buf, line)
        if sep is None:
            out_lines.append(buf)
            buf = line
        elif sep == "\0":
            buf, joins = buf[:-1] + line, joins + 1
        else:
            buf, joins = buf + sep + line, joins + 1
    if buf:
        out_lines.append(buf)
    return "\n".join(out_lines), joins
