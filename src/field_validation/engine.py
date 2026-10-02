"""ルールセットの読み込み・スキーマ検証・評価."""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any

import jsonschema
from devtools_common.cli import UsageError
from devtools_common.config import load_structured
from devtools_common.log import get_logger
from devtools_common.report import Finding, Severity

from .datasets import Datasets, DatasetSpec
from .loaders import Source, open_source
from .rules import CONDITION_SCHEMA, RULE_TYPES, Context, Rule
from .values import CONVERTERS, ValueTypeError, coerce, is_blank, to_text

log = get_logger(__name__)
ENTRY_POINT_GROUP = "field_validation.rules"


# ---------------------------------------------------------------------------
# プラグイン
# ---------------------------------------------------------------------------
def load_plugins(paths: Iterable[Path] = ()) -> list[str]:
    """--plugin で指定されたファイルと、entry point で配布されたルールを登録する."""
    loaded = []
    for ep in entry_points(group=ENTRY_POINT_GROUP):
        ep.load()
        loaded.append(ep.name)
    for p in paths:
        if not p.is_file():
            raise UsageError(f"プラグインが見つかりません: {p}")
        name = f"field_validation_plugin_{p.stem}"
        spec = importlib.util.spec_from_file_location(name, p)
        if spec is None or spec.loader is None:
            raise UsageError(f"プラグインを読み込めません: {p}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        loaded.append(str(p))
    return loaded


# ---------------------------------------------------------------------------
# ルールファイルの JSON Schema（登録済みルール型から生成するのでプラグインも検証される）
# ---------------------------------------------------------------------------
BASE_RULE_PROPS: dict[str, Any] = {
    "id": {"type": "string", "minLength": 1},
    "type": {"type": "string"},
    "severity": {"enum": [s.value for s in Severity]},
    "message": {"type": "string"},
    "suggestion": {"type": "string"},
    "description": {"type": "string"},
    "when": {"$ref": "#/$defs/condition"},
}


def rules_schema() -> dict[str, Any]:
    branches = []
    for name, cls in sorted(RULE_TYPES.items()):
        branches.append(
            {
                "if": {"properties": {"type": {"const": name}}, "required": ["type"]},
                "then": {
                    "properties": {**BASE_RULE_PROPS, **cls.params_schema},
                    "required": ["id", "type", *cls.required_params],
                    "additionalProperties": False,
                },
            }
        )
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "field-validation rules",
        "type": "object",
        "required": ["rules"],
        "additionalProperties": False,
        "properties": {
            "version": {"const": 1},
            "name": {"type": "string"},
            "description": {"type": "string"},
            "fields": {
                "type": "object",
                "description": "入力スキーマ（前提）。列が無ければ早期エラー、型が合わなければ schema.type 指摘",
                "additionalProperties": {
                    "type": "object",
                    "properties": {"type": {"enum": list(CONVERTERS)}, "description": {"type": "string"}},
                    "additionalProperties": False,
                },
            },
            "datasets": {
                "type": "object",
                "description": "参照する別の表（他シート・他ファイル）。パスはルールファイルからの相対",
                "additionalProperties": {
                    "type": "object",
                    "required": ["file"],
                    "properties": {
                        "file": {"type": "string"},
                        "table": {
                            "type": ["string", "integer"],
                            "description": "Markdown の表（番号か直前の見出し名）",
                        },
                        "encoding": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
            },
            "masters": {
                "type": "object",
                "description": "reference ルールで使うキーの一覧。ファイル・datasets の列・値の列挙のいずれか",
                "additionalProperties": {
                    "type": "object",
                    "properties": {
                        "file": {"type": "string"},
                        "table": {"type": ["string", "integer"]},
                        "dataset": {"type": "string"},
                        "key": {"type": "string"},
                        "values": {"type": "array"},
                    },
                    "oneOf": [
                        {"required": ["file", "key"]},
                        {"required": ["dataset", "key"]},
                        {"required": ["values"]},
                    ],
                    "additionalProperties": False,
                },
            },
            "rules": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "required": ["id", "type"],
                    "properties": {"type": {"enum": sorted(RULE_TYPES)}},
                    "allOf": branches,
                },
            },
        },
        "$defs": {"condition": CONDITION_SCHEMA},
    }


# ---------------------------------------------------------------------------
# ルールセット
# ---------------------------------------------------------------------------
@dataclass
class RuleSet:
    name: str
    fields: dict[str, dict[str, Any]]
    rules: list[Rule]
    masters: dict[str, set[str]] = field(default_factory=dict)
    datasets: list[str] = field(default_factory=list)


