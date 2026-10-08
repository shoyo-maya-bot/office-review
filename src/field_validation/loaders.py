"""入力の読み込み（ストリーム）.

対応: CSV / TSV / JSON（配列 or {"records": [...]}）/ JSON Lines / YAML / Markdown の表（doc-to-markdown の出力）
変換（Office → 構造化データ）は責務外。Excel 等は doc-to-markdown で Markdown 化してから渡す。

各ローダは (列名リスト or None, レコードのイテレータ, 警告) を返す。レコードは (位置, dict)。

文字コード: 既定（encoding=None）は UTF-8（BOM 付き可）を試し、読めなければ Shift_JIS（cp932）を試す。
どちらでも読めなければ使い方の誤り（終了コード 2）にして --encoding を案内する。
"""

from __future__ import annotations

import codecs
import csv
import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from devtools_common.cli import UsageError
from devtools_common.log import get_logger
from devtools_common.report import Finding, Severity

from .mdtable import markdown_tables

Record = tuple[str, dict[str, Any]]
AUTO_ENCODINGS = ("utf-8-sig", "cp932")
log = get_logger(__name__)

__all__ = ["AUTO_ENCODINGS", "Record", "Source", "detect_encoding", "markdown_tables", "open_source"]


@dataclass
class Source:
    columns: list[str] | None  # 表形式なら列名（入力スキーマの早期検証に使う）
    records: Iterator[Record]
    warnings: list[Finding] = field(default_factory=list)  # 入力の形の注意（表の行のセル数の過不足など）


def _decodes(path: Path, encoding: str) -> bool:
    """ファイル全体がこの文字コードで読めるか（少しずつ読むので、大きなファイルでもメモリを使わない）."""
    dec = codecs.getincrementaldecoder(encoding)()
    try:
        with path.open("rb") as f:
            while chunk := f.read(1 << 20):
                dec.decode(chunk)
        dec.decode(b"", final=True)
    except UnicodeDecodeError:
        return False
    return True


def detect_encoding(path: Path, encoding: str | None) -> str:
    """encoding が None なら UTF-8 → cp932 の順に試す. どちらも駄目なら UsageError."""
    if encoding:
        return encoding
    for enc in AUTO_ENCODINGS:
        if _decodes(path, enc):
            return enc
    raise UsageError(
        f"{path.name}: 文字コードを判別できません（UTF-8 でも Shift_JIS でもありません）。"
        "--encoding で指定してください（例: --encoding euc-jp）"
    )


def _decode_error(path: Path, encoding: str, e: UnicodeDecodeError) -> UsageError:
    return UsageError(
        f"{path.name}: 文字コード {encoding} として読めません（{e.start} バイト目付近）。"
        "--encoding で正しい文字コードを指定してください（例: --encoding cp932）"
    )


def _read_text(path: Path, encoding: str) -> str:
    try:
        return path.read_text(encoding=encoding)
    except UnicodeDecodeError as e:
        raise _decode_error(path, encoding, e) from None


def _csv(path: Path, encoding: str, delimiter: str) -> Source:
    f = path.open(encoding=encoding, newline="")
    try:
        reader = csv.DictReader(f, delimiter=delimiter)
        columns = [c.strip() for c in (reader.fieldnames or [])]
        _check_duplicate_columns(columns, path)
    except UnicodeDecodeError as e:
        f.close()
        raise _decode_error(path, encoding, e) from None
    except BaseException:
        f.close()
        raise
    warnings: list[Finding] = []  # 読みながら足す（evaluate は読み終えてから集める）

    def gen() -> Iterator[Record]:
        try:
            for i, row in enumerate(reader, 1):
                loc = f"row {i}"
                extra = row.pop(None, None)  # DictReader は列より多いセルを None キーにまとめる
                if extra:
                    hint = "列の区切り文字（, やタブ）が値の中に引用符なしで入っていないか"
                    w = _extra_cells_warning(path, loc, len(columns), extra, hint)
                    log.warning("%s %s（%s / %s）", w.location, w.message, w.expected, w.actual)
                    warnings.append(w)
                yield loc, {k.strip(): v for k, v in row.items()}
        except UnicodeDecodeError as e:
            raise _decode_error(path, encoding, e) from None
        finally:
            f.close()

    return Source(columns, gen(), warnings)


