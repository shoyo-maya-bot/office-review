"""fieldcheck の JSON Lines 入力: 各行は JSON のオブジェクト. それ以外は入力の誤り（終了コード 2）.

以前は `[1, 2]` のような行をそのままレコードとして扱い、評価中に落ちて終了コード 3 になっていた.
"""

from __future__ import annotations

import contextlib
import io
import shutil
import tempfile
import unittest
from pathlib import Path

from devtools_common.cli import run_main

from field_validation import cli

RULES = "rules:\n  - {id: R, type: required, fields: [名前]}\n"


class JsonlInputShapeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "rules.yaml").write_text(RULES, encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def validate(self, jsonl: str) -> tuple[int, str, str]:
        data = self.tmp / "in.jsonl"
        data.write_text(jsonl, encoding="utf-8")
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = run_main(cli.main, ["validate", str(data), "-r", str(self.tmp / "rules.yaml"), "-f", "json"])
        return rc, out.getvalue(), err.getvalue()

    def test_non_object_lines_are_usage_errors_with_line_number(self):
        for line in ("[1, 2]", "1", '"名前"', "null", "true"):
            with self.subTest(line=line):
                rc, out, err = self.validate('{"名前": "a"}\n' + line + "\n")
                self.assertEqual(rc, 2, err)
                self.assertEqual(out, "")
                self.assertIn("in.jsonl:2", err)
                self.assertIn("オブジェクト", err)

    def test_object_lines_are_validated(self):
        rc, _, err = self.validate('{"名前": "a"}\n\n{"名前": ""}\n')
        self.assertEqual(rc, 1, err)


if __name__ == "__main__":
    unittest.main()
