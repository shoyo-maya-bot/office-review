"""harness — 設計資料レビューの道具（チェックリスト生成・事前チェック・結果の検査・ダッシュボード・蓄積）.

流れ:
  harness convert 設計書.xlsx --type api            # Office を Markdown にして inputs/api/ へ（doc-to-markdown が必要。
                                                    #   変換レポートは data/convert-reports/api/ に残る）
  harness precheck inputs/api/order-api.md --type api   # 機械で確かめられる観点を先に確かめる
  （Copilot Chat で /review inputs/api/order-api.md → reviews/YYYY-MM-DD_order-api_result.md）
  harness lint reviews/                              # レビュー結果の形式チェック
  harness dashboard                                  # reviews/dashboard.html（採択 / 不採択を選んで triage.json を保存）
  harness triage triage.json                         # 判定を data/history.jsonl に蓄積
  harness accumulate                                 # 観点ごとの採択率と、観点の追加候補を docs/ に出力
  harness checklists                                 # config/checks.yaml から docs/review-checklists/ を生成

終了コード: 0 問題なし / 1 指摘あり / 2 引数・設定の誤り / 3 実行時エラー
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import sys
from pathlib import Path

from devtools_common import cli as common_cli
from devtools_common import report
from devtools_common.report import Finding

from . import __version__, checks, dashboard, history, precheck, results

TOOL = "harness"


def _root(args: argparse.Namespace) -> Path:
    return args.root.resolve()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=TOOL, description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("--root", type=Path, default=Path("."), help="リポジトリのルート（既定: カレント）")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("checklists", help="config/checks.yaml から種別ごとのチェックリストを生成する")
    s.add_argument("--check", action="store_true", help="書かずに、古いファイルがあれば終了コード 1（CI 用）")

    s = sub.add_parser("convert", help="Office 文書を Markdown にして inputs/<種別>/ に置く")
    s.add_argument("file", type=Path)
    s.add_argument("--type", required=True, choices=checks.TYPES)

    s = sub.add_parser("precheck", help="機械で確かめられる観点を確かめる")
    s.add_argument("file", type=Path)
    s.add_argument("--type", required=True, choices=checks.TYPES)
    common_cli.add_common_args(s)

    s = sub.add_parser("lint", help="レビュー結果ファイルの形式を確かめる")
    s.add_argument("paths", nargs="*", type=Path, help="ファイルかフォルダ（既定: reviews/）")
    common_cli.add_common_args(s)

    s = sub.add_parser("dashboard", help="レビュー結果の HTML ダッシュボードを作る")
    s.add_argument("-d", "--dest", type=Path, help="出力先（既定: reviews/dashboard.html）")

    s = sub.add_parser("triage", help="ダッシュボードで保存した triage.json を蓄積する")
    s.add_argument("file", type=Path)

    sub.add_parser("accumulate", help="採択率と観点の追加候補を docs/ に出力する")
    return p


def cmd_checklists(root: Path, args) -> int:
    catalog = checks.load(root / "config/checks.yaml")
    stale = checks.write_checklists(catalog, root / "docs/review-checklists", check_only=args.check)
    if args.check and stale:
        print(
            "古いチェックリストがあります。harness checklists を実行してコミットしてください:\n  " + "\n  ".join(stale)
        )
        return common_cli.ExitCode.FINDINGS
    print("チェックリスト: " + ("最新です" if not stale else f"{len(stale)} 件を更新しました"))
    return common_cli.ExitCode.OK


def cmd_convert(root: Path, args) -> int:
    """doc2md で変換する. doc2md の終了コード（doc-to-markdown の docs/OUTPUT_SPEC.md）:

    0 すべて変換できた（warning があっても 0）/ 1 convert.failed がある（--strict 時は warning も）/
    2 未対応の形式・入力が無い・外部ツールが無い / 3 想定外のエラー。
    --strict は付けないので 1 は「変換失敗」。warning（loss.* 等）は表示して data/convert-reports/ に残し、続行する。
    """
    # 見つけたパスで起動する（Windows では拡張子（.exe / .cmd）付きのパスでないと起動できないことがあるため）
    doc2md = shutil.which("doc2md")
    if doc2md is None:
        raise common_cli.UsageError(
            'doc2md が見つかりません。doc-to-markdown を入れてください（pip install -e ".[convert]"、または setup を再実行）'
        )
    import subprocess

    dest = root / "inputs" / args.type
    rep_path = root / "data" / "convert-reports" / args.type / f"{args.file.stem}.json"
    rep_path.parent.mkdir(parents=True, exist_ok=True)
    rc = subprocess.run(
        [doc2md, str(args.file), "-d", str(dest), "--front-matter", "-f", "json", "-o", str(rep_path)], check=False
    ).returncode
    findings = _convert_findings(rep_path)
    if rc != 0:
        errors = [f for f in findings if f.get("severity") == "error"]
        detail = "".join(f"\n  {f.get('rule_id', '')}: {f.get('message', '')}" for f in errors)
        kind = "引数・入力の誤り、または必要な外部ソフトが無い" if rc == 2 else "変換に失敗しました"
        print(f"error: doc2md: {args.file.name} の{kind}（終了コード {rc}）{detail}", file=sys.stderr)
        return common_cli.ExitCode.USAGE if rc == 2 else common_cli.ExitCode.RUNTIME
    warnings = [f for f in findings if f.get("severity") == "warning"]
    for f in warnings:
        print(f"警告: {f.get('rule_id', '')}: {f.get('message', '')}（{f.get('location', '')}）")
    out = dest / (args.file.stem + ".md")
    rel = rep_path.relative_to(root).as_posix()
    if warnings:
        print(f"→ {out}（警告 {len(warnings)} 件。原本と見比べてからレビューしてください。変換レポート: {rel}）")
    else:
        print(f"→ {out}（変換レポート: {rel}）")
    return common_cli.ExitCode.OK


def _convert_findings(path: Path) -> list[dict]:
    try:
        return list(json.loads(path.read_text(encoding="utf-8")).get("findings", []))
    except (OSError, ValueError, AttributeError):
        return []


def cmd_precheck(root: Path, args) -> int:
    catalog = checks.load(root / "config/checks.yaml")
    if not args.file.is_file():
        raise common_cli.UsageError(f"見つかりません: {args.file}")
    findings = precheck.run(args.file, args.type, catalog)
    text = report.render(
        findings, args.format, title="事前チェック", meta={"file": args.file.name, "type": args.type}, tool=TOOL
    )
    common_cli.write_output(text, args.output)
    return common_cli.ExitCode.FINDINGS if report.has_errors(findings) else common_cli.ExitCode.OK


def _review_files(root: Path, paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    for p in paths or [root / "reviews"]:
        files += sorted(p.glob("*_result.md")) if p.is_dir() else [p]
    return files


def cmd_lint(root: Path, args) -> int:
    catalog = checks.load(root / "config/checks.yaml")
    files = _review_files(root, args.paths)
    findings: list[Finding] = []
    for f in files:
        findings += results.lint(f, catalog)
    text = report.render(
        findings, args.format, title="レビュー結果の形式チェック", meta={"files": len(files)}, tool=TOOL
    )
    common_cli.write_output(text, args.output)
    return common_cli.ExitCode.FINDINGS if report.has_errors(findings) else common_cli.ExitCode.OK


def cmd_dashboard(root: Path, args) -> int:
    catalog = checks.load(root / "config/checks.yaml")
    items = results.load_all(root / "reviews")
    hist = history.load(root / "data/history.jsonl", items)
    decided = {k: v["decision"] for k, v in hist.decisions.items()}
    dest = args.dest or root / "reviews/dashboard.html"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(dashboard.render(items, catalog, decided), encoding="utf-8", newline="\n")
    print(f"→ {dest}（{len(items)} 件）")
    _report_orphans(hist)
    return common_cli.ExitCode.OK


def _report_orphans(hist: history.Resolved) -> None:
    if not hist.orphans:
        return
    print(
        f"注意: いまの指摘に結び付けられない旧形式の判定 {len(hist.orphans)} 件"
        "（再レビューで No がずれた等。採択率には数えます。必要ならダッシュボードで判定し直してください）:"
    )
    for r in sorted(hist.orphans, key=lambda r: r.get("key", "")):
        print(f"  {r.get('key', '')} {r.get('decision', '')} {r.get('check_id', '')} {r.get('finding', '')}")


def _read_triage(path: Path) -> dict[str, str]:
    """triage.json（指紋 → adopted / rejected）. 無い・壊れているは使い方の誤り（終了コード 2）."""
    if not path.is_file():
        raise common_cli.UsageError(
            f"判定のファイルが見つかりません: {path}（ダッシュボードで書き出した triage.json を指定）"
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as e:
        raise common_cli.UsageError(
            f"{path.name} を JSON として読めません（{e.lineno} 行 {e.colno} 列: {e.msg}）"
        ) from None
    except UnicodeDecodeError:
        raise common_cli.UsageError(f"{path.name} を UTF-8 として読めません") from None
    if not isinstance(data, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in data.items()):
        raise common_cli.UsageError(f'{path.name} は {{"指紋": "adopted" か "rejected"}} の形にしてください')
    return data


def cmd_triage(root: Path, args) -> int:
    decisions = _read_triage(args.file)
    items = results.load_all(root / "reviews")
    try:
        n = history.append(root / "data/history.jsonl", decisions, items, date=dt.date.today().isoformat())
    except (KeyError, ValueError) as e:
        raise common_cli.UsageError(str(e).strip("'\"")) from None
    print(f"{n} 件の判定を data/history.jsonl に記録しました。次は harness accumulate")
    return common_cli.ExitCode.OK


def _cell(value) -> str:
    """Markdown の表のセル: | をエスケープし、改行を <br> にする（列がずれて表が壊れないように）."""
    text = "" if value is None else str(value)
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\r\n", "\n").replace("\n", "<br>")


def cmd_accumulate(root: Path, args) -> int:
    catalog = checks.load(root / "config/checks.yaml")
    resolved = history.load(root / "data/history.jsonl", results.load_all(root / "reviews"))
    hist = resolved.all_records()
    stats = history.stats(hist)
    known = catalog.by_id()
    min_samples, low = 5, 0.3
    lines = [
        "# 観点ごとの採択率",
        "",
        "<!-- harness accumulate が data/history.jsonl から生成 -->",
        "",
        f"採択率 = 採択 /（採択 + 不採択）。{min_samples} 件以上の観点で {low:.0%} 未満は見直し候補（⚠）です。",
        "観点の書き方か「無効となるケース」が足りず、誤指摘が多い可能性があります。",
        "",
        "| check_id | 観点 | 採択 | 不採択 | 採択率 | |",
        "|---|---|---|---|---|---|",
    ]
    warn = 0
    for s in stats:
        title = known[s.check_id].title if s.check_id in known else "（新しい観点）"
        flag = "⚠" if s.total >= min_samples and (s.rate or 0) < low else ""
        warn += bool(flag)
        rate = f"{s.rate:.0%}" if s.rate is not None else "-"
        lines.append(f"| {_cell(s.check_id)} | {_cell(title)} | {s.adopted} | {s.rejected} | {rate} | {flag} |")
    (root / "docs").mkdir(exist_ok=True)
    (root / "docs/adoption.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    cands = history.new_check_candidates(hist)
    cand_lines = [
        "# 観点の追加候補",
        "",
        "<!-- harness accumulate が生成。採択された MUST / WANT のうち、既存の観点（check_id）に無かった指摘 -->",
        "",
        "人が確認し、残すべきものは config/checks.yaml に観点として追加（無効となるケースも書く）して PR を出してください。",
        "",
        "| 指摘 | 種別 | ロール | 重要度 | 記録日 |",
        "|---|---|---|---|---|",
    ]
    cand_lines += [
        "| " + " | ".join(_cell(c.get(k)) for k in ("finding", "doc_type", "role", "severity", "triaged_at")) + " |"
        for c in cands
    ]
    (root / "docs/check-candidates.md").write_text("\n".join(cand_lines) + "\n", encoding="utf-8", newline="\n")
    print(
        f"docs/adoption.md（見直し候補 {warn} 件）、docs/check-candidates.md（追加候補 {len(cands)} 件）を更新しました"
    )
    _report_orphans(resolved)
    return common_cli.ExitCode.OK


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if hasattr(args, "log_level"):
        common_cli.configure_from_args(args)
    root = _root(args)
    handlers = {
        "checklists": cmd_checklists,
        "convert": cmd_convert,
        "precheck": cmd_precheck,
        "lint": cmd_lint,
        "dashboard": cmd_dashboard,
        "triage": cmd_triage,
        "accumulate": cmd_accumulate,
    }
    return handlers[args.command](root, args)


def entrypoint() -> None:
    sys.exit(common_cli.run_main(main))


if __name__ == "__main__":
    entrypoint()
