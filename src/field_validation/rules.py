"""検証ルール: 基底クラス・適用条件（when）・組み込みルール・登録（プラグイン拡張点）.

カスタムルールの追加方法（プラグイン）:

    from field_validation.rules import Rule, register

    @register
    class NoFullWidthDigits(Rule):
        type_name = "no_fullwidth_digits"
        params_schema = {"field": {"type": "string"}}
        required_params = ["field"]

        def check(self, loc, rec):
            v = rec.get(self.spec["field"])
            if v and any("０" <= ch <= "９" for ch in str(v)):
                yield self.finding(loc, self.spec["field"], "全角数字が含まれています", actual=str(v))

`fieldcheck validate ... --plugin my_rules.py` で読み込むか、パッケージの entry point
（group = "field_validation.rules"）として配布する。
"""

from __future__ import annotations

import operator
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, ClassVar

from devtools_common.report import Finding, Severity

from .datasets import AGG_FUNCS, Datasets
from .expr import Expression, ExpressionError, display_values
from .values import ValueTypeError, comparable, display, is_blank, to_number, to_text

RULE_TYPES: dict[str, type[Rule]] = {}


def register(cls: type[Rule]) -> type[Rule]:
    if not getattr(cls, "type_name", ""):
        raise ValueError(f"{cls.__name__} must define type_name")
    RULE_TYPES[cls.type_name] = cls
    return cls


@dataclass
class Context:
    masters: dict[str, set[str]] = field(default_factory=dict)
    datasets: Datasets = field(default_factory=Datasets)
    field_types: dict[str, str] = field(default_factory=dict)  # fields: で宣言した型


# ---------------------------------------------------------------------------
# 適用条件
# ---------------------------------------------------------------------------
CONDITION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "field": {"type": "string"},
        "equals": {},
        "not_equals": {},
        "in": {"type": "array"},
        "not_in": {"type": "array"},
        "present": {"type": "boolean"},
        "gt": {},
        "gte": {},
        "lt": {},
        "lte": {},
        "all": {"type": "array", "items": {"$ref": "#/$defs/condition"}},
        "any": {"type": "array", "items": {"$ref": "#/$defs/condition"}},
    },
    "additionalProperties": False,
}

_CMP = {"gt": operator.gt, "gte": operator.ge, "lt": operator.lt, "lte": operator.le}


def condition_holds(cond: dict[str, Any] | None, rec: dict[str, Any]) -> bool:
    if not cond:
        return True
    if "all" in cond and not all(condition_holds(c, rec) for c in cond["all"]):
        return False
    if "any" in cond and not any(condition_holds(c, rec) for c in cond["any"]):
        return False
    if "field" not in cond:
        return True
    v = rec.get(cond["field"])
    text = to_text(v)
    if "present" in cond and cond["present"] == is_blank(v):
        return False
    if "equals" in cond and text != to_text(cond["equals"]):
        return False
    if "not_equals" in cond and text == to_text(cond["not_equals"]):
        return False
    if "in" in cond and text not in {to_text(x) for x in cond["in"]}:
        return False
    if "not_in" in cond and text in {to_text(x) for x in cond["not_in"]}:
        return False
    for key, op in _CMP.items():
        if key in cond:
            if is_blank(v):
                return False
            try:
                if not op(comparable(v), comparable(cond[key])):
                    return False
            except TypeError:
                return False
    return True


def condition_fields(cond: dict[str, Any] | None) -> set[str]:
    if not cond:
        return set()
    out = {cond["field"]} if "field" in cond else set()
    for c in cond.get("all", []) + cond.get("any", []):
        out |= condition_fields(c)
    return out


