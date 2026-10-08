"""AI レビューの前に、機械で確かめられる観点（checks.yaml の machine）を確かめる."""

from __future__ import annotations

import re
from pathlib import Path

from devtools_common.report import Finding, Severity
from devtools_common.review_results import front_matter

from field_validation.mdtable import markdown_tables

from .checks import Catalog

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.M)

SEVERITY_MAP = {"MUST": Severity.ERROR, "WANT": Severity.WARNING, "NITS": Severity.INFO}


def _norm(s: str) -> str:
    return re.sub(r"[\s*`]", "", s)


def table_headers(text: str) -> list[list[str]]:
    """表の見出し行（列名）. 表の読み取りは fieldcheck と同じパーサ（field_validation.mdtable）を使う."""
    return [[_norm(c) for c in header] for _, header, _ in markdown_tables(text)]


def run(path: Path, doc_type: str, catalog: Catalog) -> list[Finding]:
    text = path.read_text(encoding="utf-8-sig")  # BOM 付きでも Front Matter を読めるように
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
            missing = [k for k in m.get("keys", []) if not fm.get(k)]
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
