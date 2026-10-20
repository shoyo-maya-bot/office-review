"""第 6 回: --max-findings で軽い指摘が重い指摘を押し出さない・finish() で超えたら truncated・matches の不正な正規表現."""  # noqa: E501

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
from devtools_common.report import Finding, Severity

from field_validation import cli
from field_validation.engine import evaluate, load_ruleset
from field_validation.loaders import open_source

# 1〜3 行目: 備考が空（warning）. 4 行目: 金額が負（error）. 申請ID の重複（unique・error）は finish() で出る
DATA = """申請ID,金額,備考
A1,100,
A2,200,
A3,300,
A4,-1,
A1,500,x
"""

RULES = """version: 1
name: t
fields:
  申請ID: {type: string}
  金額: {type: number}
  備考: {type: string}
rules:
  - id: W-001
    type: required
    severity: warning
    fields: [備考]
  - id: E-001
    type: expression
    assert: 金額 >= 0
  - id: E-002
    type: unique
    fields: [申請ID]
"""


def run_cli(*args: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = run_main(cli.main, list(args))
    return rc, out.getvalue(), err.getvalue()


class Round6Test(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.data = self.write("d.csv", DATA)
        self.rules = self.write("r.yaml", RULES)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def write(self, name: str, text: str) -> Path:
        p = self.tmp / name
        p.write_text(textwrap.dedent(text), encoding="utf-8")
        return p

    def _validate(self, *extra: str) -> tuple[int, dict]:
        rc, out, err = run_cli("validate", str(self.data), "-r", str(self.rules), "-f", "json", *extra)
        return rc, json.loads(out) if out.strip().startswith("{") else {"err": err}

    def test_warnings_do_not_push_out_errors(self):
        rc, rep = self._validate("--max-findings", "2")
        sev = [f["severity"] for f in rep["findings"]]
        self.assertEqual(len(sev), 2)
        self.assertIn("error", sev)  # 以前は warning 2 件だけで終了コード 0
        self.assertEqual(rc, 1)
        self.assertIn("truncated", json.dumps(rep, ensure_ascii=False))

    def test_fail_on_never_and_warning_keep_old_early_stop(self):
        rc, rep = self._validate("--max-findings", "2", "--fail-on", "warning")
        self.assertEqual(rc, 1)
        self.assertEqual([f["severity"] for f in rep["findings"]], ["warning", "warning"])
        rc, _ = self._validate("--max-findings", "2", "--fail-on", "never")
        self.assertEqual(rc, 0)

    def test_finish_findings_over_limit_set_truncated_and_keep_errors(self):
        # レコードの指摘は warning 1 件だけ（上限 2 に届かない）. 集計系のルールの finish() の error 2 件で上限を超える
        data = self.write("d3.csv", "申請ID,金額,備考\nA1,1,\nB1,1,x\n")
        extra = [Finding("AGG-001", Severity.ERROR, "集計が合いません", location=f"group {g}") for g in ("a", "b")]

        def ruleset():
            rs = load_ruleset(self.rules, [])
            rs.rules[0].finish = lambda: list(extra)
            return rs

        full = evaluate(open_source(data), ruleset(), max_findings=100)
        self.assertFalse(full.truncated)
        self.assertEqual(len(full.findings), 3)
        res = evaluate(open_source(data), ruleset(), max_findings=2)
        self.assertTrue(res.truncated)  # 以前は finish() の分の切り捨てで truncated が立たなかった
        self.assertEqual([f.rule_id for f in res.findings], ["AGG-001", "AGG-001"])  # error を残し warning を落とす

    def test_library_default_still_stops_at_limit(self):
        rs = load_ruleset(self.rules, [])
        res = evaluate(open_source(self.data), rs, max_findings=2)
        self.assertTrue(res.truncated)
        self.assertEqual(res.records, 2)  # fail_on を渡さなければ従来どおり上限で止める

    def test_invalid_regex_in_matches_is_rules_error(self):
        extra = '  - id: E-003\n    type: expression\n    assert: matches(申請ID, "[A-")\n'
        bad = self.write("bad.yaml", RULES + extra)
        rc, out, err = run_cli("check-rules", str(bad))
        self.assertEqual(rc, 2, out + err)  # 以前は check-rules を通り、validate で終了コード 3
        self.assertIn("正規表現", err)
        rc, _, err = run_cli("validate", str(self.data), "-r", str(bad))
        self.assertEqual(rc, 2, err)

    def test_invalid_regex_from_field_value_is_finding_not_crash(self):
        data = self.write("d2.csv", "申請ID,金額,備考,式\nA1,1,x,[A-\n")
        rules = self.write(
            "r2.yaml",
            "version: 1\nname: t\nrules:\n  - id: E-004\n    type: expression\n    assert: matches(申請ID, 式)\n",
        )
        rc, out, err = run_cli("validate", str(data), "-r", str(rules), "-f", "json")
        self.assertEqual(rc, 1, err)
        self.assertIn("正規表現が不正", out)


if __name__ == "__main__":
    unittest.main()