# ---------------------------------------------------------------------------
# 基底クラス
# ---------------------------------------------------------------------------
class Rule:
    type_name: ClassVar[str] = ""
    params_schema: ClassVar[dict[str, Any]] = {}
    required_params: ClassVar[list[str]] = []
    field_params: ClassVar[list[str]] = []  # 列名を指す引数（入力スキーマの早期検証に使う）

    def __init__(self, spec: dict[str, Any], ctx: Context):
        self.spec = spec
        self.id: str = spec["id"]
        self.severity = Severity(spec.get("severity", "error"))
        self.message: str | None = spec.get("message")
        self.suggestion: str = spec.get("suggestion", "")
        self.when: dict[str, Any] | None = spec.get("when")
        self.ctx = ctx
        self.setup()

    def setup(self) -> None:
        """引数の前処理（正規表現のコンパイル等）. 誤りは ValueError で知らせる."""

    def referenced_fields(self) -> set[str]:
        out = condition_fields(self.when)
        for p in self.field_params:
            v = self.spec.get(p)
            if isinstance(v, str):
                out.add(v)
            elif isinstance(v, list):
                out.update(x for x in v if isinstance(x, str))
        return out

    def applies(self, rec: dict[str, Any]) -> bool:
        return condition_holds(self.when, rec)

    def check(self, loc: str, rec: dict[str, Any]) -> Iterable[Finding]:
        raise NotImplementedError

    def finish(self) -> Iterable[Finding]:
        """全レコード処理後に呼ばれる（集計系ルール用）."""
        return ()

    def finding(
        self,
        loc: str,
        fld: str,
        default_message: str,
        *,
        expected: str = "",
        actual: str = "",
        suggestion: str | None = None,
    ) -> Finding:
        return Finding(
            rule_id=self.id,
            severity=self.severity,
            message=self.message or default_message,
            location=loc,
            field=fld,
            expected=expected,
            actual=actual,
            suggestion=self.suggestion if suggestion is None or self.suggestion else suggestion,
        )


# ---------------------------------------------------------------------------
# 組み込みルール
# ---------------------------------------------------------------------------
@register
class Required(Rule):
    """必須項目。when と組み合わせて「A が X なら B は必須」を表す."""

    type_name = "required"
    params_schema = {"fields": {"type": "array", "items": {"type": "string"}, "minItems": 1}}
    required_params = ["fields"]
    field_params = ["fields"]

    def check(self, loc, rec):
        for f in self.spec["fields"]:
            if is_blank(rec.get(f)):
                yield self.finding(
                    loc, f, f"{f} は必須です", expected="値あり", actual="(空)", suggestion=f"{f} を入力してください"
                )


@register
class Range(Rule):
    """数値・日付の値域."""

    type_name = "range"
    params_schema = {"field": {"type": "string"}, "min": {}, "max": {}}
    required_params = ["field"]
    field_params = ["field"]

    def setup(self):
        if "min" not in self.spec and "max" not in self.spec:
            raise ValueError("range には min か max のどちらかが必要です")
        self.lo = comparable(self.spec["min"]) if "min" in self.spec else None
        self.hi = comparable(self.spec["max"]) if "max" in self.spec else None

    def check(self, loc, rec):
        f = self.spec["field"]
        v = rec.get(f)
        if is_blank(v):
            return
        x = comparable(v)
        try:
            bad = (self.lo is not None and x < self.lo) or (self.hi is not None and x > self.hi)
        except TypeError:
            yield self.finding(loc, f, f"{f} の型が値域と比較できません", actual=display(v))
            return
        if bad:
            lo = display(self.lo) if self.lo is not None else ""
            hi = display(self.hi) if self.hi is not None else ""
            yield self.finding(
                loc,
                f,
                f"{f} が範囲外です",
                expected=f"{lo}〜{hi}",
                actual=display(v),
                suggestion="値または入力単位を確認してください",
            )


@register
class Pattern(Rule):
    """書式（正規表現に完全一致）."""

    type_name = "pattern"
    params_schema = {"field": {"type": "string"}, "regex": {"type": "string"}}
    required_params = ["field", "regex"]
    field_params = ["field"]

    def setup(self):
        try:
            self.rx = re.compile(self.spec["regex"])
        except re.error as e:
            raise ValueError(f"regex が不正です: {e}") from e

    def check(self, loc, rec):
        f = self.spec["field"]
        v = rec.get(f)
        if not is_blank(v) and not self.rx.fullmatch(to_text(v)):
            yield self.finding(loc, f, f"{f} の書式が不正です", expected=self.spec["regex"], actual=to_text(v))


@register
class Enum(Rule):
    """許可された値の一覧."""

    type_name = "enum"
    params_schema = {"field": {"type": "string"}, "values": {"type": "array", "minItems": 1}}
    required_params = ["field", "values"]
    field_params = ["field"]

    def setup(self):
        self.allowed = [to_text(x) for x in self.spec["values"]]

    def check(self, loc, rec):
        f = self.spec["field"]
        v = rec.get(f)
        if not is_blank(v) and to_text(v) not in self.allowed:
            yield self.finding(
                loc, f, f"{f} が許可された値ではありません", expected=" / ".join(self.allowed), actual=to_text(v)
            )


