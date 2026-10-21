"""値の解釈（CSV / Markdown 由来の文字列と、JSON / YAML 由来の型付き値の両方を扱う）."""

from __future__ import annotations

import datetime as dt
import re
from decimal import MAX_EMAX, MIN_EMIN, Context, Decimal, InvalidOperation, localcontext
from typing import Any

_NUM_CLEAN = re.compile(r"[,\s¥￥$円]")
_DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y年%m月%d日")


# 扱える数の大きさ（10 進の桁の位置. Decimal の adjusted()）. これを超える値（`1E+999999999` など）は数値として扱わない.
# 合計・差を求めると decimal.Overflow（既定の文脈の Emax は 999999）で検証全体が落ち（終了コード 3）、
# 表示も 100 万桁近くになるため。業務のデータでは十分に大きい（±10 の 9999 乗）.
MAX_EXPONENT = 9999
# 表示で固定小数点（桁をすべて書く）にする上限. これを超える桁の位置は指数表記（1E+40）にする
_DISPLAY_FIXED_DIGITS = 30
_WIDE = Context(prec=28, Emax=MAX_EMAX, Emin=MIN_EMIN)


class ValueTypeError(ValueError):
    pass


def is_blank(v: Any) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def to_number(v: Any) -> Decimal:
    """数値（Decimal）に. 読めない値と有限でない値（NaN・Infinity. 文字列・float・Decimal）は ValueTypeError.

    NaN / Infinity を数値として通すと、比較で decimal.InvalidOperation になって検証全体が落ちるので、
    「数値でない値」として扱う（型の誤りの指摘になる）。桁の位置が ±MAX_EXPONENT を超える値も同じ
    （`1E+999999999` を通すと、合計・差で decimal.Overflow になる）。
    """
    if isinstance(v, bool):
        raise ValueTypeError("boolean is not a number")
    if isinstance(v, int):
        return _finite(Decimal(v), v)  # str() を経ない（4300 桁を超える int は str() で ValueError）
    if isinstance(v, (float, Decimal)):
        return _finite(Decimal(str(v)), v)
    text = _NUM_CLEAN.sub("", str(v)).replace("−", "-").replace("△", "-").replace("▲", "-")
    if text.startswith("(") and text.endswith(")"):  # 会計表記の負数 (1,000)
        text = "-" + text[1:-1]
    percent = text.endswith("%") or text.endswith("％")
    if percent:  # 10% → 0.1（Excel のセルの値と同じ）
        text = text[:-1]
    try:
        n = _finite(Decimal(text), v)
        return n / 100 if percent else n
    except InvalidOperation as e:
        raise ValueTypeError(f"not a number: {v!r}") from e


def _finite(n: Decimal, original: Any) -> Decimal:
    if not n.is_finite():
        raise ValueTypeError(f"not a finite number: {original!r}")
    if not n:
        return n if abs(n.adjusted()) <= MAX_EXPONENT else Decimal(0)  # 0E+99999999 も 0
    if abs(n.adjusted()) > MAX_EXPONENT:
        raise ValueTypeError(f"number out of range (10^±{MAX_EXPONENT}): {_short(original)}")
    return n


def _short(v: Any, limit: int = 40) -> str:
    """メッセージ用に短くした値（巨大な値をそのまま例外の文に入れない）."""
    try:
        text = repr(v)
    except ValueError:  # 4300 桁を超える int は repr できない
        return f"<{type(v).__name__}>"
    return text if len(text) <= limit else text[:limit] + "…"


def to_date(v: Any) -> dt.date:
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    text = str(v).strip()
    for fmt in _DATE_FORMATS:
        try:
            return dt.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    try:
        return dt.datetime.fromisoformat(text).date()
    except ValueError as e:
        raise ValueTypeError(f"not a date: {v!r}") from e


def to_text(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if isinstance(v, int) and not isinstance(v, bool):
        return str(Decimal(v))  # 4300 桁を超える int は str() で ValueError になるため Decimal を経る
    return str(v).strip()


CONVERTERS = {"number": to_number, "date": to_date, "string": to_text}


def coerce(v: Any, type_name: str) -> Any:
    return CONVERTERS[type_name](v)


def comparable(v: Any) -> Any:
    """比較用: 数値→Decimal、日付→date、それ以外→文字列."""
    for conv in (to_number, to_date):
        try:
            return conv(v)
        except ValueTypeError:
            continue
    return to_text(v)


def display(v: Any) -> str:
    """指摘の期待値・実測値の表示. 大きすぎる・小さすぎる数は指数表記（桁を全部並べない）."""
    if isinstance(v, Decimal):
        if v.is_finite() and v and abs(v.adjusted()) > _DISPLAY_FIXED_DIGITS:
            with localcontext(_WIDE):  # 既定の文脈（Emax 999999）では normalize() が Overflow しうる
                return str(v.normalize())
        return format(v.normalize(), "f") if v == v.to_integral() else str(v)
    return to_text(v)
