"""入力の読み込み（ストリーム）.

対応: CSV / TSV / JSON（配列 or {"records": [...]}）/ JSON Lines / YAML / Markdown の表（doc-to-markdown の出力）
変換（Office → 構造化データ）は責務外。Excel 等は doc-to-markdown で Markdown 化してから渡す。

各ローダは (列名リスト or None, レコードのイテレータ) を返す。レコードは (位置, dict)。
"""

from __future__ import annotations

import csv
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from devtools_common.cli import UsageError

Record = tuple[str, dict[str, Any]]


@dataclass
class Source:
    columns: list[str] | None  # 表形式なら列名（入力スキーマの早期検証に使う）
    records: Iterator[Record]


def _csv(path: Path, encoding: str, delimiter: str) -> Source:
    f = path.open(encoding=encoding, newline="")
    reader = csv.DictReader(f, delimiter=delimiter)
    columns = [c.strip() for c in (reader.fieldnames or [])]

    def gen() -> Iterator[Record]:
        try:
            for i, row in enumerate(reader, 1):
                yield f"row {i}", {(k or "").strip(): v for k, v in row.items()}
        finally:
            f.close()

    return Source(columns, gen())


def _records_from(data: Any, name: str) -> list[dict]:
    if isinstance(data, dict) and isinstance(data.get("records"), list):
        data = data["records"]
    if not isinstance(data, list) or not all(isinstance(r, dict) for r in data):
        raise UsageError(f"{name}: レコードの配列（または records キー）が必要です")
    return data


def _structured(path: Path, encoding: str) -> Source:
    text = path.read_text(encoding=encoding)
    try:
        data = json.loads(text) if path.suffix.lower() == ".json" else yaml.safe_load(text)
    except (json.JSONDecodeError, yaml.YAMLError) as e:
        raise UsageError(f"{path.name} を解析できません: {e}") from e
    records = _records_from(data, path.name)
    return Source(None, ((f"record {i}", r) for i, r in enumerate(records, 1)))


def _jsonl(path: Path, encoding: str) -> Source:
    def gen() -> Iterator[Record]:
        with path.open(encoding=encoding) as f:
            for i, line in enumerate(f, 1):
                if line.strip():
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError as e:
                        raise UsageError(f"{path.name}:{i} JSON を解析できません: {e}") from e
                    yield f"line {i}", obj

    return Source(None, gen())


_SEP = re.compile(r"^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")
_SPLIT = re.compile(r"(?<!\\)\|")


def _md_cells(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    return [c.strip().replace("\\|", "|").replace("<br>", "\n") for c in _SPLIT.split(s)]


def markdown_tables(text: str) -> list[tuple[str, list[str], list[list[str]]]]:
    """Markdown 中の GFM 表を (直前の見出し, 列名, 行) のリストで返す."""
    lines = text.splitlines()
    tables = []
    heading = ""
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("#"):
            heading = line.lstrip("#").strip()
        if line.strip().startswith("|") and i + 1 < len(lines) and _SEP.match(lines[i + 1].strip()):
            header = _md_cells(line)
            rows = []
            j = i + 2
            while j < len(lines) and lines[j].strip().startswith("|"):
                rows.append(_md_cells(lines[j]))
                j += 1
            tables.append((heading, header, rows))
            i = j
            continue
        i += 1
    return tables


def _markdown(path: Path, encoding: str, table: str | None) -> Source:
    tables = markdown_tables(path.read_text(encoding=encoding))
    if not tables:
        raise UsageError(f"{path.name} に表がありません")
    if table is None:
        chosen = tables[0]
    elif table.isdigit():
        n = int(table)
        if not 1 <= n <= len(tables):
            raise UsageError(f"--table {n}: 表は {len(tables)} 個です")
        chosen = tables[n - 1]
    else:
        matches = [t for t in tables if t[0] == table]
        if not matches:
            names = ", ".join(repr(t[0]) for t in tables)
            raise UsageError(f"--table {table!r}: 該当する見出しの表がありません（候補: {names}）")
        chosen = matches[0]
    _, header, rows = chosen
    return Source(header, ((f"row {i}", dict(zip(header, r, strict=False))) for i, r in enumerate(rows, 1)))


def open_source(path: Path, *, encoding: str = "utf-8-sig", table: str | None = None) -> Source:
    if not path.is_file():
        raise UsageError(f"入力が見つかりません: {path}")
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return _csv(path, encoding, ",")
    if suffix in (".tsv", ".tab"):
        return _csv(path, encoding, "\t")
    if suffix in (".json", ".yaml", ".yml"):
        return _structured(path, encoding)
    if suffix == ".jsonl":
        return _jsonl(path, encoding)
    if suffix in (".md", ".markdown"):
        return _markdown(path, encoding, table)
    raise UsageError(
        f"未対応の入力形式です: {path.name}（csv/tsv/json/jsonl/yaml/md）。"
        "Office/PDF は doc-to-markdown で Markdown 化してから渡してください"
    )