@register
class Length(Rule):
    """文字数の範囲."""

    type_name = "length"
    params_schema = {"field": {"type": "string"}, "min": {"type": "integer"}, "max": {"type": "integer"}}
    required_params = ["field"]
    field_params = ["field"]

    def check(self, loc, rec):
        f = self.spec["field"]
        v = rec.get(f)
        if is_blank(v):
            return
        n = len(to_text(v))
        lo, hi = self.spec.get("min"), self.spec.get("max")
        if (lo is not None and n < lo) or (hi is not None and n > hi):
            yield self.finding(loc, f, f"{f} の文字数が範囲外です", expected=f"{lo or 0}〜{hi or ''}", actual=str(n))


@register
class SumEquals(Rule):
    """合計 = 内訳の和."""

    type_name = "sum_equals"
    params_schema = {
        "total": {"type": "string"},
        "parts": {"type": "array", "items": {"type": "string"}, "minItems": 1},
        "tolerance": {"type": "number", "minimum": 0},
    }
    required_params = ["total", "parts"]
    field_params = ["total", "parts"]

    def check(self, loc, rec):
        total_f, parts = self.spec["total"], self.spec["parts"]
        if is_blank(rec.get(total_f)):
            return
        try:
            total = to_number(rec[total_f])
            s = sum((to_number(rec.get(p)) for p in parts if not is_blank(rec.get(p))), Decimal(0))
        except ValueTypeError:
            return  # 型の誤りは schema.type で報告済み
        if abs(total - s) > Decimal(str(self.spec.get("tolerance", 0))):
            yield self.finding(
                loc,
                total_f,
                f"{total_f} が {' + '.join(parts)} と一致しません",
                expected=display(s),
                actual=display(total),
                suggestion=f"{total_f} または内訳（{', '.join(parts)}）を確認してください",
            )


@register
class Compare(Rule):
    """2 項目（または項目と定数）の大小・等価比較. 例: 開始日 <= 終了日."""

    type_name = "compare"
    OPS: ClassVar[dict[str, Any]] = {
        "==": operator.eq,
        "!=": operator.ne,
        "<": operator.lt,
        "<=": operator.le,
        ">": operator.gt,
        ">=": operator.ge,
    }
    params_schema = {"left": {"type": "string"}, "op": {"enum": list(OPS)}, "right": {"type": "string"}, "value": {}}
    required_params = ["left", "op"]
    field_params = ["left", "right"]

    def setup(self):
        if ("right" in self.spec) == ("value" in self.spec):
            raise ValueError("compare には right（項目）か value（定数）のどちらか一方を指定します")

    def check(self, loc, rec):
        left_f, op = self.spec["left"], self.spec["op"]
        lv = rec.get(left_f)
        rv = rec.get(self.spec["right"]) if "right" in self.spec else self.spec["value"]
        if is_blank(lv) or is_blank(rv):
            return
        rname = self.spec.get("right", display(comparable(rv)))
        try:
            ok = self.OPS[op](comparable(lv), comparable(rv))
        except TypeError:
            ok = False
        if not ok:
            yield self.finding(
                loc,
                left_f,
                f"{left_f} {op} {rname} を満たしません",
                expected=f"{left_f} {op} {rname}",
                actual=f"{display(lv)} / {display(rv)}",
            )


@register
class Reference(Rule):
    """参照整合性: 値がマスタに存在する."""

    type_name = "reference"
    params_schema = {"field": {"type": "string"}, "master": {"type": "string"}}
    required_params = ["field", "master"]
    field_params = ["field"]

    def setup(self):
        if self.spec["master"] not in self.ctx.masters:
            raise ValueError(f"master '{self.spec['master']}' が masters に定義されていません")
        self.keys = self.ctx.masters[self.spec["master"]]

    def check(self, loc, rec):
        f = self.spec["field"]
        v = rec.get(f)
        if not is_blank(v) and to_text(v) not in self.keys:
            yield self.finding(
                loc,
                f,
                f"{f} がマスタ「{self.spec['master']}」に存在しません",
                expected=f"{self.spec['master']} のキー",
                actual=to_text(v),
                suggestion="コードの誤記、またはマスタへの登録漏れを確認してください",
            )


@register
class Unique(Rule):
    """一意性（複数項目の組み合わせも可）. キーだけを保持するのでメモリはキー数に比例."""

    type_name = "unique"
    params_schema = {"fields": {"type": "array", "items": {"type": "string"}, "minItems": 1}}
    required_params = ["fields"]
    field_params = ["fields"]

    def setup(self):
        self.seen: dict[tuple[str, ...], str] = {}

    def check(self, loc, rec) -> Iterator[Finding]:
        key = tuple(to_text(rec.get(f)) for f in self.spec["fields"])
        if all(k == "" for k in key):
            return
        if key in self.seen:
            yield self.finding(
                loc,
                ",".join(self.spec["fields"]),
                "値が重複しています",
                expected="一意",
                actual=f"{' / '.join(key)}（{self.seen[key]} と重複）",
            )
        else:
            self.seen[key] = loc


