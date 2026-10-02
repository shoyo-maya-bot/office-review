"""値の解釈（CSV / Markdown 由来の文字列と、JSON / YAML 由来の型付き値の両方を扱う）."""

from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal, InvalidOperation
from typing import Any

_NUM_CLEAN = re.compile(r"[,\s¥￥$円]")
_DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y年%m月%d日")


class ValueTypeError(ValueError):
    pass


def is_blank(v: Any) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def to_number(v: Any) -> Decimal:
    if isinstance(v, bool):
        raise ValueTypeError("boolean is not a number")
    if isinstance(v, (int, float, Decimal)):
        return Decimal(str(v))
    text = _NUM_CLEAN.sub("", str(v)).replace("−", "-").replace("△", "-").replace("▲", "-")
    if text.startswith("(") and text.endswith(")"):  # 会計表記の負数 (1,000)
        text = "-" + text[1:-1]
    percent = text.endswith("%") or text.endswith("％")
    if percent:  # 10% → 0.1（Excel のセルの値と同じ）
        text = text[:-1]
    try:
        n = Decimal(text)
        return n / 100 if percent else n
    except InvalidOperation as e:
        raise ValueTypeError(f"not a number: {v!r}") from e


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
    if isinstance(v, Decimal):
        return format(v.normalize(), "f") if v == v.to_integral() else str(v)
    return to_text(v)
