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

from pathlib import Path

from devtools_common import review_results as rr
from devtools_common.report import Finding, Severity

from .checks import SEVERITIES, TYPES, Catalog

# 読み込み（BOM・Front Matter・表のエスケープ）は devtools-common の共通部品で行う（quality-analytics と同じ規則）
COLUMNS = rr.COLUMNS
REQUIRED_META = rr.REQUIRED_META
normalize = rr.normalize
ReviewItem = rr.ReviewItem


def parse(path: Path) -> tuple[list[ReviewItem], list[Finding]]:
    """レビュー結果 1 ファイル → (指摘の行, 形式の誤り). type は config の種別（TYPES）で確かめる."""
    return rr.parse_review_file(path, doc_types=TYPES)


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
    return rr.load_review_dir(reviews_dir)
