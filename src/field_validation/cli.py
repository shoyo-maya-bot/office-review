"""fieldcheck — 構造化データの項目間整合性（クロスフィールド）を検証する CLI.

例:
  fieldcheck validate data.csv -r rules.yaml
  fieldcheck validate out/expense-list.md -r rules.yaml --table 申請一覧 -f json   # doc-to-markdown の出力を検証
  fieldcheck validate header.csv -r rules.yaml --dataset 明細=detail.csv              # 表をまたぐ検証
  fieldcheck check-rules rules.yaml                                               # ルールファイルだけ検証
  fieldcheck schema > rules.schema.json                                           # エディタ補完・AI 用のスキーマ

終了コード: 0 指摘なし / 1 --fail-on 以上の指摘あり / 2 引数・ルール・入力スキーマの誤り / 3 実行時エラー
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from devtools_common import cli as common_cli
from devtools_common import report
from devtools_common.report import Severity

from . import __version__
from .datasets import parse_dataset_arg
from .engine import evaluate, load_plugins, load_ruleset, rules_schema
from .loaders import open_source
from .rules import RULE_TYPES


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="fieldcheck", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("--plugin", action="append", type=Path, default=[], help="カスタムルールの .py（複数可）")
    sub = p.add_subparsers(dest="command", required=True)

    v = sub.add_parser("validate", help="データを検証してレポートを出力")
    v.add_argument("input", type=Path, help="csv / tsv / json / jsonl / yaml / md")
    v.add_argument("-r", "--rules", type=Path, required=True, help="ルールファイル（YAML / JSON）")
    v.add_argument("--table", help="Markdown 入力で使う表（1 始まりの番号、または直前の見出し名）")
    v.add_argument("--encoding", default="utf-8-sig", help="入力の文字コード（例: cp932）")
    v.add_argument(
        "--fail-on",
        choices=["error", "warning", "info", "never"],
        default="error",
        help="この重大度以上の指摘があれば終了コード 1（既定: error）",
    )
    v.add_argument("--max-findings", type=int, help="指摘がこの件数に達したら打ち切る")
    v.add_argument(
        "--dataset",
        action="append",
        default=[],
        metavar="名前=パス[#表]",
        help="ルールが参照する別の表（ルールファイルの datasets: を上書き。複数可）",
    )
    common_cli.add_common_args(v)

    c = sub.add_parser("check-rules", help="ルールファイルだけを検証")
    c.add_argument("rules", type=Path)
    c.add_argument("--dataset", action="append", default=[], metavar="名前=パス[#表]")
    c.add_argument("--log-level", default="WARNING")

    s = sub.add_parser("schema", help="ルールファイルの JSON Schema を出力")
    s.add_argument("--log-level", default="WARNING")

    t = sub.add_parser("list-rules", help="使えるルール型の一覧")
    t.add_argument("--log-level", default="WARNING")
    return p


def _fails(findings, level: str) -> bool:
    if level == "never":
        return False
    limit = Severity(level).rank
    return any(f.severity.rank <= limit for f in findings)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    common_cli.configure_from_args(args)
    load_plugins(args.plugin)

    if args.command == "schema":
        sys.stdout.write(json.dumps(rules_schema(), ensure_ascii=False, indent=2) + "\n")
        return common_cli.ExitCode.OK
    if args.command == "list-rules":
        for name, cls in sorted(RULE_TYPES.items()):
            doc = (cls.__doc__ or "").strip().splitlines()[0] if cls.__doc__ else ""
            params = ", ".join(f"{k}{'*' if k in cls.required_params else ''}" for k in cls.params_schema)
            print(f"{name:12} {params:40} {doc}")
        return common_cli.ExitCode.OK
    if args.command == "check-rules":
        rs = load_ruleset(args.rules, [parse_dataset_arg(d) for d in args.dataset])
        print(f"OK: {args.rules.name} ({len(rs.rules)} rules, {len(rs.fields)} fields, {len(rs.masters)} masters)")
        return common_cli.ExitCode.OK

    ruleset = load_ruleset(args.rules, [parse_dataset_arg(d) for d in args.dataset])
    source = open_source(args.input, encoding=args.encoding, table=args.table)
    result = evaluate(source, ruleset, max_findings=args.max_findings)
    meta = {"input": args.input.name, "rules": f"{ruleset.name} ({len(ruleset.rules)})", "records": result.records}
    if args.table:
        meta["table"] = args.table
    if ruleset.datasets:
        meta["datasets"] = ", ".join(ruleset.datasets)
    if result.truncated:
        meta["truncated"] = f"--max-findings {args.max_findings} で打ち切り"
    text = report.render(
        result.findings,
        args.format,
        title="fieldcheck 検証レポート",
        meta=meta,
        tool="fieldcheck",
        tool_version=__version__,
    )
    common_cli.write_output(text, args.output)
    return common_cli.ExitCode.FINDINGS if _fails(result.findings, args.fail_on) else common_cli.ExitCode.OK


def entrypoint() -> None:
    sys.exit(common_cli.run_main(main))


if __name__ == "__main__":
    entrypoint()
