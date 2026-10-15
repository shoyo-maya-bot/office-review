"""expression ルールの式: 文字列・リストの繰り返しと連結の長さ、文字列の % 書式、冪に上限・制限がある.

以前は `"x" * 100000000000` が MemoryError（終了コード 3、または長時間メモリを使い切る）になっていた.
"""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from devtools_common.cli import run_main

from field_validation import cli
from field_validation.expr import MAX_SEQUENCE_LEN, Expression, ExpressionError


class ExpressionLimitsTest(unittest.TestCase):
    def evaluate(self, source: str, rec: dict | None = None):
        return Expression(source).evaluate(rec or {})

    def test_huge_string_repeat_is_rejected(self):
        for src in ('"x" * 100000000000 == ""', '100000000000 * "x" == ""', f'"ab" * {MAX_SEQUENCE_LEN} == ""'):
            with self.subTest(src=src), self.assertRaisesRegex(ExpressionError, "長すぎます"):
                self.evaluate(src)

    def test_huge_list_repeat_and_concat_are_rejected(self):
        with self.assertRaisesRegex(ExpressionError, "長すぎます"):
            self.evaluate("len([1] * 100000000000) == 0")
        half = MAX_SEQUENCE_LEN // 2 + 1
        with self.assertRaisesRegex(ExpressionError, "長すぎます"):
            self.evaluate(f'("x" * {half}) + ("y" * {half}) == ""')

    def test_field_value_repeat_is_rejected(self):
        with self.assertRaisesRegex(ExpressionError, "長すぎます"):
            self.evaluate("名前 * 100000000000 == 名前", {"名前": "abc"})

    def test_string_percent_format_is_rejected(self):
        with self.assertRaisesRegex(ExpressionError, "文字列の % 書式"):
            self.evaluate('"%0999999999d" % 1 == ""')

    def test_power_is_not_allowed(self):
        with self.assertRaisesRegex(ExpressionError, "Pow"):
            Expression("2 ** 100000000000 == 0")

    def test_small_values_still_work(self):
        self.assertTrue(self.evaluate('"ab" * 3 == "ababab"'))
        self.assertTrue(self.evaluate("[1, 2] * 2 == [1, 2, 1, 2]"))
        self.assertTrue(self.evaluate('"x" * 0 == ""'))
        self.assertTrue(self.evaluate("7 % 3 == 1"))
        self.assertTrue(self.evaluate('名前 + "様" == "山田様"', {"名前": "山田"}))
        self.assertTrue(self.evaluate(f'len("x" * {MAX_SEQUENCE_LEN}) == {MAX_SEQUENCE_LEN}'))

    def test_validate_reports_finding_instead_of_crashing(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        (tmp / "in.csv").write_text("名前\nabc\n", encoding="utf-8")
        (tmp / "rules.yaml").write_text(
            "rules:\n  - {id: BIG, type: expression, assert: '名前 * 100000000000 == \"\"'}\n", encoding="utf-8"
        )
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = run_main(cli.main, ["validate", str(tmp / "in.csv"), "-r", str(tmp / "rules.yaml"), "-f", "json"])
        self.assertEqual(rc, 1, err.getvalue())
        [finding] = json.loads(out.getvalue())["findings"]
        self.assertEqual(finding["rule_id"], "BIG")
        self.assertIn("長すぎます", finding["message"])


if __name__ == "__main__":
    unittest.main()
