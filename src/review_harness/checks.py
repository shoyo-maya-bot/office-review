"""レビュー観点（config/checks.yaml）の読み込み・検証と、種別ごとのチェックリストの生成."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from devtools_common.cli import UsageError

TYPES = ["api", "batch", "external-if", "common", "oas", "architecture", "db"]
TYPE_NAMES = {
    "api": "API 設計書",
    "batch": "バッチ設計書",
    "external-if": "外部インタフェース設計書",
    "common": "共通部品設計書",
    "oas": "OpenAPI（OAS）",
    "architecture": "基本設計書（アーキテクチャの章）",
    "db": "基本設計書（DB の章）",
}
SEVERITIES = ["MUST", "WANT", "NITS", "Q"]
ID_RE = re.compile(r"^[A-Z]{2,4}-\d{3}$")
# machine（harness precheck が確かめる部分）の種類 → 必須のキー
#   front_matter: keys（Front Matter に要るキーの一覧。「a|b」は a か b のどちらかがあればよい）
#   heading: pattern（見出しに当てる正規表現）
#   table_columns: columns（表に要る列名の一覧）
MACHINE_KINDS: dict[str, tuple[str, ...]] = {
    "front_matter": ("keys",),
    "heading": ("pattern",),
    "table_columns": ("columns",),
}


@dataclass
class Check:
    id: str
    types: list[str]
    role: str
    category: str
    severity: str
    title: str
    viewpoint: str
    invalid_when: str
    machine: dict[str, Any] | None = None

    def applies_to(self, doc_type: str) -> bool:
        return "all" in self.types or doc_type in self.types


@dataclass
class Catalog:
    roles: dict[str, dict[str, str]]
    checks: list[Check] = field(default_factory=list)

    def by_id(self) -> dict[str, Check]:
        return {c.id: c for c in self.checks}

    def for_type(self, doc_type: str) -> list[Check]:
        return [c for c in self.checks if c.applies_to(doc_type)]


def load(path: Path) -> Catalog:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    roles = data.get("roles") or {}
    errors: list[str] = []
    checks: list[Check] = []
    seen: set[str] = set()
    for i, raw in enumerate(data.get("checks") or []):
        where = f"checks[{i}]"
        missing = [
            k
            for k in ("id", "types", "role", "category", "severity", "title", "viewpoint", "invalid_when")
            if not raw.get(k)
        ]
        if missing:
            errors.append(f"{where}: {', '.join(missing)} がありません")
            continue
        cid = str(raw["id"])
        if not ID_RE.match(cid):
            errors.append(f"{where}: id '{cid}' は「英大文字 2〜4 字-数字 3 桁」にしてください")
        if cid in seen:
            errors.append(f"{where}: id '{cid}' が重複しています")
        seen.add(cid)
        bad_types = [t for t in raw["types"] if t != "all" and t not in TYPES]
        if bad_types:
            errors.append(f"{cid}: types に不明な種別 {bad_types}（使える種別: all, {', '.join(TYPES)}）")
        if raw["role"] not in roles:
            errors.append(f"{cid}: role '{raw['role']}' が roles にありません")
        if raw["severity"] not in SEVERITIES[:3]:
            errors.append(f"{cid}: severity は MUST / WANT / NITS のどれかです")
        if raw.get("machine") is not None:
            errors += [f"{cid}: machine: {e}" for e in machine_errors(raw["machine"])]
        checks.append(Check(**{k: raw.get(k) for k in Check.__dataclass_fields__}))
    if errors:
        raise UsageError(f"{path} の誤り:\n  " + "\n  ".join(errors))
    return Catalog(roles=roles, checks=checks)


def machine_errors(m: Any) -> list[str]:
    """checks.yaml の machine の誤り（読み込み時に確かめ、precheck の途中で落ちたり黙って飛ばしたりしない）."""
    if not isinstance(m, dict):
        return ["kind を持つ mapping にしてください"]
    kind = m.get("kind")
    if kind is None:
        return [f"kind がありません（使える kind: {', '.join(MACHINE_KINDS)}）"]
    if kind not in MACHINE_KINDS:
        return [f"kind '{kind}' は使えません（使える kind: {', '.join(MACHINE_KINDS)}）"]
    need = MACHINE_KINDS[kind]
    errors = [f"kind {kind} には {k} が必要です" for k in need if k not in m]
    for k in ("keys", "columns"):
        if k in need and k in m:
            v = m[k]
            if not isinstance(v, list) or not v or not all(isinstance(x, str) and x.strip() for x in v):
                errors.append(f"{k} は空でない文字列の一覧にしてください")
            elif k == "keys" and any(not a.strip() for x in v for a in x.split("|")):
                errors.append("keys の「a|b」の両側には空でないキー名を書いてください")
    if "pattern" in need and "pattern" in m:
        p = m["pattern"]
        if not isinstance(p, str) or not p:
            errors.append("pattern は空でない文字列（正規表現）にしてください")
        else:
            try:
                re.compile(p)
            except re.error as e:
                errors.append(f"pattern が正規表現として不正です: {e}")
    return errors


def render_checklist(catalog: Catalog, doc_type: str) -> str:
    """種別ごとのチェックリスト（Markdown）. config/checks.yaml から生成するので手で編集しない."""
    lines = [
        f"# {TYPE_NAMES[doc_type]} レビューチェックリスト",
        "",
        "<!-- harness checklists で config/checks.yaml から生成。直接編集しないこと -->",
        "",
        "指摘は「無効となるケース」に当たらないことを確かめてから出します。重要度は既定値で、状況に応じて変えてかまいません。",
        "",
    ]
    by_role: dict[str, list[Check]] = {}
    for c in catalog.for_type(doc_type):
        by_role.setdefault(c.role, []).append(c)
    for role, checks in sorted(by_role.items(), key=lambda kv: list(catalog.roles).index(kv[0])):
        info = catalog.roles[role]
        lines += [f"## {info['name']}（{role}）— {info['focus']}", ""]
        lines += ["| ID | 分類 | 既定の重要度 | 観点 | 確かめること | 無効となるケース |", "|---|---|---|---|---|---|"]
        for c in checks:
            mark = " 🤖" if c.machine else ""
            lines.append(
                f"| {c.id}{mark} | {c.category} | {c.severity} | {c.title} | {c.viewpoint} | {c.invalid_when} |"
            )
        lines.append("")
    lines.append("🤖 = `harness precheck` が機械的に確かめる部分がある観点（AI レビューの前に実行）")
    return "\n".join(lines) + "\n"


def write_checklists(catalog: Catalog, out_dir: Path, *, check_only: bool = False) -> list[str]:
    """生成して書き出す. check_only のときは書かず、古いファイルの一覧を返す."""
    stale = []
    for t in TYPES:
        path = out_dir / f"{t}-review.md"
        text = render_checklist(catalog, t)
        current = path.read_text(encoding="utf-8") if path.is_file() else None
        if current != text:
            stale.append(path.as_posix())
            if not check_only:
                out_dir.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8", newline="\n")
    return stale
