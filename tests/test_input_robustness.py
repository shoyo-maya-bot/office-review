"""採点で出た入力の扱いの指摘の再現テスト.

文字コード・Markdown のエスケープ・表の行の過不足・BOM・壊れた判定ファイル.
"""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import tempfile
import textwrap
import unittest
from pathlib import Path

from devtools_common.cli import run_main

from field_validation import cli as fv_cli
from field_validation.loaders import open_source
from field_validation.mdtable import cells, markdown_tables, split_row
from review_harness import cli as rh_cli
from review_harness import history, precheck

ROOT = Path(__file__).resolve().parent.parent
RULES = "rules:\n  - {id: R, type: required, fields: [名前]}\n"


def run(main, *args: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = run_main(main, list(args))
    return rc, out.getvalue(), err.getvalue()


class Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def write(self, name: str, text: str, encoding: str = "utf-8") -> Path:
        p = self.tmp / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(text), encoding=encoding)
        return p


class EncodingTest(Tmp):
    """CSV の既定の文字コード: UTF-8（BOM 可）→ cp932 の順に自動で試す."""

    def test_shift_jis_csv_without_encoding(self):
        c = self.tmp / "sj.csv"
        c.write_bytes("名前,金額\n山田,100\n".encode("cp932"))
        src = open_source(c)  # 以前は UnicodeDecodeError（終了コード 3）
        self.assertEqual(list(src.records), [("row 1", {"名前": "山田", "金額": "100"})])
        rules = self.write("r.yaml", RULES)
        rc, out, err = run(fv_cli.main, "validate", str(c), "-r", str(rules), "-f", "json")
        self.assertEqual(rc, 0, err)

    def test_undecodable_is_usage_error_with_hint(self):
        c = self.tmp / "bad.csv"
        c.write_bytes(b"\x83\xff\xfe,\x80\x81\n")  # UTF-8 でも cp932 でもない
        rules = self.write("r.yaml", RULES)
        rc, _, err = run(fv_cli.main, "validate", str(c), "-r", str(rules))
        self.assertEqual(rc, 2)
        self.assertIn("--encoding", err)

    def test_wrong_explicit_encoding_is_usage_error(self):
        c = self.tmp / "sj.csv"
        c.write_bytes("名前\n山田\n".encode("cp932"))
        rules = self.write("r.yaml", RULES)
        rc, _, err = run(fv_cli.main, "validate", str(c), "-r", str(rules), "--encoding", "utf-8")
        self.assertEqual(rc, 2)
        self.assertIn("--encoding", err)

    def test_utf8_bom_markdown(self):
        p = self.tmp / "a.md"
        p.write_bytes("| 名前 |\n|---|\n| 山田 |\n".encode("utf-8-sig"))
        self.assertEqual(open_source(p).columns, ["名前"])


class MarkdownEscapeTest(Tmp):
    """doc2md の出力仕様（docs/OUTPUT_SPEC.md の「エスケープ」）どおりに戻す."""

    def test_backslash_is_restored(self):
        # 採点で再現した入力: C:\data\x は doc2md で C:\\data\\x になる。以前はそのまま読んでいた
        md = "| パス | 備考 |\n|---|---|\n| C:\\\\data\\\\x | a\\|b |\n"
        self.assertEqual(markdown_tables(md)[0][2], [["C:\\data\\x", "a|b"]])

    def test_round_trip_of_doc2md_cell_escapes(self):
        originals = [
            "C:\\data\\x",
            "a|b",
            "<b>太字</b>",
            "1行目\n2行目",
            "末尾\\",
            "\\|",
            "# 見出しではない",
            "*強調ではない*",
        ]
        # doc2md の表のセル: \ → \\、| → \|、< → \<、改行 → <br>（Markdown の記号の前に \ を付けたもの）
        escaped = [
            o.replace("\\", "\\\\").replace("|", "\\|").replace("<", "\\<").replace("\n", "<br>") for o in originals
        ]
        escaped[6] = "\\# 見出しではない"
        escaped[7] = "\\*強調ではない\\*"
        line = "| " + " | ".join(escaped) + " |"
        self.assertEqual(cells(line), originals)

    def test_split_respects_escaped_backslash_before_pipe(self):
        self.assertEqual(split_row("| a\\\\| b |"), [" a\\\\", " b "])  # \\ の後ろの | は区切り
        self.assertEqual(split_row("| a\\| b |"), [" a\\| b "])
        self.assertEqual(cells("| x\\|y | 1 |"), ["x|y", "1"])

    def test_backslash_before_letter_is_kept(self):
        self.assertEqual(cells("| C:\\data |"), ["C:\\data"])  # 古い doc2md の出力（エスケープなし）もそのまま


