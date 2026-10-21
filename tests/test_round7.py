"""第 7 回: 巨大な数（Overflow・表示の桁）・CSV の列不足の行・JSON の巨大な整数・precheck の Shift_JIS."""

from __future__ import annotations

import json
from decimal import Decimal

from test_input_robustness import HarnessBase, Tmp, run

from field_validation import cli as fv_cli
from field_validation.loaders import open_source
from field_validation.values import MAX_EXPONENT, ValueTypeError, display, to_number, to_text

SUM_RULES = """version: 1
fields:
  合計: {type: number}
  a: {type: number}
  b: {type: number}
rules:
  - {id: S-001, type: sum_equals, total: 合計, parts: [a, b]}
"""

SUM_RULES_UNTYPED = """version: 1
rules:
  - {id: S-001, type: sum_equals, total: 合計, parts: [a, b]}
  - {id: C-001, type: compare, left: 合計, op: ">=", right: a}
"""


def findings(out: str) -> list[dict]:
    return json.loads(out)["findings"]


class HugeNumberTest(Tmp):
    """1E+999999999 は decimal.Overflow（終了コード 3）にせず、数値でない値として扱う."""

    def test_to_number_rejects_out_of_range_exponent(self):
        for text in ("1E+999999999", "1e-999999999", f"1E+{MAX_EXPONENT + 1}"):
            with self.subTest(text=text), self.assertRaises(ValueTypeError):
                to_number(text)
        self.assertEqual(to_number(f"1E+{MAX_EXPONENT}"), Decimal(f"1E+{MAX_EXPONENT}"))
        self.assertEqual(to_number("0E+999999999"), 0)

    def test_sum_equals_with_huge_exponent_is_type_error_not_crash(self):
        data = self.write("d.csv", "合計,a,b\n1E+999999999,1,2\n3,1,2\n")
        rules = self.write("r.yaml", SUM_RULES)
        rc, out, err = run(fv_cli.main, "validate", str(data), "-r", str(rules), "-f", "json")
        self.assertEqual(rc, 1, err)  # 以前は decimal.Overflow で終了コード 3
        got = [(f["rule_id"], f["location"], f["field"]) for f in findings(out)]
        self.assertEqual(got, [("schema.type", "row 1", "合計")])

    def test_untyped_huge_value_does_not_crash(self):
        data = self.write("d.csv", "合計,a,b\n1E+999999999,-1E+999999999,2\n")
        rules = self.write("r.yaml", SUM_RULES_UNTYPED)
        rc, _, err = run(fv_cli.main, "validate", str(data), "-r", str(rules), "-f", "json")
        self.assertIn(rc, (0, 1), err)

    def test_display_uses_exponent_for_huge_numbers(self):
        self.assertEqual(display(Decimal("1E+9999")), "1E+9999")  # 以前は 1 万桁を並べた
        self.assertEqual(display(Decimal(10**5000)), "1E+5000")
        self.assertEqual(display(Decimal("1E+900000")), "1E+900000")  # 既定の文脈で normalize() が Overflow しない
        self.assertEqual(display(Decimal("1200")), "1200")
        self.assertEqual(display(Decimal("1.50")), "1.50")
        data = self.write("d.csv", f"合計,a,b\n1E+{MAX_EXPONENT},1,2\n")
        rules = self.write("r.yaml", SUM_RULES)
        rc, out, _ = run(fv_cli.main, "validate", str(data), "-r", str(rules), "-f", "json")
        self.assertEqual(rc, 1)
        f = findings(out)[0]
        self.assertEqual((f["rule_id"], f["actual"], f["expected"]), ("S-001", f"1E+{MAX_EXPONENT}", "3"))
        self.assertLess(len(out), 5000)


class CsvMissingCellsTest(Tmp):
    """CSV の列が足りない行は、Markdown の表と同じく input.row_shape の警告（以前は黙って空として読んだ）."""

    def test_short_row_is_warned_and_read_as_blank(self):
        c = self.write("d.csv", "名前,金額,備考\n山田,100,\n佐藤\n鈴木,300,x\n")
        src = open_source(c)
        recs = list(src.records)
        self.assertEqual(recs[1], ("row 2", {"名前": "佐藤", "金額": None, "備考": None}))
        self.assertEqual([(w.rule_id, w.location, w.expected, w.actual) for w in src.warnings],
                         [("input.row_shape", "row 2", "3 セル", "1 セル")])  # fmt: skip

    def test_trailing_empty_cell_is_not_a_short_row(self):
        c = self.write("d.csv", "名前,金額\n山田,\n")
        src = open_source(c)
        list(src.records)
        self.assertEqual(src.warnings, [])

    def test_cli_fail_on_warning(self):
        c = self.write("d.csv", "名前,金額\n山田\n")
        rules = self.write("r.yaml", "rules:\n  - {id: R, type: required, fields: [名前]}\n")
        rc, out, _ = run(fv_cli.main, "validate", str(c), "-r", str(rules), "-f", "json", "--fail-on", "warning")
        self.assertEqual(rc, 1)
        self.assertEqual([f["rule_id"] for f in findings(out)], ["input.row_shape"])


class JsonHugeIntegerTest(Tmp):
    """4300 桁を超える JSON の整数は Decimal として読む（以前は ValueError で終了コード 3）."""

    BIG = "9" * 5000

    def test_json_and_jsonl(self):
        rules = self.write("r.yaml", "fields:\n  n: {type: number}\nrules:\n  - {id: R, type: required, fields: [n]}\n")
        for name, text in (("d.json", f'[{{"n": {self.BIG}}}]'), ("d.jsonl", f'{{"n": {self.BIG}}}\n')):
            with self.subTest(name=name):
                data = self.write(name, text)
                recs = list(open_source(data).records)
                self.assertEqual(recs[0][1]["n"], Decimal(self.BIG))
                rc, _, err = run(fv_cli.main, "validate", str(data), "-r", str(rules), "-f", "json")
                self.assertEqual(rc, 0, err)

    def test_huge_int_to_text(self):
        self.assertEqual(len(to_text(10**5000)), 5001)  # str(int) の 4300 桁の上限に当たらない
        self.assertEqual(to_text(12), "12")


class PrecheckEncodingTest(HarnessBase):
    def test_shift_jis_markdown(self):
        doc = self.tmp / "inputs/api/order-api.md"
        doc.write_bytes(doc.read_text(encoding="utf-8").encode("cp932"))
        rc, out, err = self.harness("precheck", str(doc), "--type", "api", "-f", "json")
        self.assertEqual((rc, findings(out)), (0, []), err)  # 以前は UnicodeDecodeError で終了コード 3

    def test_undecodable_is_usage_error(self):
        doc = self.tmp / "inputs/api/bad.md"
        doc.write_bytes(b"# \xff\xfe\x80\x81\n")
        rc, _, err = self.harness("precheck", str(doc), "--type", "api")
        self.assertEqual(rc, 2)
        self.assertIn("UTF-8", err)
        self.assertNotIn("--encoding", err)
