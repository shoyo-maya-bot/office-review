"""レビュー結果・history.jsonl の読み込みを devtools-common の共通部品（devtools_common.review_results）に
置き換えた後も、office-review の動き（警告文・戻り値・形式の誤り）が変わらないことの確認.

quality-analytics も同じ共通部品で読むので、両ツールの規則が食い違わない。
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from devtools_common import review_results as rr

from review_harness import history, precheck, results

HEADER = (
    "| No | check_id | 重要度 | ロール | 箇所 | 指摘 | 根拠 | 無効となるケースの確認 |\n"
    "|---|---|---|---|---|---|---|---|\n"
)


class Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp)


class UsesCommonReaderTest(unittest.TestCase):
    def test_office_review_reads_with_the_common_reader(self):
        self.assertTrue(
            issubclass(results.ReviewItem, rr.ReviewItem)
        )  # 指紋だけ office-review の版（ロール・観点入り）
        self.assertIs(history.Resolved, rr.ResolvedHistory)
        self.assertIs(precheck.front_matter, rr.front_matter)
        self.assertEqual(results.COLUMNS, rr.COLUMNS)


class ReviewFileCompatTest(Tmp):
    def write(self, text: str, *, bom: bool = False, name: str = "2026-10-02_x_result.md") -> Path:
        p = self.tmp / name
        p.write_bytes(text.encode("utf-8-sig" if bom else "utf-8"))
        return p

    def test_bom_escapes_and_trailing_backslash(self):
        fm = "---\ntarget: inputs/api/x.md\ntype: api\nreviewed_at: 2026-10-02\n---\n\n"
        rows = (
            "| 1 | API-002 | MUST | dba | 3. 処理 | a \\| b の分岐 | C:\\\\data\\\\ | 該当しない |\n"
            "| 2 | NEW | Q | ops | 全体 | 末尾が \\\\ | x | y\\\\|\n"
            "| 3 | NEW | Q | ops | 全体 | 列が足りない |\n"
            "| x | NEW | Q | ops | 全体 | f | b | c |\n"
        )
        p = self.write(fm + HEADER + rows, bom=True)
        items, problems = results.parse(p)
        self.assertEqual([i.no for i in items], [1, 2])
        self.assertEqual(items[0].finding, "a | b の分岐")  # \| だけを戻す
        self.assertEqual(items[0].basis, "C:\\\\data\\\\")  # ほかのエスケープはそのまま（指紋が変わらないように）
        self.assertEqual(items[1].invalid_check, "y\\\\")  # 末尾の \\| の | は区切り
        self.assertEqual(
            (items[0].target, items[0].doc_type, items[0].reviewed_at), ("inputs/api/x.md", "api", "2026-10-02")
        )
        self.assertEqual(
            [(f.rule_id, f.message, f.location) for f in problems],
            [
                ("lint.table", "列数が 6 です", f"{p.name}:11"),
                ("lint.table", "No が数字ではありません", f"{p.name}:12"),
            ],
        )

    def test_meta_and_missing_table_messages(self):
        p = self.write("---\ntype: unknown\n---\n# x\n")
        items, problems = results.parse(p)
        self.assertEqual(items, [])
        self.assertEqual(
            [f.message for f in problems],
            [
                "Front Matter に target がありません",
                "Front Matter に reviewed_at がありません",
                "type 'unknown' は不明です",  # 種別は config の TYPES で確かめる
                "指摘の表（列: " + " / ".join(rr.COLUMNS) + "）がありません",
            ],
        )

    def test_load_all_reads_only_result_files_in_name_order(self):
        fm = "---\ntarget: t.md\ntype: api\nreviewed_at: 2026-10-02\n---\n"
        row = "| 1 | NEW | Q | ops | 全体 | {} | b | c |\n"
        self.write(fm + HEADER + row.format("後"), name="2026-10-03_x_result.md")
        self.write(fm + HEADER + row.format("先"), name="2026-10-01_x_result.md")
        self.write(fm + HEADER + row.format("対象外"), name="memo.md")
        self.assertEqual([i.finding for i in results.load_all(self.tmp)], ["先", "後"])


class HistoryCompatTest(Tmp):
    def test_broken_lines_warning_text_and_records(self):
        good = {"key": "a#1", "decision": "adopted", "check_id": "X"}
        p = self.tmp / "history.jsonl"
        p.write_bytes(
            (
                json.dumps(good) + "\n\n{ 壊れた\n[1]\n" + json.dumps({"decision": "maybe", "check_id": "X"}) + "\n"
            ).encode("utf-8-sig")
        )
        warned: list[str] = []
        self.assertEqual(history.load_records(p, warned.append), [good])
        self.assertEqual(len(warned), 3)
        self.assertTrue(warned[0].startswith("警告: history.jsonl:3 を飛ばしました（JSON として読めません: "))
        self.assertEqual(
            warned[1:],
            [
                "警告: history.jsonl:4 を飛ばしました（decision（adopted / rejected）と check_id が必要です）",
                "警告: history.jsonl:5 を飛ばしました（decision（adopted / rejected）と check_id が必要です）",
            ],
        )
        self.assertEqual(history.load_records(self.tmp / "none.jsonl"), [])


if __name__ == "__main__":
    unittest.main()
