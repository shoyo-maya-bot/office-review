"""レビュー結果ファイル（reviews/*.md）の読み込みと形式チェック（review-lint）.

形式:

    ---
    target: inputs/api/order-api.md
    type: api
    reviewed_at: 2026-10-02
    ---
    # レビュー結果: order-api

    | No | check_id | 重要度 | ロール | 箇所 | 指摘 | 根拠 | 無効となるケースの確認 |
    |---|---|---|---|---|---|---|---|
    | 1 | API-002 | MUST | spec_manager | 3. 入力 | … | … | 入力ありのため該当しない |

check_id が NEW の指摘は「既存の観点に無い指摘」で、accumulate がチェックリストの追加候補にする。
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from devtools_common.report import Finding, Severity

from .checks import SEVERITIES, TYPES, Catalog
from .precheck import front_matter

COLUMNS = ["No", "check_id", "重要度", "ロール", "箇所", "指摘", "根拠", "無効となるケースの確認"]
REQUIRED_META = ["target", "type", "reviewed_at"]


@dataclass
class ReviewItem:
    file: str
    target: str
    doc_type: str
    reviewed_at: str
    no: int
    check_id: str
    severity: str
    role: str
    location: str
    finding: str
    basis: str
    invalid_check: str

    @property
    def key(self) -> str:
        """ファイル名 + 番号（旧形式のキー）. 再レビューで No がずれると別の指摘を指すので、判定の結び付けには使わない."""
        return f"{self.file}#{self.no}"

    @property
    def fingerprint(self) -> str:
        """指摘の指紋. 対象・箇所・指摘の本文から作り、No やファイル名（レビュー日）が変わっても同じ値になる."""
        parts = [normalize(self.target or self.file), normalize(self.location), normalize(self.finding)]
        return "fp:" + hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:16]


def normalize(text: str) -> str:
    """指紋用の正規化: 全角・半角の統一（NFKC）、空白の連続を 1 つに、前後の空白を除く、英字を小文字に."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip().lower()


def _cells(line: str) -> list[str]:
    body = line.strip()[1:-1]
    return [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", body)]


def parse(path: Path) -> tuple[list[ReviewItem], list[Finding]]:
    text = path.read_text(encoding="utf-8")
    meta = front_matter(text)
    problems: list[Finding] = []
    loc = path.name
    for k in REQUIRED_META:
        if not meta.get(k):
            problems.append(Finding("lint.meta", Severity.ERROR, f"Front Matter に {k} がありません", location=loc))
    if meta.get("type") and meta["type"] not in TYPES:
        problems.append(Finding("lint.meta", Severity.ERROR, f"type '{meta['type']}' は不明です", location=loc))
    lines = text.splitlines()
    header_at = next((i for i, ln in enumerate(lines) if ln.startswith("|") and _cells(ln) == COLUMNS), None)
    if header_at is None:
        problems.append(
            Finding(
                "lint.table", Severity.ERROR, "指摘の表（列: " + " / ".join(COLUMNS) + "）がありません", location=loc
            )
        )
        return [], problems
    items: list[ReviewItem] = []
    for i in range(header_at + 2, len(lines)):
        ln = lines[i]
        if not ln.startswith("|"):
            break
        cells = _cells(ln)
        if len(cells) != len(COLUMNS):
            problems.append(
                Finding("lint.table", Severity.ERROR, f"列数が {len(cells)} です", location=f"{loc}:{i + 1}")
            )
            continue
        try:
            no = int(cells[0])
        except ValueError:
            problems.append(Finding("lint.table", Severity.ERROR, "No が数字ではありません", location=f"{loc}:{i + 1}"))
            continue
        reviewed = meta.get("reviewed_at", "")
        if isinstance(reviewed, dt.date):
            reviewed = reviewed.isoformat()
        items.append(
            ReviewItem(path.name, str(meta.get("target", "")), str(meta.get("type", "")), str(reviewed), no, *cells[1:])
        )
    return items, problems


def lint(path: Path, catalog: Catalog) -> list[Finding]:
    items, problems = parse(path)
    checks = catalog.by_id()
    for expected_no, it in enumerate(items, 1):
        loc = f"{path.name}#{it.no}"
        if it.no != expected_no:
            problems.append(
                Finding(
                    "lint.numbering", Severity.WARNING, f"No が連番ではありません（期待 {expected_no}）", location=loc
                )
            )
        if it.severity not in SEVERITIES:
            problems.append(
                Finding(
                    "lint.severity",
                    Severity.ERROR,
                    "重要度は MUST / WANT / NITS / Q です",
                    location=loc,
                    actual=it.severity,
                )
            )
        if it.check_id != "NEW":
            c = checks.get(it.check_id)
            if c is None:
                problems.append(
                    Finding(
                        "lint.check_id",
                        Severity.ERROR,
                        "check_id が config/checks.yaml にありません（新しい観点なら NEW）",
                        location=loc,
                        actual=it.check_id,
                    )
                )
            elif it.doc_type and not c.applies_to(it.doc_type):
                problems.append(
                    Finding(
                        "lint.check_type",
                        Severity.ERROR,
                        f"{it.check_id} は種別 {it.doc_type} の観点ではありません",
                        location=loc,
                    )
                )
            if not it.invalid_check:
                problems.append(
                    Finding(
                        "lint.invalid_when",
                        Severity.ERROR,
                        "「無効となるケース」に当たらないことの確認が空です",
                        location=loc,
                    )
                )
        if it.role not in catalog.roles:
            problems.append(
                Finding(
                    "lint.role",
                    Severity.ERROR,
                    "ロールが config/checks.yaml の roles にありません",
                    location=loc,
                    actual=it.role,
                )
            )
        if not it.basis:
            problems.append(
                Finding("lint.basis", Severity.ERROR, "根拠（対象の記載箇所・参照資料）が空です", location=loc)
            )
        if not it.finding:
            problems.append(Finding("lint.finding", Severity.ERROR, "指摘が空です", location=loc))
    return problems


def load_all(reviews_dir: Path) -> list[ReviewItem]:
    items: list[ReviewItem] = []
    for p in sorted(reviews_dir.glob("*_result.md")):
        got, _ = parse(p)
        items += got
    return items