class RowShapeTest(Tmp):
    """セル数が見出しと合わない行: 黙って切り詰めず、警告を出す（不足は空として読む）."""

    def test_short_and_long_rows_are_warned(self):
        md = self.write("a.md", "| 名前 | 金額 | 備考 |\n|---|---|---|\n| 山田 | 100 |\n| 佐藤 | 200 | x | 余り |\n")
        src = open_source(md)
        recs = list(src.records)
        self.assertEqual(recs[0][1], {"名前": "山田", "金額": "100", "備考": ""})
        self.assertEqual(recs[1][1], {"名前": "佐藤", "金額": "200", "備考": "x"})
        self.assertEqual(
            [(w.rule_id, w.location) for w in src.warnings],
            [("input.row_shape", "row 1"), ("input.row_shape", "row 2")],
        )
        self.assertIn("余り", src.warnings[1].actual)
        rules = self.write("r.yaml", RULES)
        rc, out, _ = run(fv_cli.main, "validate", str(md), "-r", str(rules), "-f", "json")
        self.assertEqual(rc, 0)  # 警告は既定の --fail-on error では失敗にしない
        self.assertEqual([f["rule_id"] for f in json.loads(out)["findings"]], ["input.row_shape", "input.row_shape"])
        rc, _, _ = run(fv_cli.main, "validate", str(md), "-r", str(rules), "--fail-on", "warning")
        self.assertEqual(rc, 1)


class HarnessBase(Tmp):
    def setUp(self):
        super().setUp()
        for d in ["config", "docs", "inputs", "reviews"]:
            shutil.copytree(ROOT / d, self.tmp / d)
        (self.tmp / "data").mkdir()

    def harness(self, *args: str) -> tuple[int, str, str]:
        return run(rh_cli.main, "--root", str(self.tmp), *args)


class PrecheckBomTest(HarnessBase):
    def test_bom_markdown_front_matter_is_read(self):
        doc = self.tmp / "inputs/api/order-api.md"
        doc.write_bytes(doc.read_text(encoding="utf-8").encode("utf-8-sig"))
        rc, out, _ = self.harness("precheck", str(doc), "--type", "api", "-f", "json")
        self.assertEqual((rc, json.loads(out)["findings"]), (0, []))

    def test_precheck_uses_the_same_table_parser(self):
        md = "| 項目 \\| 名 | `型` |\n|---|---|\n| a | b |\n"
        self.assertEqual(precheck.table_headers(md), [["項目|名", "型"]])


class TriageInputTest(HarnessBase):
    def test_missing_or_broken_triage_json_is_usage_error(self):
        rc, _, err = self.harness("triage", str(self.tmp / "nope.json"))
        self.assertEqual(rc, 2)
        self.assertIn("見つかりません", err)
        bad = self.write("triage.json", "{ 壊れている")
        rc, _, err = self.harness("triage", str(bad))
        self.assertEqual(rc, 2)
        self.assertIn("JSON として読めません", err)
        wrong = self.write("t2.json", '["fp:1"]')
        self.assertEqual(self.harness("triage", str(wrong))[0], 2)

    def test_broken_history_line_is_skipped_with_line_number(self):
        good = {"key": "a#1", "decision": "adopted", "check_id": "API-005", "severity": "WANT", "finding": "x"}
        (self.tmp / "data/history.jsonl").write_text(
            json.dumps(good) + "\n{ 壊れた行\n" + json.dumps({"key": "b#1"}) + "\n", encoding="utf-8"
        )
        warned: list[str] = []
        recs = history.load_records(self.tmp / "data/history.jsonl", warned.append)
        self.assertEqual(recs, [good])
        self.assertEqual(len(warned), 2)
        self.assertIn("history.jsonl:2", warned[0])
        self.assertIn("history.jsonl:3", warned[1])
        rc, _, err = self.harness("accumulate")  # 以前は 1 行の破損で終了コード 3
        self.assertEqual(rc, 0)
        self.assertIn("history.jsonl:2", err)
        self.assertEqual(self.harness("dashboard")[0], 0)


