"""field-validation のテスト.

ゴールデンテスト: samples/ の入力に対するレポート（JSON）を tests/golden と比較し、回帰を検出する。
意図した変更で差分が出たら `UPDATE_GOLDEN=1 pytest` で更新し、差分を PR でレビューする。
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import tempfile
import textwrap
import unittest
from pathlib import Path

import jsonschema
from devtools_common.cli import UsageError, run_main

from field_validation import cli
from field_validation.engine import evaluate, load_plugins, load_ruleset
from field_validation.loaders import markdown_tables, open_source
from field_validation.rules import RULE_TYPES, condition_holds

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "fieldcheck" / "samples"
GOLDEN = Path(__file__).resolve().parent / "golden"
RULES = SAMPLES / "rules.yaml"


def run_cli(*args: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = run_main(cli.main, list(args))
    return rc, out.getvalue(), err.getvalue()


def findings_of(report_json: str) -> list[tuple[str, str, str]]:
    return [(f["rule_id"], f["location"], f["field"]) for f in json.loads(report_json)["findings"]]


class Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def write(self, name: str, text: str) -> Path:
        p = self.tmp / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(text), encoding="utf-8")
        return p


class GoldenTest(unittest.TestCase):
    CASES = [
        ("expense.csv", []),
        ("expense-list.md", ["--table", "申請一覧"]),
    ]

    def test_reports_match_golden(self):
        for name, extra in self.CASES:
            with self.subTest(name):
                rc, out, _ = run_cli("validate", str(SAMPLES / name), "-r", str(RULES), "-f", "json", *extra)
                self.assertEqual(rc, 1)
                golden = GOLDEN / f"{Path(name).stem}.report.json"
                if os.environ.get("UPDATE_GOLDEN"):
                    golden.write_text(out, encoding="utf-8", newline="\n")
                actual = json.loads(out)
                expected = json.loads(golden.read_text(encoding="utf-8"))
                for d in (actual, expected):  # 版番号はリリースごとに変わるので比較しない
                    d.pop("tool_version", None)
                self.assertEqual(actual, expected)

    def test_expected_violations(self):
        _, out, _ = run_cli("validate", str(SAMPLES / "expense.csv"), "-r", str(RULES), "-f", "json")
        got = set(findings_of(out))
        for expected in [
            ("EXP-003", "row 2", "合計"),  # 合計 ≠ 金額 + 税額
            ("EXP-002", "row 3", "承認者"),  # 接待費なのに承認者なし
            ("EXP-004", "row 4", "金額"),  # 負の金額
            ("EXP-005", "row 4", "部門コード"),  # マスタに無い部門
            ("EXP-007", "row 6", "申請ID"),  # 重複
            ("schema.type", "row 6", "金額"),  # 数値でない
        ]:
            self.assertIn(expected, got)
        # 型不正の項目に二次的な指摘を重ねない
        self.assertNotIn(("EXP-004", "row 6", "金額"), got)
        # "2,400" はカンマ付きでも数値として扱う
        self.assertFalse(any(loc == "row 5" for _, loc, _ in got))


class RulesFileTest(Tmp):
    def test_schema_rejects_typo_and_unknown_type(self):
        bad = self.write(
            "r.yaml",
            """
            rules:
              - {id: A, type: required, feilds: [x]}
              - {id: B, type: nope}
        """,
        )
        with self.assertRaises(UsageError) as cm:
            load_ruleset(bad)
        msg = str(cm.exception)
        self.assertIn("feilds", msg)
        self.assertIn("nope", msg)

    def test_duplicate_ids_and_bad_regex(self):
        with self.assertRaises(UsageError):
            load_ruleset(
                self.write(
                    "a.yaml", "rules: [{id: A, type: required, fields: [x]}, {id: A, type: required, fields: [y]}]\n"
                )
            )
        with self.assertRaises(UsageError) as cm:
            load_ruleset(self.write("b.yaml", "rules: [{id: P, type: pattern, field: x, regex: '('}]\n"))
        self.assertIn("regex", str(cm.exception))

    def test_unknown_master(self):
        with self.assertRaises(UsageError):
            load_ruleset(self.write("c.yaml", "rules: [{id: R, type: reference, field: x, master: nope}]\n"))

    def test_check_rules_cli(self):
        rc, out, _ = run_cli("check-rules", str(RULES))
        self.assertEqual(rc, 0)
        self.assertIn("9 rules", out)


class InputSchemaTest(Tmp):
    def test_missing_column_is_early_usage_error(self):
        data = self.write("d.csv", "申請ID,金額\nA-001,1\n")
        rc, _, err = run_cli("validate", str(data), "-r", str(RULES))
        self.assertEqual(rc, 2)
        self.assertIn("必要な列がありません", err)

    def test_json_first_record_checked(self):
        rules = self.write("r.yaml", "rules: [{id: R, type: required, fields: [b]}]\n")
        data = self.write("d.json", '[{"a": 1}]')
        with self.assertRaises(UsageError):
            evaluate(open_source(data), load_ruleset(rules))


class BuiltinRuleTest(Tmp):
    def run_rules(self, rules_yaml: str, records: list[dict]) -> list[tuple[str, str, str]]:
        rules = self.write("r.yaml", rules_yaml)
        data = self.write("d.json", json.dumps(records, ensure_ascii=False))
        result = evaluate(open_source(data), load_ruleset(rules))
        return [(f.rule_id, f.location, f.field) for f in result.findings]

    def test_compare_dates_and_when_any(self):
        got = self.run_rules(
            """
            rules:
              - {id: C, type: compare, left: start, op: "<=", right: end}
              - id: W
                type: required
                fields: [note]
                when: {any: [{field: kind, equals: X}, {field: amount, gt: 100}]}
        """,
            [
                {"start": "2026-04-01", "end": "2026/04/02", "kind": "Y", "amount": 50, "note": ""},
                {"start": "2026-05-01", "end": "2026-04-30", "kind": "X", "amount": 1, "note": ""},
                {"start": "", "end": "", "kind": "Y", "amount": 101, "note": "ok"},
            ],
        )
        self.assertEqual(got, [("C", "record 2", "start"), ("W", "record 2", "note")])

    def test_enum_length_unique_composite(self):
        got = self.run_rules(
            """
            rules:
              - {id: E, type: enum, field: s, values: [a, b]}
              - {id: L, type: length, field: s, max: 1}
              - {id: U, type: unique, fields: [k1, k2]}
        """,
            [{"s": "a", "k1": 1, "k2": "x"}, {"s": "cc", "k1": 1, "k2": "y"}, {"s": "b", "k1": "1", "k2": "x"}],
        )
        self.assertEqual(got, [("E", "record 2", "s"), ("L", "record 2", "s"), ("U", "record 3", "k1,k2")])

    def test_sum_tolerance(self):
        rules = "rules: [{id: S, type: sum_equals, total: t, parts: [a, b], tolerance: 1}]\n"
        self.assertEqual(self.run_rules(rules, [{"t": 10, "a": 5, "b": 4}]), [])
        self.assertEqual(len(self.run_rules(rules, [{"t": 10, "a": 5, "b": 3}])), 1)

    def test_condition_helpers(self):
        self.assertTrue(condition_holds({"field": "a", "present": False}, {"a": " "}))
        self.assertFalse(condition_holds({"field": "a", "gte": 10}, {"a": ""}))
        self.assertTrue(
            condition_holds(
                {"all": [{"field": "a", "in": [1, 2]}, {"field": "b", "lt": "2026-01-01"}]},
                {"a": "2", "b": "2025-12-31"},
            )
        )


class LoaderTest(Tmp):
    def test_markdown_tables_with_escaped_pipe(self):
        md = "# T\n\n## 一覧\n\n| a | b |\n| --- | --- |\n| x\\|y | 1 |\n\n| c |\n|---|\n| 2 |\n"
        tables = markdown_tables(md)
        self.assertEqual(len(tables), 2)
        self.assertEqual(tables[0][0], "一覧")
        self.assertEqual(tables[0][2], [["x|y", "1"]])

    def test_table_selection_errors(self):
        p = self.write("a.md", "| a |\n| --- |\n| 1 |\n")
        with self.assertRaises(UsageError):
            open_source(p, table="3")
        with self.assertRaises(UsageError):
            open_source(p, table="無い見出し")

    def test_office_input_points_to_converter(self):
        p = self.write("a.xlsx", "x")
        with self.assertRaises(UsageError) as cm:
            open_source(p)
        self.assertIn("doc-to-markdown", str(cm.exception))

    def test_jsonl_and_cp932_csv(self):
        p = self.write("a.jsonl", '{"a": 1}\n\n{"a": 2}\n')
        self.assertEqual([loc for loc, _ in open_source(p).records], ["line 1", "line 3"])
        c = self.tmp / "sj.csv"
        c.write_bytes("列\n値\n".encode("cp932"))
        src = open_source(c, encoding="cp932")
        self.assertEqual(src.columns, ["列"])
        self.assertEqual(list(src.records), [("row 1", {"列": "値"})])


class PluginAndCliTest(Tmp):
    def test_plugin_rule(self):
        load_plugins([SAMPLES / "plugins" / "weekday_rule.py"])
        self.assertIn("weekday", RULE_TYPES)
        rules = self.write("r.yaml", "rules: [{id: WD, type: weekday, field: d, severity: warning}]\n")
        data = self.write("d.csv", "d\n2026-04-03\n2026-04-04\n")  # 金曜, 土曜
        rc, out, _ = run_cli(
            "--plugin",
            str(SAMPLES / "plugins" / "weekday_rule.py"),
            "validate",
            str(data),
            "-r",
            str(rules),
            "-f",
            "json",
        )
        self.assertEqual(rc, 0)  # warning のみ（--fail-on error）
        self.assertEqual(findings_of(out), [("WD", "row 2", "d")])
        rc, _, _ = run_cli(
            "--plugin",
            str(SAMPLES / "plugins" / "weekday_rule.py"),
            "validate",
            str(data),
            "-r",
            str(rules),
            "--fail-on",
            "warning",
        )
        self.assertEqual(rc, 1)

    def test_schema_command_matches_committed_file(self):
        rc, out, _ = run_cli("schema")
        self.assertEqual(rc, 0)
        schema = json.loads(out)
        self.assertIn("rules", schema["properties"])
        # samples/rules.yaml から参照しているスキーマファイルが古くなっていないこと（fieldcheck schema で再生成）
        committed = json.loads((ROOT / "fieldcheck" / "rules.schema.json").read_text(encoding="utf-8"))
        committed_types = committed["properties"]["rules"]["items"]["properties"]["type"]["enum"]
        builtin = [t for t in schema["properties"]["rules"]["items"]["properties"]["type"]["enum"] if t != "weekday"]
        self.assertEqual(committed_types, builtin, "rules.schema.json が古い: fieldcheck schema > rules.schema.json")

    def test_max_findings_and_output_file(self):
        out_file = self.tmp / "report.md"
        rc, _, _ = run_cli(
            "validate", str(SAMPLES / "expense.csv"), "-r", str(RULES), "--max-findings", "2", "-o", str(out_file)
        )
        self.assertEqual(rc, 1)
        text = out_file.read_text(encoding="utf-8")
        self.assertIn("truncated", text)
        self.assertEqual(text.count("| error |") + text.count("| warning |"), 2)


class CrossTableTest(Tmp):
    CROSS = SAMPLES / "cross"

    def test_header_vs_detail_sum_and_missing(self):
        rc, out, _ = run_cli(
            "validate", str(self.CROSS / "claims.csv"), "-r", str(self.CROSS / "rules.yaml"), "-f", "json"
        )
        self.assertEqual(rc, 1)
        got = findings_of(out)
        self.assertIn(("CR-001", "row 3", "合計"), got)  # 明細が無い
        self.assertNotIn(("CR-001", "row 1", "合計"), got)  # 15400 = 1400 + 14000
        self.assertIn(("CR-002", "row 2", "終了日,開始日"), got)  # 終了日 < 開始日
        self.assertIn(("CR-002", "row 4", "終了日,開始日"), got)  # 30 日超
        self.assertEqual(json.loads(out)["meta"]["datasets"], "明細")

    def test_sum_mismatch_detected(self):
        lines = self.write(
            "lines.csv",
            "申請ID,行,費目,金額\nC-001,1,交通費,1400\nC-002,1,交通費,8000\nC-003,1,交通費,5000\nC-004,1,交通費,3000\n",
        )
        rc, out, _ = run_cli(
            "validate",
            str(self.CROSS / "claims.csv"),
            "-r",
            str(self.CROSS / "rules.yaml"),
            "--dataset",
            f"明細={lines}",
            "-f",
            "json",
        )
        data = json.loads(out)
        c1 = [f for f in data["findings"] if f["rule_id"] == "CR-001"]
        self.assertEqual([(f["location"], f["expected"], f["actual"]) for f in c1], [("row 1", "1400", "15400")])

    def test_reverse_reference_from_dataset(self):
        rc, out, _ = run_cli(
            "validate", str(self.CROSS / "claim-lines.csv"), "-r", str(self.CROSS / "lines-rules.yaml"), "-f", "json"
        )
        self.assertEqual(findings_of(out), [("LN-001", "row 6", "申請ID")])

    def test_excel_ledger_from_doc2md(self):
        ledger = SAMPLES / "ledger"
        rc, out, _ = run_cli(
            "validate",
            str(ledger / "expense-ledger.md"),
            "--table",
            "表1（A4:G10）",
            "-r",
            str(ledger / "rules.yaml"),
            "-f",
            "json",
        )
        self.assertEqual(findings_of(out), [("LG-003", "row 3", "承認者")])

    def test_dataset_errors(self):
        rc, _, err = run_cli(
            "validate",
            str(self.CROSS / "claims.csv"),
            "-r",
            str(self.CROSS / "rules.yaml"),
            "--dataset",
            "明細=nope.csv",
        )
        self.assertEqual(rc, 2)
        rc, _, err = run_cli(
            "validate", str(self.CROSS / "claims.csv"), "-r", str(self.CROSS / "rules.yaml"), "--dataset", "形式が違う"
        )
        self.assertEqual(rc, 2)
        self.assertIn("--dataset", err)
        bad = self.write(
            "bad.yaml", "rules: [{id: A, type: aggregate_equals, field: x, group_by: k, dataset: 無い, func: count}]\n"
        )
        with self.assertRaises(UsageError) as cm:
            load_ruleset(bad)
        self.assertIn("無い", str(cm.exception))


class ExpressionTest(Tmp):
    def run_expr(self, expr: str, rec: dict, types: dict | None = None):
        from field_validation.expr import Expression

        return Expression(expr).evaluate(rec, types)

    def test_arithmetic_dates_and_functions(self):
        rec = {"a": "1,000", "b": "10%", "s": "2026/04/01", "e": "2026-04-11", "k": "東京支店"}
        self.assertTrue(self.run_expr("a * b == 100", rec))
        self.assertTrue(self.run_expr("days(e, s) == 10", rec))
        self.assertTrue(self.run_expr('startswith(k, "東京") and len(k) == 4', rec))
        self.assertTrue(self.run_expr('k in ["東京支店", "大阪支店"]', rec))
        self.assertTrue(self.run_expr("round(a / 3, 1) == 333.3", rec))
        self.assertEqual(self.run_expr("a if a > 1 else 0", rec), 1000)
        self.assertTrue(self.run_expr('value("s") < value("e")', rec))

    def test_string_constant_compares_as_text_without_declared_type(self):
        """型を宣言していない項目を文字列の定数と比べるときは文字列のまま比べる（"001" が数値 1 にならない）."""
        rec = {"code": "001", "dept": "0120", "n": "10", "d": "2026-04-01"}
        self.assertTrue(self.run_expr('code == "001"', rec))
        self.assertTrue(self.run_expr('"001" == code', rec))
        self.assertFalse(self.run_expr('code != "001"', rec))
        self.assertTrue(self.run_expr('code in ["001", "002"]', rec))
        self.assertTrue(self.run_expr('value("dept") not in ("120", "999")', rec))
        self.assertTrue(self.run_expr('"12" in dept', rec))
        self.assertTrue(self.run_expr('d == "2026-04-01"', rec))
        self.assertTrue(self.run_expr('code < "002"', rec))
        # 連鎖でも、文字列の定数と比べる側だけ文字列になる
        self.assertTrue(self.run_expr('"000" < code < "002"', rec))
        # 数値の定数とは今までどおり数値で比べる
        self.assertTrue(self.run_expr("code == 1", rec))
        self.assertTrue(self.run_expr("n > 9", rec))
        # 型を宣言した項目は宣言どおり（number なら 1 になる）
        self.assertFalse(self.run_expr('code == "001"', rec, {"code": "number"}))
        self.assertTrue(self.run_expr('code == "001"', rec, {"code": "string"}))

    def test_string_constant_rule_on_csv(self):
        rules = self.write(
            "r.yaml",
            """
            rules:
              - {id: C, type: expression, assert: 'code in ["001", "002"]'}
        """,
        )
        data = self.write("d.csv", "code\n001\n002\n1\n")
        result = evaluate(open_source(data), load_ruleset(rules))
        self.assertEqual([f.location for f in result.findings if f.rule_id == "C"], ["row 3"])

    def test_unsafe_expressions_rejected(self):
        from field_validation.expr import Expression, ExpressionError

        for bad in [
            '__import__("os").system("x")',
            "a.__class__",
            "(lambda: 1)()",
            "[x for x in a]",
            "open('f')",
            "a[0]",
            "exec('1')",
            "a := 1",
        ]:
            with self.subTest(bad), self.assertRaises(ExpressionError):
                Expression(bad)

    def test_expression_rule_reports_values_and_eval_errors(self):
        rules = self.write(
            "r.yaml",
            """
            fields: {n: {type: number}}
            rules:
              - {id: X, type: expression, assert: "n > 0"}
              - {id: Y, type: expression, assert: "t + 1 > 0"}
        """,
        )
        data = self.write("d.csv", "n,t\n-1,abc\n5,2\n")
        result = evaluate(open_source(data), load_ruleset(rules))
        got = [(f.rule_id, f.location, f.actual) for f in result.findings]
        self.assertIn(("X", "row 1", "n=-1"), got)
        self.assertTrue(any(f.rule_id == "Y" and "評価できません" in f.message for f in result.findings))

    def test_bad_expression_is_usage_error(self):
        bad = self.write("r.yaml", "rules: [{id: X, type: expression, assert: 'a +'}]\n")
        with self.assertRaises(UsageError):
            load_ruleset(bad)

    def test_percent_values(self):
        from field_validation.values import to_number

        self.assertEqual(str(to_number("10%")), "0.1")
        self.assertEqual(str(to_number("▲1,000")), "-1000")


class PluginTrustDocTest(unittest.TestCase):
    """--plugin・entry point は任意のコードを実行する. 信頼できるものだけ使うと README・SECURITY.md・--help に書く."""

    def test_docs_warn_that_plugins_run_as_code(self):
        root = Path(__file__).resolve().parent.parent
        for name in ("README.md", "SECURITY.md"):
            text = (root / name).read_text(encoding="utf-8")
            with self.subTest(name):
                self.assertIn("--plugin", text)
                self.assertIn("entry point", text)
                self.assertIn("field_validation.rules", text)
                self.assertIn("信頼できるもの", text)
        self.assertIn("信頼できるもの", cli.build_parser().format_help())


class OutputSpecTest(unittest.TestCase):
    def test_json_matches_report_schema(self):
        from devtools_common.report import report_schema

        _, out, _ = run_cli("validate", str(SAMPLES / "expense.csv"), "-r", str(RULES), "-f", "json")
        data = json.loads(out)
        jsonschema.validate(data, report_schema())
        self.assertEqual(data["tool"], "fieldcheck")


if __name__ == "__main__":
    unittest.main()


class VersionTest(unittest.TestCase):
    """配布名（pyproject の project.name）でバージョンを引くこと."""

    def test_version_is_looked_up_by_distribution_name(self):
        import importlib
        import importlib.metadata as md
        import tomllib
        from unittest import mock

        import field_validation

        dist = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["name"]
        asked: list[str] = []

        def fake_version(name: str) -> str:
            asked.append(name)
            if name != dist:
                raise md.PackageNotFoundError(name)
            return "9.9.9"

        try:
            with mock.patch.object(md, "version", fake_version):
                importlib.reload(field_validation)
                self.assertEqual(field_validation.__version__, "9.9.9")
                self.assertEqual(asked, [dist])
        finally:
            importlib.reload(field_validation)
            importlib.reload(cli)
