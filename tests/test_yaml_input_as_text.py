"""fieldcheck の YAML 入力は、引用符の無い値も文字列のまま読む（CSV・Markdown の表と同じ）.

以前は yaml.safe_load で読んでいたため、`010` が 8（8 進数）、`no` が False、`2026-04-01` が日付になり、
`コード == "010"` や `enum: ["010"]` が成り立たなかった.
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
from field_validation.loaders import open_source

RULES = """\
rules:
  - {id: CODE, type: enum, field: コード, values: ["010", "020"]}
  - {id: EXPR, type: expression, assert: 'コード == "010" or コード == "020"'}
"""


class YamlInputAsTextTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def write(self, name: str, text: str) -> Path:
        p = self.tmp / name
        p.write_text(text, encoding="utf-8")
        return p

    def records(self, text: str) -> list[dict]:
        return [r for _, r in open_source(self.write("in.yaml", text)).records]

    def test_unquoted_scalars_stay_text(self):
        recs = self.records(
            "records:\n"
            "  - {コード: 010, 区分: no, 日付: 2026-04-01, 金額: 1_000, 比率: 1.50, 十六進: 0x1A, 無限: .inf}\n"
        )
        self.assertEqual(
            recs,
            [
                {
                    "コード": "010",
                    "区分": "no",
                    "日付": "2026-04-01",
                    "金額": "1_000",
                    "比率": "1.50",
                    "十六進": "0x1A",
                    "無限": ".inf",
                }
            ],
        )

    def test_null_and_empty_are_blank_and_merge_keys_work(self):
        recs = self.records("base: &b {部門: 001}\nrecords:\n  - {<<: *b, 備考: ~, メモ: }\n")
        self.assertEqual(recs, [{"部門": "001", "備考": None, "メモ": None}])

    def test_top_level_list_and_quoted_values(self):
        recs = self.records('- {コード: "010"}\n- {コード: 020}\n')
        self.assertEqual(recs, [{"コード": "010"}, {"コード": "020"}])

    def test_validate_matches_leading_zero_code(self):
        data = self.write("in.yml", "records:\n  - {コード: 010}\n  - {コード: 020}\n  - {コード: 030}\n")
        rules = self.write("rules.yaml", RULES)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = run_main(cli.main, ["validate", str(data), "-r", str(rules), "-f", "json"])
        self.assertEqual(rc, 1, err.getvalue())
        findings = json.loads(out.getvalue())["findings"]
        # 030 だけが両方のルールに当たる（010・020 は 8・16 にならず、文字列のまま一致する）
        self.assertEqual(
            sorted((f["rule_id"], f["location"]) for f in findings), [("CODE", "record 3"), ("EXPR", "record 3")]
        )

    def test_json_keeps_json_types(self):
        recs = [r for _, r in open_source(self.write("in.json", '[{"n": 10, "b": false}]')).records]
        self.assertEqual(recs, [{"n": 10, "b": False}])


if __name__ == "__main__":
    unittest.main()
