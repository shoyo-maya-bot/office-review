"""Markdown（GFM）の表の読み取り. fieldcheck の入力と、harness precheck の両方で使う（パーサは 1 つ）.

doc-to-markdown の出力仕様（docs/OUTPUT_SPEC.md の「エスケープ」）に合わせて、エスケープを元の文字に戻す。

- `\\` の後ろの ASCII の記号（`\\` `|` `<` `#` `*` など）はその記号そのもの（CommonMark と同じ）
  例: `C:\\\\data` → `C:\\data`
- `\\` の後ろが記号でなければ、`\\` は普通の文字（`C:\\data` はそのまま）
- セルの区切りは、エスケープされていない `|`。`\\\\|` は「`\\` の後ろの区切り」
- セルの中の `<br>`（`<br/>` `<br />` も）は改行。`\\<br>` は文字の `<br>`
"""

from __future__ import annotations

import re
import string

_SEP = re.compile(r"^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")
_BR = re.compile(r"<br\s*/?>", re.I)
_PUNCT = set(string.punctuation)


def split_row(line: str) -> list[str]:
    """表の 1 行をセル（エスケープはそのまま）に分ける. 先頭・末尾の `|` は除く."""
    s = line.strip()
    cells: list[str] = []
    cur: list[str] = []
    i = 0
    while i < len(s):
        ch = s[i]
        if ch == "\\" and i + 1 < len(s):
            cur.append(s[i : i + 2])
            i += 2
            continue
        if ch == "|":
            cells.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
        i += 1
    cells.append("".join(cur))
    if s.startswith("|"):
        cells = cells[1:]
    if s.endswith("|") and not _escaped_at_end(s) and cells:
        cells = cells[:-1]
    return cells


def _escaped_at_end(s: str) -> bool:
    """末尾の | が \\ でエスケープされているか（直前の \\ の数が奇数）."""
    n = 0
    for ch in reversed(s[:-1]):
        if ch != "\\":
            break
        n += 1
    return n % 2 == 1


def unescape(cell: str) -> str:
    """セルの文字を元に戻す（エスケープを外し、<br> を改行に）. 前後の空白は除く."""
    s = cell.strip()
    out: list[str] = []
    i = 0
    while i < len(s):
        ch = s[i]
        if ch == "\\" and i + 1 < len(s) and s[i + 1] in _PUNCT:
            out.append(s[i + 1])
            i += 2
            continue
        if ch == "<":
            m = _BR.match(s, i)
            if m:
                out.append("\n")
                i = m.end()
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def cells(line: str) -> list[str]:
    return [unescape(c) for c in split_row(line)]


def is_separator(line: str) -> bool:
    return bool(_SEP.match(line.strip()))


def markdown_tables(text: str) -> list[tuple[str, list[str], list[list[str]]]]:
    """Markdown 中の GFM 表を (直前の見出し, 列名, 行) のリストで返す. セルはエスケープを戻した文字."""
    lines = text.splitlines()
    tables = []
    heading = ""
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("#"):
            heading = line.lstrip("#").strip()
        if line.strip().startswith("|") and i + 1 < len(lines) and is_separator(lines[i + 1]):
            header = cells(line)
            rows = []
            j = i + 2
            while j < len(lines) and lines[j].strip().startswith("|"):
                rows.append(cells(lines[j]))
                j += 1
            tables.append((heading, header, rows))
            i = j
            continue
        i += 1
    return tables
