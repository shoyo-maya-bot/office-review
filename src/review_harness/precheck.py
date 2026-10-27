"""AI レビューの前に、機械で確かめられる観点（checks.yaml の machine）を確かめる."""

from __future__ import annotations

import re
from pathlib import Path

from devtools_common.cli import UsageError
from devtools_common.report import Finding, Severity
from devtools_common.review_results import front_matter

from field_validation.loaders import detect_encoding
from field_validation.mdtable import markdown_tables

from .checks import Catalog

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.M)

SEVERITY_MAP = {"MUST": Severity.ERROR, "WANT": Severity.WARNING, "NITS": Severity.INFO}


def _norm(s: str) -> str:
    return re.sub(r"[\s*`]", "", s)


def key_alternatives(key: str) -> list[str]:
    """front_matter の keys の 1 項目. 「version|版数」はどちらかがあればよい."""
    return [a.strip() for a in key.split("|")]


def table_headers(text: str) -> list[list[str]]:
    """表の見出し行（列名）. 表の読み取りは fieldcheck と同じパーサ（field_validation.mdtable）を使う."""
    return [[_norm(c) for c in header] for _, header, _ in markdown_tables(text)]


def read_document(path: Path) -> str:
    """対象の Markdown を読む. 文字コードは fieldcheck と同じ（UTF-8（BOM 付き可）→ Shift_JIS（cp932）の順に試す）.

    BOM 付きでも Front Matter を読める. どちらでも読めなければ UsageError（終了コード 2. 以前は終了コード 3）。
    """
    try:
        encoding = detect_encoding(path, None)
    except UsageError:  # fieldcheck の文は --encoding を案内するが、precheck にその引数は無い
        raise UsageError(
            f"{path.name}: 文字コードを判別できません（UTF-8 でも Shift_JIS でもありません）。"
            "UTF-8 で保存し直してください"
        ) from None
    return path.read_text(encoding=encoding)


def run(path: Path, doc_type: str, catalog: Catalog) -> list[Finding]:
    text = read_document(path)
    fm = front_matter(text)
    headings = [h.group(2) for h in _HEADING.finditer(text)]
    tables = table_headers(text)
    findings: list[Finding] = []
    for c in catalog.for_type(doc_type):
        m = c.machine
        if not m:
            continue
        sev = SEVERITY_MAP[c.severity]
        kind = m.get("kind")
        if kind == "front_matter":
            missing = [
                k.replace("|", " か ") for k in m.get("keys", []) if not any(fm.get(a) for a in key_alternatives(k))
            ]
            if missing:
                findings.append(
                    Finding(
                        c.id, sev, f"{c.title}（Front Matter に {', '.join(missing)} がありません）", location=path.name
                    )
                )
        elif kind == "heading":
            rx = re.compile(m["pattern"])
            if not any(rx.search(h) for h in headings):
                findings.append(
                    Finding(
                        c.id, sev, f"{c.title}（該当する見出しがありません）", location=path.name, expected=m["pattern"]
                    )
                )
        elif kind == "table_columns":
            want = [_norm(x) for x in m["columns"]]
            if not any(all(any(w in col for col in head) for w in want) for head in tables):
                findings.append(
                    Finding(
                        c.id,
                        sev,
                        f"{c.title}（列 {'・'.join(m['columns'])} を持つ表がありません）",
                        location=path.name,
                        expected="・".join(m["columns"]),
                    )
                )
    return findings
