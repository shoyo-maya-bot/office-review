"""NaN・Infinity を数値として扱わない（数値でない値＝型の誤り）. 比較で decimal.InvalidOperation にならない."""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import tempfile
import textwrap
import unittest
from decimal import Decimal
from pathlib import Path

from devtools_common.cli import run_main

from field_validation import cli
from field_validation.rules import condition_holds
from field_validation.values import ValueTypeError, comparable, to_number

NON_FINITE = ["NaN", "nan", "-NaN", "sNaN", "Infinity", "inf", "-Infinity", "+inf", "NaN%", "(Infinity)"]


def run(*args: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = run_main(cli.main, list(args))
    return rc, out.getvalue(), err.getvalue()


class ToNumberTest(unittest.TestCase):
    def test_non_finite_text_is_not_a_number(self):
        for text in NON_FINITE:
            with self.subTest(text=text), self.assertRaises(ValueTypeError):
                to_number(text)

    def test_non_finite_float_and_decimal_are_not_numbers(self):
        for v in (float("nan"), float("inf"), float("-inf"), Decimal("NaN"), Decimal("sNaN"), Decimal("-Infinity")):
            with self.subTest(v=v), self.assertRaises(ValueTypeError):
                to_number(v)

    def test_finite_values_unchanged(self):
        self.assertEqual(to_number("1,000"), Decimal("1000"))
        self.assertEqual(to_number(2.5), Decimal("2.5"))
        self.assertEqual(to_number("10%"), Decimal("0.1"))

    def test_comparable_falls_back_to_text(self):
        self.assertEqual(comparable("NaN"), "NaN")
        self.assertEqual(comparable("Infinity"), "Infinity")


class Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def write(self, name: str, text: str) -> Path:
        p = self.tmp / name
        p.write_text(textwrap.dedent(text), encoding="utf-8")
        return p

    def validate(self, data: str, rules: str) -> tuple[int, list[tuple[str, str, str]], str]:
        rc, out, err = run(
            "validate", str(self.write("d.csv", data)), "-r", str(self.write("r.yaml", rules)), "-f", "json"
        )
        found = [(f["rule_id"], f["location"], f["field"]) for f in json.loads(out)["findings"]] if out else []
        return rc, found, err


class RulesWithNonFiniteValuesTest(Tmp):
    """以前は range・sum_equals・compare・when の比較で InvalidOperation（終了コード 3）."""

    DATA = """\
        名前,金額,税,合計
        A,NaN,1,2
        B,1,Infinity,inf
        C,sNaN,1,1
        D,5,1,6
        """

    def test_declared_number_field_reports_type_error(self):
        rules = """\
            fields:
              金額: {type: number}
              税: {type: number}
              合計: {type: number}
            rules:
              - {id: RANGE, type: range, field: 金額, min: 0, max: 100}
              - {id: SUM, type: sum_equals, total: 合計, parts: [金額, 税]}
              - {id: CMP, type: compare, left: 金額, op: "<=", right: 合計}
              - {id: WHEN, type: required, fields: [名前], when: {field: 税, gt: 0}}
            """
        rc, found, err = self.validate(self.DATA, rules)
        self.assertEqual(rc, 1, err)
        self.assertEqual(
            sorted(found),
            sorted(
                [
                    ("schema.type", "row 1", "金額"),
                    ("schema.type", "row 2", "税"),
                    ("schema.type", "row 2", "合計"),
                    # 右辺（合計）が数値でないときの compare は、"abc" などと同じ既存の扱い（比較できない＝満たさない）
                    ("CMP", "row 2", "金額"),
                    ("schema.type", "row 3", "金額"),
                ]
            ),
        )

    def test_undeclared_fields_do_not_crash(self):
        rules = """\
            rules:
              - {id: RANGE, type: range, field: 金額, min: 0, max: 100}
              - {id: SUM, type: sum_equals, total: 合計, parts: [金額, 税]}
              - {id: CMP, type: compare, left: 金額, op: "<=", value: 10}
              - {id: WHEN, type: required, fields: [名前], when: {field: 金額, lt: 3}}
            """
        rc, found, err = self.validate(self.DATA, rules)
        self.assertNotIn("InvalidOperation", err)
        self.assertEqual(rc, 1, err)
        # 数値でない値は値域・大小と比較できない（文字列として扱う）
        self.assertIn(("RANGE", "row 1", "金額"), found)
        self.assertIn(("CMP", "row 1", "金額"), found)
        self.assertNotIn(("SUM", "row 1", "合計"), found)  # 合計が数値でない行は和を比べない（既存の扱い）

    def test_when_condition_with_non_finite_value_is_false(self):
        rec = {"金額": "NaN"}
        self.assertFalse(condition_holds({"field": "金額", "gt": 0}, rec))
        self.assertFalse(condition_holds({"field": "金額", "lte": 0}, rec))

    def test_non_finite_rule_constant_is_rule_error(self):
        for rule in (
            "{id: R, type: range, field: 金額, max: .inf}",
            "{id: R, type: range, field: 金額, min: .nan}",
            '{id: R, type: compare, left: 金額, op: "<", value: .inf}',
        ):
            with self.subTest(rule=rule):
                rc, _, err = self.validate(self.DATA, f"rules:\n  - {rule}\n")
                self.assertEqual(rc, 2, err)
                self.assertIn("有限でない数", err)


if __name__ == "__main__":
    unittest.main()
