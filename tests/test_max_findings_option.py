"""fieldcheck validate --max-findings は 1 以上. 0・負の数は引数の誤り（終了コード 2）.

以前は 0 や負の数でも「打ち切り」と表示して指摘 0 件・終了コード 0 になり、検証していないのに成功に見えた.
"""

from __future__ import annotations

import contextlib
import io
import json
import unittest
from pathlib import Path

from devtools_common.cli import UsageError, run_main

from field_validation import cli
from field_validation.engine import evaluate, load_ruleset
from field_validation.loaders import open_source

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "fieldcheck" / "samples"
DATA, RULES = SAMPLES / "expense.csv", SAMPLES / "rules.yaml"


def run(*args: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = run_main(cli.main, list(args))
    return rc, out.getvalue(), err.getvalue()


class MaxFindingsOptionTest(unittest.TestCase):
    def test_zero_negative_and_non_integer_are_usage_errors(self):
        for value in ("0", "-1", "-100", "abc", "1.5"):
            with self.subTest(value=value):
                rc, out, err = run("validate", str(DATA), "-r", str(RULES), "--max-findings", value, "-f", "json")
                self.assertEqual(rc, 2, err)
                self.assertEqual(out, "")
                self.assertIn("--max-findings", err)

    def test_positive_value_truncates(self):
        rc, out, _ = run("validate", str(DATA), "-r", str(RULES), "--max-findings", "1", "-f", "json")
        self.assertEqual(rc, 1)
        report = json.loads(out)
        self.assertEqual(len(report["findings"]), 1)
        self.assertIn("truncated", report["meta"])

    def test_omitted_means_no_limit(self):
        rc, out, _ = run("validate", str(DATA), "-r", str(RULES), "-f", "json")
        self.assertEqual(rc, 1)
        report = json.loads(out)
        self.assertGreater(len(report["findings"]), 1)
        self.assertNotIn("truncated", report["meta"])

    def test_engine_rejects_non_positive_limit(self):
        ruleset = load_ruleset(RULES)
        for value in (0, -1):
            with self.subTest(value=value), self.assertRaises(UsageError):
                evaluate(open_source(DATA), ruleset, max_findings=value)

    def test_documented_in_help_and_output_spec(self):
        help_text = cli.build_parser()._subparsers._group_actions[0].choices["validate"].format_help()
        self.assertIn("1 以上", help_text)
        spec = (ROOT / "fieldcheck" / "OUTPUT_SPEC.md").read_text(encoding="utf-8")
        self.assertIn("`--max-findings` が 1 未満", spec)


if __name__ == "__main__":
    unittest.main()