class CsvShapeTest(Tmp):
    """CSV: 列より多いセルを黙って捨てない（警告）. 列名の重複で値を黙って上書きしない（使い方の誤り）."""

    def test_extra_cells_are_warned(self):
        c = self.write("a.csv", "名前,金額\n山田,100\n佐藤,1,200\n")
        src = open_source(c)
        recs = list(src.records)
        self.assertEqual(recs[1][1], {"名前": "佐藤", "金額": "1"})  # None キー（余りのセル）は入れない
        self.assertEqual([(w.rule_id, w.location) for w in src.warnings], [("input.row_shape", "row 2")])
        self.assertIn("200", src.warnings[0].actual)
        self.assertIn("3 セル", src.warnings[0].actual)
        rules = self.write("r.yaml", RULES)
        rc, out, err = run(fv_cli.main, "validate", str(c), "-r", str(rules), "-f", "json")
        self.assertEqual(rc, 0)
        self.assertEqual(
            [(f["rule_id"], f["location"]) for f in json.loads(out)["findings"]], [("input.row_shape", "row 2")]
        )
        rc, _, _ = run(fv_cli.main, "validate", str(c), "-r", str(rules), "--fail-on", "warning")
        self.assertEqual(rc, 1)

    def test_tsv_extra_cells_are_warned(self):
        c = self.write("a.tsv", "名前\t金額\n山田\t100\tx\n")
        src = open_source(c)
        list(src.records)
        self.assertEqual([w.location for w in src.warnings], ["row 1"])

    def test_duplicate_column_names_are_usage_error(self):
        c = self.write("d.csv", "名前,金額, 金額\n山田,100,200\n")
        rules = self.write("r.yaml", RULES)
        rc, _, err = run(fv_cli.main, "validate", str(c), "-r", str(rules))
        self.assertEqual(rc, 2)
        self.assertIn("列名が重複しています: 金額", err)

    def test_duplicate_column_names_in_markdown_table_are_usage_error(self):
        md = self.write("d.md", "| 名前 | 名前 |\n|---|---|\n| a | b |\n")
        rules = self.write("r.yaml", RULES)
        rc, _, err = run(fv_cli.main, "validate", str(md), "-r", str(rules))
        self.assertEqual(rc, 2)
        self.assertIn("列名が重複しています: 名前", err)


class AccumulateEscapeTest(HarnessBase):
    """accumulate の表: セルの中の | や改行で列がずれない."""

    def test_pipe_and_newline_in_candidates_are_escaped(self):
        rec = {
            "key": "fp:0000000000000001",
            "decision": "adopted",
            "check_id": "NEW",
            "severity": "MUST",
            "finding": "a | b の\n分岐が無い",
            "doc_type": "api",
            "role": "dev|ops",
            "triaged_at": "2026-10-08",
        }
        (self.tmp / "data/history.jsonl").write_text(json.dumps(rec, ensure_ascii=False) + "\n", encoding="utf-8")
        rc, _, _ = self.harness("accumulate")
        self.assertEqual(rc, 0)
        text = (self.tmp / "docs/check-candidates.md").read_text("utf-8")
        row = [ln for ln in text.splitlines() if "分岐が無い" in ln]
        self.assertEqual(row, ["| a \\| b の<br>分岐が無い | api | dev\\|ops | MUST | 2026-10-08 |"])
        self.assertEqual(len(split_row(row[0])), 5)  # 区切りは 5 列のまま
        self.assertEqual(cells(row[0])[0], "a | b の\n分岐が無い")  # 読み戻すと元の値


if __name__ == "__main__":
    unittest.main()