def _load_master(base: Path, name: str, spec: dict[str, Any], datasets: Datasets) -> set[str]:
    if "values" in spec:
        return {to_text(v) for v in spec["values"]}
    key = spec["key"]
    if "dataset" in spec:
        try:
            return datasets.keys(spec["dataset"], key)
        except UsageError as e:
            raise UsageError(f"master '{name}': {e}") from None
    path = (base / spec["file"]).resolve()
    table = str(spec["table"]) if "table" in spec else None
    try:
        src = open_source(path, table=table)
    except UsageError as e:
        raise UsageError(f"master '{name}': {e}") from None
    keys: set[str] = set()
    for i, (_, rec) in enumerate(src.records):
        if i == 0 and key not in rec:
            raise UsageError(f"master '{name}': 列 '{key}' がありません（列: {', '.join(rec)}）")
        if not is_blank(rec.get(key)):
            keys.add(to_text(rec.get(key)))
    return keys


def _format_schema_error(e: jsonschema.ValidationError) -> str:
    where = "/".join(str(p) for p in e.absolute_path) or "(root)"
    return f"{where}: {e.message}"


def load_ruleset(path: Path, dataset_overrides: Iterable[DatasetSpec] = ()) -> RuleSet:
    data = load_structured(path)
    validator = jsonschema.Draft202012Validator(rules_schema())
    errors = sorted(validator.iter_errors(data), key=lambda e: list(e.absolute_path))
    if errors:
        detail = "\n  ".join(_format_schema_error(e) for e in errors[:10])
        raise UsageError(f"ルールファイル {path.name} がスキーマに適合しません:\n  {detail}")

    datasets = Datasets()
    for name, spec in (data.get("datasets") or {}).items():
        table = str(spec["table"]) if "table" in spec else None
        datasets.specs[name] = DatasetSpec(
            name, (path.parent / spec["file"]).resolve(), table, spec.get("encoding", "utf-8-sig")
        )
    for spec in dataset_overrides:  # CLI の --dataset はカレントディレクトリからの相対
        datasets.specs[spec.name] = spec
    masters = {n: _load_master(path.parent, n, s, datasets) for n, s in (data.get("masters") or {}).items()}
    fields = data.get("fields") or {}
    ctx = Context(
        masters=masters, datasets=datasets, field_types={k: v["type"] for k, v in fields.items() if "type" in v}
    )
    rules: list[Rule] = []
    seen: set[str] = set()
    for spec in data["rules"]:
        if spec["id"] in seen:
            raise UsageError(f"ルール id が重複しています: {spec['id']}")
        seen.add(spec["id"])
        try:
            rules.append(RULE_TYPES[spec["type"]](spec, ctx))
        except ValueError as e:
            raise UsageError(f"ルール {spec['id']}: {e}") from e
    return RuleSet(data.get("name", path.stem), fields, rules, masters, sorted(datasets.specs))


# ---------------------------------------------------------------------------
# 評価
# ---------------------------------------------------------------------------
@dataclass
class Result:
    findings: list[Finding]
    records: int
    truncated: bool = False


def _check_columns(columns: Iterable[str], ruleset: RuleSet, origin: str) -> None:
    cols = set(columns)
    needed = set(ruleset.fields)
    for r in ruleset.rules:
        needed |= r.referenced_fields()
    missing = sorted(needed - cols)
    if missing:
        raise UsageError(
            f"入力（{origin}）に必要な列がありません: {', '.join(missing)}。"
            f"入力の列: {', '.join(sorted(cols)) or '(なし)'}"
        )


def evaluate(source: Source, ruleset: RuleSet, *, max_findings: int | None = None) -> Result:
    if source.columns is not None:
        _check_columns(source.columns, ruleset, "ヘッダ行")
    findings: list[Finding] = []
    count = 0
    truncated = False
    for loc, rec in source.records:
        count += 1
        if count == 1 and source.columns is None:
            _check_columns(rec.keys(), ruleset, "先頭レコード")
        bad_fields: set[str] = set()
        for fname, fspec in ruleset.fields.items():
            t = fspec.get("type")
            v = rec.get(fname)
            if t and t != "string" and not is_blank(v):
                try:
                    coerce(v, t)
                except ValueTypeError:
                    bad_fields.add(fname)
                    findings.append(
                        Finding(
                            "schema.type",
                            Severity.ERROR,
                            f"{fname} は {t} である必要があります",
                            location=loc,
                            field=fname,
                            expected=t,
                            actual=to_text(v),
                        )
                    )
        for rule in ruleset.rules:
            if rule.applies(rec):
                # 型が不正な項目についての二次的な指摘は出さない（schema.type で報告済み）
                findings.extend(f for f in rule.check(loc, rec) if f.field not in bad_fields)
        if max_findings is not None and len(findings) >= max_findings:
            truncated = True
            break
    for rule in ruleset.rules:
        findings.extend(rule.finish())
    log.info("evaluated %d records with %d rules: %d findings", count, len(ruleset.rules), len(findings))
    return Result(findings[:max_findings] if max_findings else findings, count, truncated)
