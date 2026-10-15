"""harness lint の対象: 存在しないパスは使い方の誤り（終了コード 2）、0 件は警告（--fail-if-empty なら 2）.

以前は存在しないパスで終了コード 3（想定外のエラー）、対象 0 件でも黙って終了コード 0 だった
（CI で何も確かめずに通っていても気づけない）.
"""

from __future__ import annotations

import contextlib
import io
import shutil
import tempfile
import unittest
from pathlib import Path

from devtools_common.cli import run_main

from review_harness import cli

ROOT = Path(__file__).resolve().parent.parent


class HarnessLintTargetsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        for d in ["config", "reviews"]:
            shutil.copytree(ROOT / d, self.tmp / d)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def lint(self, *args: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = run_main(cli.main, ["--root", str(self.tmp), "lint", *args])
        return rc, out.getvalue(), err.getvalue()

    def test_missing_path_is_usage_error(self):
        rc, _, err = self.lint(str(self.tmp / "nope"), "-f", "json")
        self.assertEqual(rc, 2)
        self.assertIn("見つかりません", err)
        self.assertIn("nope", err)

    def test_missing_default_reviews_dir_is_usage_error(self):
        shutil.rmtree(self.tmp / "reviews")
        rc, _, err = self.lint("-f", "json")
        self.assertEqual(rc, 2)
        self.assertIn("見つかりません", err)

    def test_empty_target_warns_by_default(self):
        empty = self.tmp / "empty"
        empty.mkdir()
        rc, _, err = self.lint(str(empty), "-f", "json")
        self.assertEqual(rc, 0)
        self.assertIn("レビュー結果（*_result.md）がありません", err)

    def test_empty_target_with_fail_if_empty_is_usage_error(self):
        empty = self.tmp / "empty"
        empty.mkdir()
        rc, out, err = self.lint(str(empty), "--fail-if-empty", "-f", "json")
        self.assertEqual(rc, 2)
        self.assertEqual(out, "")
        self.assertIn("--fail-if-empty", err)

    def test_existing_results_are_checked_with_fail_if_empty(self):
        rc, _, err = self.lint("--fail-if-empty", "-f", "json")
        self.assertEqual(rc, 0, err)
        self.assertNotIn("ありません", err)

    def test_ci_uses_fail_if_empty(self):
        ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertIn("harness lint --fail-if-empty", ci)


if __name__ == "__main__":
    unittest.main()
