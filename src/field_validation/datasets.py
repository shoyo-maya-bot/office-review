"""参照データセット（他の表・他のファイル）の読み込みと集計.

ルールファイルの `datasets:` か、CLI の `--dataset 名前=パス[#表]` で定義する。
集計（合計・件数など）はキーごとに 1 回だけ流し読みして作るので、メモリはキーの数に比例する。
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from devtools_common.cli import UsageError

from .loaders import Record, open_source
from .values import ValueTypeError, is_blank, to_number, to_text

AGG_FUNCS = ("sum", "count", "min", "max")


@dataclass
class DatasetSpec:
    name: str
    path: Path
    table: str | None = None
    encoding: str = "utf-8-sig"


def parse_dataset_arg(value: str) -> DatasetSpec:
    """`名前=パス` または `名前=パス#表` （表は Markdown の表番号か見出し名）."""
    name, sep, rest = value.partition("=")
    if not sep or not name or not rest:
        raise UsageError(f"--dataset の形式が不正です: {value}（例: 明細=out/申請.md#明細）")
    path, _, table = rest.partition("#")
    return DatasetSpec(name.strip(), Path(path), table or None)


@dataclass
class Datasets:
    specs: dict[str, DatasetSpec] = field(default_factory=dict)
    _aggs: dict[tuple, dict[str, Any]] = field(default_factory=dict)
    _columns: dict[str, list[str] | None] = field(default_factory=dict)

    def require(self, name: str) -> DatasetSpec:
        if name not in self.specs:
            known = ", ".join(self.specs) or "(なし)"
            raise UsageError(
                f"dataset '{name}' が定義されていません（定義済み: {known}）。datasets: か --dataset で指定"
            )
        return self.specs[name]

    def records(self, name: str) -> Iterator[Record]:
        spec = self.require(name)
        src = open_source(spec.path, encoding=spec.encoding, table=spec.table)
        self._columns[name] = src.columns
        first = True
        for loc, rec in src.records:
            if first and src.columns is None:
                self._columns[name] = list(rec.keys())
            first = False
            yield loc, rec

    def check_columns(self, name: str, needed: set[str]) -> None:
        if name not in self._columns:
            for _ in self.records(name):
                break
        cols = self._columns.get(name)
        missing = sorted(needed - set(cols or []))
        if cols is not None and missing:
            raise UsageError(f"dataset '{name}' に必要な列がありません: {', '.join(missing)}（列: {', '.join(cols)}）")

    def keys(self, name: str, column: str) -> set[str]:
        self.check_columns(name, {column})
        return {to_text(rec.get(column)) for _, rec in self.records(name) if not is_blank(rec.get(column))}

    def aggregate(self, name: str, group_by: str, column: str | None, func: str) -> dict[str, Any]:
        """group_by の値 → 集計値. func: sum / count / min / max（count は column 不要）."""
        cache_key = (name, group_by, column, func)
        if cache_key in self._aggs:
            return self._aggs[cache_key]
        self.check_columns(name, {group_by} | ({column} if column else set()))
        out: dict[str, Any] = {}
        for loc, rec in self.records(name):
            key = to_text(rec.get(group_by))
            if not key:
                continue
            if func == "count":
                out[key] = out.get(key, 0) + 1
                continue
            raw = rec.get(column) if column else None
            if is_blank(raw):
                out.setdefault(key, Decimal(0) if func == "sum" else None)
                continue
            try:
                v = to_number(raw)
            except ValueTypeError:
                raise UsageError(f"dataset '{name}' {loc} の {column} が数値ではありません: {raw!r}") from None
            cur = out.get(key)
            if func == "sum":
                out[key] = (cur or Decimal(0)) + v
            elif func == "min":
                out[key] = v if cur is None else min(cur, v)
            elif func == "max":
                out[key] = v if cur is None else max(cur, v)
        self._aggs[cache_key] = out
        return out
