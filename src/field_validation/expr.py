"""安全な式の評価（expression ルール用）.

Python の式の構文のうち、次だけを許す（それ以外はルール読み込み時にエラー）。
eval は使わず、構文木をたどって評価する（任意コードは実行できない）。

- 値: 数値・文字列・True/False/None、項目名（日本語可。例: `合計`）、`value("列 名")`（記号や空白を含む列名）
- 演算: + - * / // % 、比較（== != < <= > >=、連鎖も可）、and / or / not、`x if 条件 else y`、
  `in` / `not in`（右辺はリスト・タプル・文字列）
- 関数: abs, round, min, max, len, int, float, str, sum（リスト）,
        blank(x)（空欄か）, date("2026-04-01"), days(日付1, 日付2)（日付1 - 日付2 の日数）,
        year(d), month(d), day(d), startswith(s, p), endswith(s, p), contains(s, p), matches(s, 正規表現)

項目の値の型: ルールファイルの `fields` で型を宣言していればその型、無ければ自動判定
（数値として読めれば数値、日付として読めれば日付、それ以外は文字列）。空欄は None。
"""

from __future__ import annotations

import ast
import datetime as dt
import operator
import re
from collections.abc import Callable, Mapping
from decimal import Decimal
from typing import Any

from .values import ValueTypeError, comparable, is_blank, to_date, to_number, to_text

_BIN: dict[type, Callable[[Any, Any], Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
}
_CMP: dict[type, Callable[[Any, Any], bool]] = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.In: lambda a, b: a in b,
    ast.NotIn: lambda a, b: a not in b,
}


def _num(x: Any) -> Any:
    if isinstance(x, float):
        return Decimal(str(x))
    return x


def _days(a: Any, b: Any) -> int:
    return (to_date(a) - to_date(b)).days


FUNCTIONS: dict[str, Callable[..., Any]] = {
    "abs": abs,
    "round": lambda x, n=0: round(to_number(x), int(n)),
    "min": min,
    "max": max,
    "len": lambda x: len(to_text(x)),
    "int": lambda x: int(to_number(x)),
    "float": lambda x: to_number(x),
    "str": to_text,
    "sum": lambda xs: sum((to_number(x) for x in xs if not is_blank(x)), Decimal(0)),
    "blank": is_blank,
    "date": to_date,
    "days": _days,
    "year": lambda d: to_date(d).year,
    "month": lambda d: to_date(d).month,
    "day": lambda d: to_date(d).day,
    "startswith": lambda s, p: to_text(s).startswith(p),
    "endswith": lambda s, p: to_text(s).endswith(p),
    "contains": lambda s, p: p in to_text(s),
    "matches": lambda s, rx: re.search(rx, to_text(s)) is not None,
}

_ALLOWED = (
    ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.UnaryOp, ast.Not, ast.USub, ast.UAdd, ast.BinOp, ast.Compare,
    ast.IfExp, ast.Call, ast.Name, ast.Load, ast.Constant, ast.List, ast.Tuple,
    *_BIN.keys(), *_CMP.keys(),
)  # fmt: skip


class ExpressionError(ValueError):
    pass


class Expression:
    def __init__(self, source: str):
        self.source = source
        try:
            self.tree = ast.parse(source.strip(), mode="eval")
        except SyntaxError as e:
            raise ExpressionError(f"式の構文が不正です: {source!r}（{e.msg}）") from None
        self.fields: set[str] = set()
        for node in ast.walk(self.tree):
            if not isinstance(node, _ALLOWED):
                raise ExpressionError(f"式に使えない要素があります: {type(node).__name__}（{source!r}）")
            if isinstance(node, ast.Call):
                if not isinstance(node.func, ast.Name) or node.func.id not in (*FUNCTIONS, "value"):
                    name = getattr(node.func, "id", "?")
                    raise ExpressionError(
                        f"使えない関数です: {name}（使える関数: {', '.join(sorted(FUNCTIONS))}, value）"
                    )
                if node.keywords:
                    raise ExpressionError("関数のキーワード引数は使えません")
                if node.func.id == "value":
                    if len(node.args) != 1 or not isinstance(node.args[0], ast.Constant):
                        raise ExpressionError('value() は value("列名") の形で使います')
                    self.fields.add(str(node.args[0].value))
            elif isinstance(node, ast.Name) and node.id not in FUNCTIONS and node.id != "value":
                self.fields.add(node.id)

    def evaluate(self, rec: Mapping[str, Any], types: Mapping[str, str] | None = None) -> Any:
        types = types or {}

        def get(name: str) -> Any:
            v = rec.get(name)
            if is_blank(v):
                return None
            t = types.get(name)
            try:
                if t == "number":
                    return to_number(v)
                if t == "date":
                    return to_date(v)
                if t == "string":
                    return to_text(v)
            except ValueTypeError:
                return to_text(v)
            return _num(comparable(v))

        def ev(node: ast.AST) -> Any:
            if isinstance(node, ast.Expression):
                return ev(node.body)
            if isinstance(node, ast.Constant):
                return _num(node.value)
            if isinstance(node, ast.Name):
                if node.id in ("True", "False", "None"):
                    return {"True": True, "False": False, "None": None}[node.id]
                return get(node.id)
            if isinstance(node, (ast.List, ast.Tuple)):
                return [ev(e) for e in node.elts]
            if isinstance(node, ast.BoolOp):
                if isinstance(node.op, ast.And):
                    result: Any = True
                    for v in node.values:
                        result = ev(v)
                        if not result:
                            return result
                    return result
                result = False
                for v in node.values:
                    result = ev(v)
                    if result:
                        return result
                return result
            if isinstance(node, ast.UnaryOp):
                v = ev(node.operand)
                if isinstance(node.op, ast.Not):
                    return not v
                return -v if isinstance(node.op, ast.USub) else +v
            if isinstance(node, ast.BinOp):
                return _BIN[type(node.op)](ev(node.left), ev(node.right))
            if isinstance(node, ast.Compare):
                left = ev(node.left)
                for op, comp in zip(node.ops, node.comparators, strict=True):
                    right = ev(comp)
                    if not _CMP[type(op)](left, right):
                        return False
                    left = right
                return True
            if isinstance(node, ast.IfExp):
                return ev(node.body) if ev(node.test) else ev(node.orelse)
            if isinstance(node, ast.Call):
                name = node.func.id  # type: ignore[attr-defined]
                if name == "value":
                    return get(str(node.args[0].value))  # type: ignore[attr-defined]
                return FUNCTIONS[name](*[ev(a) for a in node.args])
            raise ExpressionError(f"評価できない要素: {type(node).__name__}")

        try:
            return ev(self.tree)
        except (TypeError, ValueError, ArithmeticError, ValueTypeError) as e:
            raise ExpressionError(f"{type(e).__name__}: {e}") from None


def display_values(rec: Mapping[str, Any], fields: set[str]) -> str:
    return ", ".join(f"{f}={to_text(rec.get(f)) or '(空)'}" for f in sorted(fields))


__all__ = ["Expression", "ExpressionError", "FUNCTIONS", "display_values", "dt"]