def _check_duplicate_columns(columns: list[str], path: Path) -> None:
    """同じ列名が 2 回以上あると、後の列が前の列の値を黙って上書きする. 読む前に使い方の誤りとして止める."""
    seen: set[str] = set()
    dups = [c for c in columns if c in seen or seen.add(c)]
    if dups:
        names = ", ".join(dict.fromkeys(dups))
        raise UsageError(
            f"{path.name}: 列名が重複しています: {names}（後の列の値で前の列が上書きされるため読みません）。"
            "列名を一意にしてください（例: 金額_1, 金額_2）"
        )


def _extra_cells_warning(path: Path, loc: str, n_columns: int, extra: list[str], hint: str) -> Finding:
    return Finding(
        "input.row_shape",
        Severity.WARNING,
        f"{path.name}: 表の行のセルが列より多いため、列の無いセルを読みませんでした",
        location=loc,
        expected=f"{n_columns} セル",
        actual=f"{n_columns + len(extra)} セル（読まなかった値: {' | '.join(extra)}）",
        suggestion=f"{hint}確かめてください",
    )


def _records_from(data: Any, name: str) -> list[dict]:
    if isinstance(data, dict) and isinstance(data.get("records"), list):
        data = data["records"]
    if not isinstance(data, list) or not all(isinstance(r, dict) for r in data):
        raise UsageError(f"{name}: レコードの配列（または records キー）が必要です")
    return data


def _structured(path: Path, encoding: str) -> Source:
    text = _read_text(path, encoding)
    try:
        data = json.loads(text) if path.suffix.lower() == ".json" else yaml.safe_load(text)
    except (json.JSONDecodeError, yaml.YAMLError) as e:
        raise UsageError(f"{path.name} を解析できません: {e}") from e
    records = _records_from(data, path.name)
    return Source(None, ((f"record {i}", r) for i, r in enumerate(records, 1)))


def _jsonl(path: Path, encoding: str) -> Source:
    def gen() -> Iterator[Record]:
        with path.open(encoding=encoding) as f:
            try:
                for i, line in enumerate(f, 1):
                    if line.strip():
                        try:
                            obj = json.loads(line)
                        except json.JSONDecodeError as e:
                            raise UsageError(f"{path.name}:{i} JSON を解析できません: {e}") from e
                        yield f"line {i}", obj
            except UnicodeDecodeError as e:
                raise _decode_error(path, encoding, e) from None

    return Source(None, gen())


def _markdown(path: Path, encoding: str, table: str | None) -> Source:
    tables = markdown_tables(_read_text(path, encoding))
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
    _check_duplicate_columns(header, path)
    warnings: list[Finding] = []
    records: list[Record] = []
    for i, r in enumerate(rows, 1):
        loc = f"row {i}"
        if len(r) < len(header):
            # 足りないセルは空として読む（末尾の空のセルを省いた表など）
            warnings.append(
                Finding(
                    "input.row_shape",
                    Severity.WARNING,
                    f"{path.name}: 表の行のセルが列より少ないため、足りない列を空として読みました",
                    location=loc,
                    expected=f"{len(header)} セル",
                    actual=f"{len(r)} セル",
                )
            )
            r = r + [""] * (len(header) - len(r))
        elif len(r) > len(header):
            extra = r[len(header) :]
            warnings.append(
                _extra_cells_warning(path, loc, len(header), extra, "セルの中の | が \\| とエスケープされているか")
            )
            r = r[: len(header)]
        records.append((loc, dict(zip(header, r, strict=True))))
    for w in warnings:
        log.warning("%s %s（%s / %s）", w.location, w.message, w.expected, w.actual)
    return Source(header, iter(records), warnings)


def open_source(path: Path, *, encoding: str | None = None, table: str | None = None) -> Source:
    """encoding: None なら UTF-8 → cp932 の順に自動で試す."""
    if not path.is_file():
        raise UsageError(f"入力が見つかりません: {path}")
    suffix = path.suffix.lower()
    if suffix in (".csv", ".tsv", ".tab", ".json", ".yaml", ".yml", ".jsonl", ".md", ".markdown"):
        encoding = detect_encoding(path, encoding)
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