@register
class AggregateEquals(Rule):
    """表をまたぐ集計の一致: 例「ヘッダの合計額 = 明細（別の表）の金額の合計」（申請ID ごと）."""

    type_name = "aggregate_equals"
    params_schema = {
        "field": {"type": "string", "description": "この表の比較する項目（例: 合計）"},
        "group_by": {"type": "string", "description": "この表の突き合わせキー（例: 申請ID）"},
        "dataset": {"type": "string", "description": "集計する別の表（datasets: の名前）"},
        "dataset_group_by": {"type": "string", "description": "別の表の突き合わせキー（既定: group_by と同じ）"},
        "dataset_field": {"type": "string", "description": "別の表の集計する項目（count では不要）"},
        "func": {"enum": list(AGG_FUNCS)},
        "tolerance": {"type": "number", "minimum": 0},
        "missing": {"enum": ["error", "zero", "ignore"], "description": "別の表にキーが無いとき（既定 error）"},
    }
    required_params = ["field", "group_by", "dataset"]
    field_params = ["field", "group_by"]

    def setup(self):
        self.func = self.spec.get("func", "sum")
        if self.func != "count" and "dataset_field" not in self.spec:
            raise ValueError(f"func={self.func} には dataset_field が必要です")
        self.values = self.ctx.datasets.aggregate(
            self.spec["dataset"],
            self.spec.get("dataset_group_by", self.spec["group_by"]),
            self.spec.get("dataset_field"),
            self.func,
        )
        self.tolerance = Decimal(str(self.spec.get("tolerance", 0)))
        self.missing = self.spec.get("missing", "error")

    def check(self, loc, rec):
        f, key = self.spec["field"], to_text(rec.get(self.spec["group_by"]))
        if not key or is_blank(rec.get(f)):
            return
        label = f"{self.spec['dataset']} の {self.spec.get('dataset_field', '件数')} の{self.func}"
        if key not in self.values:
            if self.missing == "ignore":
                return
            if self.missing == "error":
                yield self.finding(
                    loc,
                    f,
                    f"{self.spec['dataset']} に {self.spec['group_by']}={key} の行がありません",
                    expected=f"{self.spec['group_by']}={key} の行",
                    actual="(なし)",
                )
                return
            agg = Decimal(0)
        else:
            agg = self.values[key]
        try:
            v = to_number(rec[f])
        except ValueTypeError:
            return
        if agg is None or abs(v - Decimal(agg)) > self.tolerance:
            yield self.finding(
                loc,
                f,
                f"{f} が {label}（{self.spec['group_by']}={key}）と一致しません",
                expected=display(agg) if agg is not None else "(集計不能)",
                actual=display(v),
                suggestion=f"{f} または {self.spec['dataset']} の該当行を確認してください",
            )


@register
class ExpressionRule(Rule):
    """任意の条件式: 例 `合計 == 金額 + 税額`、`days(終了日, 開始日) <= 30`、`区分 in ["A", "B"]`."""

    type_name = "expression"
    params_schema = {
        "assert": {"type": "string", "description": "成り立つべき式（Python の式の安全なサブセット）"},
        "skip_blank": {"type": "boolean", "description": "式が参照する項目に空欄があれば評価しない（既定 true）"},
    }
    required_params = ["assert"]

    def setup(self):
        try:
            self.expr = Expression(self.spec["assert"])
        except ExpressionError as e:
            raise ValueError(str(e)) from None
        self.skip_blank = self.spec.get("skip_blank", True)

    def referenced_fields(self) -> set[str]:
        return super().referenced_fields() | self.expr.fields

    def check(self, loc, rec):
        if self.skip_blank and any(is_blank(rec.get(f)) for f in self.expr.fields):
            return
        field_name = ",".join(sorted(self.expr.fields))
        try:
            ok = self.expr.evaluate(rec, self.ctx.field_types)
        except ExpressionError as e:
            yield self.finding(
                loc,
                field_name,
                f"式を評価できません: {e}",
                expected=self.spec["assert"],
                actual=display_values(rec, self.expr.fields),
            )
            return
        if not ok:
            yield self.finding(
                loc,
                field_name,
                f"条件を満たしません: {self.spec['assert']}",
                expected=self.spec["assert"],
                actual=display_values(rec, self.expr.fields),
            )
