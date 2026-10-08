"""review_harness のテスト（リポジトリを一時ディレクトリに複製して実行）."""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from devtools_common.cli import run_main

from review_harness import checks, cli, history, results

ROOT = Path(__file__).resolve().parent.parent
REVIEW = "reviews/2026-10-02_order-api_result.md"


def harness(root: Path, *args: str) -> tuple[int, str]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        rc = run_main(cli.main, ["--root", str(root), *args])
    return rc, out.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        for d in ["config", "docs", "inputs", "reviews"]:
            shutil.copytree(ROOT / d, self.tmp / d)
        (self.tmp / "data").mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def edit(self, rel: str, old: str, new: str) -> None:
        p = self.tmp / rel
        s = p.read_text(encoding="utf-8")
        self.assertIn(old, s)
        p.write_text(s.replace(old, new, 1), encoding="utf-8")

    def rules(self, out: str) -> list[str]:
        return [f["rule_id"] for f in json.loads(out)["findings"]]


class ChecklistTest(Base):
    def test_committed_checklists_are_fresh(self):
        self.assertEqual(harness(ROOT, "checklists", "--check")[0], 0)

    def test_stale_checklist_detected_and_regenerated(self):
        self.edit(
            "config/checks.yaml", "title: 想定件数・応答時間・同時実行数がある", "title: 想定件数と応答時間がある"
        )
        self.assertEqual(harness(self.tmp, "checklists", "--check")[0], 1)
        self.assertEqual(harness(self.tmp, "checklists")[0], 0)
        self.assertIn(
            "想定件数と応答時間がある", (self.tmp / "docs/review-checklists/api-review.md").read_text("utf-8")
        )

    def test_invalid_catalog_is_usage_error(self):
        self.edit("config/checks.yaml", "id: API-002", "id: API-001")
        self.edit(
            "config/checks.yaml",
            "role: dba\n    category: データアクセス",
            "role: tester\n    category: データアクセス",
        )
        rc, _ = harness(self.tmp, "checklists")
        self.assertEqual(rc, 2)
        with self.assertRaises(Exception) as ctx:
            checks.load(self.tmp / "config/checks.yaml")
        self.assertIn("重複", str(ctx.exception))
        self.assertIn("tester", str(ctx.exception))


class PrecheckTest(Base):
    def test_sample_passes_machine_checks(self):
        rc, out = harness(
            self.tmp, "precheck", str(self.tmp / "inputs/api/order-api.md"), "--type", "api", "-f", "json"
        )
        self.assertEqual((rc, self.rules(out)), (0, []))

    def test_missing_front_matter_heading_and_columns(self):
        doc = self.tmp / "inputs/api/bad.md"
        doc.write_text("# 何かの API\n\n| 名前 | 説明 |\n|---|---|\n| a | b |\n", encoding="utf-8")
        rc, out = harness(self.tmp, "precheck", str(doc), "--type", "api", "-f", "json")
        self.assertEqual(rc, 1)
        self.assertEqual(sorted(self.rules(out)), ["ALL-001", "API-001", "API-002"])

    def test_batch_step_heading(self):
        doc = self.tmp / "inputs/batch/job.md"
        doc.write_text('---\nid: "D"\ntitle: "T"\n---\n# JOB\n## 処理\n', encoding="utf-8")
        _, out = harness(self.tmp, "precheck", str(doc), "--type", "batch", "-f", "json")
        self.assertEqual(self.rules(out), ["BAT-003"])
        doc.write_text('---\nid: "D"\ntitle: "T"\n---\n# JOB\n### STEP1 入力チェック\n', encoding="utf-8")
        _, out = harness(self.tmp, "precheck", str(doc), "--type", "batch", "-f", "json")
        self.assertEqual(self.rules(out), [])


class LintTest(Base):
    def test_sample_is_valid(self):
        rc, out = harness(self.tmp, "lint", "-f", "json")
        self.assertEqual((rc, self.rules(out)), (0, []))

    def test_detects_problems(self):
        self.edit(REVIEW, "| 2 | API-003 | MUST |", "| 2 | API-999 | MUST |")
        self.edit(REVIEW, "| 3 | API-006 | MUST |", "| 3 | BAT-001 | HIGH |")
        self.edit(
            REVIEW,
            "| 4 | API-007 | WANT | dba | 3. 処理 1 | 参照テーブルと検索条件・使うインデックスが書かれていない | 3. 処理 1 |",
            "| 4 | API-007 | WANT | dba | 3. 処理 1 | 参照テーブルと検索条件・使うインデックスが書かれていない |  |",
        )
        rc, out = harness(self.tmp, "lint", "-f", "json")
        self.assertEqual(rc, 1)
        self.assertEqual(sorted(self.rules(out)), ["lint.basis", "lint.check_id", "lint.check_type", "lint.severity"])

    def test_missing_table(self):
        (self.tmp / "reviews/x_result.md").write_text(
            "---\ntarget: a\ntype: api\nreviewed_at: 2026-10-02\n---\n# r\n", encoding="utf-8"
        )
        _, out = harness(self.tmp, "lint", "-f", "json")
        self.assertIn("lint.table", self.rules(out))


class TriageTest(Base):
    def triage(self, decisions: dict) -> int:
        f = self.tmp / "triage.json"
        f.write_text(json.dumps(decisions), encoding="utf-8")
        return harness(self.tmp, "triage", str(f))[0]

    def test_flow_and_candidates(self):
        k = lambda n: triage_key(self.tmp, n)  # noqa: E731
        self.assertEqual(self.triage({k(1): "adopted", k(5): "adopted", k(4): "rejected"}), 0)
        self.assertEqual(harness(self.tmp, "accumulate")[0], 0)
        adoption = (self.tmp / "docs/adoption.md").read_text("utf-8")
        self.assertIn("| ALL-003 |", adoption)
        self.assertIn("| API-007 | 参照・更新するテーブルと条件が書かれている | 0 | 1 | 0% |  |", adoption)
        self.assertIn("タイムゾーン", (self.tmp / "docs/check-candidates.md").read_text("utf-8"))

    def test_low_adoption_is_flagged(self):
        lines = []
        for i in range(6):
            lines.append(
                json.dumps(
                    {
                        "key": f"r{i}#1",
                        "decision": "rejected" if i else "adopted",
                        "check_id": "API-005",
                        "severity": "WANT",
                        "role": "infra",
                        "doc_type": "api",
                        "finding": "x",
                        "triaged_at": "2026-10-02",
                    }
                )
            )
        (self.tmp / "data/history.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
        harness(self.tmp, "accumulate")
        self.assertIn(
            "| API-005 | 想定件数・応答時間・同時実行数がある | 1 | 5 | 17% | ⚠ |",
            (self.tmp / "docs/adoption.md").read_text("utf-8"),
        )

    def test_unknown_key_or_decision_is_usage_error(self):
        self.assertEqual(self.triage({"nope#1": "adopted"}), 2)
        self.assertEqual(self.triage({"2026-10-02_order-api_result.md#1": "maybe"}), 2)


class DashboardTest(Base):
    def test_html_escapes_and_contains_items(self):
        self.edit(REVIEW, "「適宜ページングする」では", "<script>alert(1)</script> 「適宜ページングする」では")
        rc, _ = harness(self.tmp, "dashboard")
        self.assertEqual(rc, 0)
        html = (self.tmp / "reviews/dashboard.html").read_text("utf-8")
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertEqual(html.count('type="radio" name="fp:'), 12)

    def test_dest_folder_is_created(self):
        dest = self.tmp / "out/nested/dash.html"
        rc, _ = harness(self.tmp, "dashboard", "-d", str(dest))
        self.assertEqual(rc, 0)
        self.assertTrue(dest.is_file())


def triage_key(root: Path, no: int) -> str:
    """ダッシュボードが triage.json に書くキー（指摘の指紋）."""
    item = next(i for i in results.load_all(root / "reviews") if i.no == no)
    return item.fingerprint


class FingerprintTest(Base):
    """判定は No ではなく指摘の指紋（対象・箇所・指摘の本文）で結び付ける."""

    def renumber(self) -> None:
        # 再レビューで先頭に指摘が 1 件増え、以降の No がずれた状態を作る
        self.edit(
            REVIEW,
            "| 1 | ALL-003 |",
            "| 1 | API-005 | Q | infra | 全体 | 想定の同時実行数はどの程度か | 記載なし | 非機能要件書の参照も無い |\n| 1 | ALL-003 |",
        )
        p = self.tmp / REVIEW
        lines = p.read_text(encoding="utf-8").splitlines()
        n = 0
        for i, ln in enumerate(lines):
            if ln.startswith("| ") and ln[2].isdigit():
                n += 1
                lines[i] = f"| {n} |" + ln.split("|", 2)[2]
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def decided_by_finding(self) -> dict[str, str]:
        items = results.load_all(self.tmp / "reviews")
        res = history.resolve(history.load_records(self.tmp / "data/history.jsonl"), items)
        return {i.finding: res.decisions[i.fingerprint]["decision"] for i in items if i.fingerprint in res.decisions}

    def test_fingerprint_is_independent_of_no_and_file(self):
        items = results.load_all(self.tmp / "reviews")
        a = items[0]
        moved = results.ReviewItem(**{**a.__dict__, "no": 99, "file": "2026-10-09_order-api_result.md"})
        self.assertEqual(a.fingerprint, moved.fingerprint)
        spaced = results.ReviewItem(**{**a.__dict__, "location": " 3.  処理 3 ", "finding": a.finding + "  "})
        self.assertEqual(a.fingerprint, spaced.fingerprint)
        other = results.ReviewItem(**{**a.__dict__, "finding": "別の指摘"})
        self.assertNotEqual(a.fingerprint, other.fingerprint)
        self.assertEqual(len({i.fingerprint for i in items}), len(items))

    def test_decisions_follow_the_finding_after_rerun(self):
        f = self.tmp / "triage.json"
        f.write_text(
            json.dumps({triage_key(self.tmp, 1): "adopted", triage_key(self.tmp, 4): "rejected"}), encoding="utf-8"
        )
        self.assertEqual(harness(self.tmp, "triage", str(f))[0], 0)
        rec = json.loads((self.tmp / "data/history.jsonl").read_text("utf-8").splitlines()[0])
        self.assertIn("fingerprint", rec)
        self.assertRegex(rec["key"], r"#\d+$")  # quality-analytics が読む key は残す
        before = self.decided_by_finding()
        self.renumber()
        self.assertEqual(self.decided_by_finding(), before)
        harness(self.tmp, "dashboard")
        html = (self.tmp / "reviews/dashboard.html").read_text("utf-8")
        row = next(r for r in html.split("<tr ") if "参照テーブルと検索条件" in r)
        self.assertIn('value="rejected" checked', row)
        self.assertNotIn('value="adopted" checked', row)

    def test_legacy_entries_migrate_only_when_no_still_matches(self):
        # data/history.jsonl は旧形式（key = ファイル名#No）。No がそのままなら同じ指摘に引き継ぐ
        shutil.copy(ROOT / "data/history.jsonl", self.tmp / "data/history.jsonl")
        before = self.decided_by_finding()
        self.assertEqual(len(before), 3)
        self.renumber()
        items = results.load_all(self.tmp / "reviews")
        res = history.resolve(history.load_records(self.tmp / "data/history.jsonl"), items)
        self.assertEqual(res.decisions, {})
        self.assertEqual(
            sorted(r["key"] for r in res.orphans), [f"2026-10-02_order-api_result.md#{n}" for n in (1, 4, 5)]
        )
        rc, out = harness(self.tmp, "dashboard")
        self.assertEqual(rc, 0)
        self.assertIn("結び付けられない旧形式の判定 3 件", out)
        # 孤立した判定も、観点ごとの採択率には数える
        harness(self.tmp, "accumulate")
        self.assertIn(
            "| API-007 | 参照・更新するテーブルと条件が書かれている | 0 | 1 | 0% |  |",
            (self.tmp / "docs/adoption.md").read_text("utf-8"),
        )

    def test_retriage_overrides_migrated_legacy_entry(self):
        shutil.copy(ROOT / "data/history.jsonl", self.tmp / "data/history.jsonl")
        f = self.tmp / "triage.json"
        f.write_text(json.dumps({triage_key(self.tmp, 4): "adopted"}), encoding="utf-8")
        self.assertEqual(harness(self.tmp, "triage", str(f))[0], 0)
        harness(self.tmp, "accumulate")
        self.assertIn(
            "| API-007 | 参照・更新するテーブルと条件が書かれている | 1 | 0 | 100% |  |",
            (self.tmp / "docs/adoption.md").read_text("utf-8"),
        )


class FingerprintRoleCheckTest(Base):
    """指紋にロールと観点（check_id）を含める. 旧指紋（0.2.0 まで）の記録も引き続き結び付く."""

    DUP = "| 7 | API-007 | WANT | infra | 3. 処理 1 | 参照テーブルと検索条件・使うインデックスが書かれていない | 3. 処理 1 | DB を使う API のため該当しない |"

    def add_same_text_other_role(self) -> None:
        # No 4（dba・API-007）と同じ箇所・同じ本文を、別のロール（infra）が出した
        p = self.tmp / REVIEW
        p.write_text(p.read_text(encoding="utf-8").rstrip("\n") + "\n" + self.DUP + "\n", encoding="utf-8")

    def item(self, no: int) -> results.ReviewItem:
        return next(i for i in results.load_all(self.tmp / "reviews") if i.no == no)

    def test_role_and_check_id_are_part_of_fingerprint(self):
        self.add_same_text_other_role()
        a, b = self.item(4), self.item(7)
        self.assertEqual(a.fingerprint_v1, b.fingerprint_v1)  # 旧指紋では区別できなかった
        self.assertNotEqual(a.fingerprint, b.fingerprint)
        other_check = results.ReviewItem(**{**vars(a), "check_id": "NEW"})
        self.assertNotEqual(a.fingerprint, other_check.fingerprint)
        # ダッシュボードで別々に判定できる（ラジオボタンの name が別）
        f = self.tmp / "triage.json"
        f.write_text(json.dumps({a.fingerprint: "adopted", b.fingerprint: "rejected"}), encoding="utf-8")
        self.assertEqual(harness(self.tmp, "triage", str(f))[0], 0)
        res = history.load(self.tmp / "data/history.jsonl", results.load_all(self.tmp / "reviews"))
        self.assertEqual(res.decisions[a.fingerprint]["decision"], "adopted")
        self.assertEqual(res.decisions[b.fingerprint]["decision"], "rejected")
        rec = json.loads((self.tmp / "data/history.jsonl").read_text("utf-8").splitlines()[0])
        self.assertEqual(rec["fingerprint"], a.fingerprint_v1)  # 読み手（quality-analytics）が引く旧指紋も残す
        self.assertEqual(rec["fingerprint_v2"], a.fingerprint)
        harness(self.tmp, "dashboard")
        html = (self.tmp / "reviews/dashboard.html").read_text("utf-8")
        self.assertEqual(html.count('type="radio" name="fp:'), 14)

    def v1_record(self, no: int, decision: str) -> dict:
        it = self.item(no)
        return {
            "key": it.key,
            "fingerprint": it.fingerprint_v1,  # 0.2.0 の harness triage が書いた形（fingerprint_v2 なし）
            "decision": decision,
            "check_id": it.check_id,
            "severity": it.severity,
            "role": it.role,
            "doc_type": it.doc_type,
            "location": it.location,
            "finding": it.finding,
            "triaged_at": "2026-10-05",
        }

    def test_v1_records_still_link_to_the_finding(self):
        recs = [self.v1_record(1, "adopted"), self.v1_record(4, "rejected")]
        (self.tmp / "data/history.jsonl").write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs), encoding="utf-8"
        )
        items = results.load_all(self.tmp / "reviews")
        res = history.load(self.tmp / "data/history.jsonl", items)
        self.assertEqual(res.orphans, [])
        self.assertEqual(res.decisions[self.item(1).fingerprint]["decision"], "adopted")
        self.assertEqual(res.decisions[self.item(4).fingerprint]["decision"], "rejected")
        # 別のロールが同じ文の指摘を足しても、旧指紋の判定は記録したロール・観点の指摘に付く
        self.add_same_text_other_role()
        res = history.load(self.tmp / "data/history.jsonl", results.load_all(self.tmp / "reviews"))
        self.assertEqual(res.decisions[self.item(4).fingerprint]["decision"], "rejected")
        self.assertNotIn(self.item(7).fingerprint, res.decisions)
        harness(self.tmp, "dashboard")
        html = (self.tmp / "reviews/dashboard.html").read_text("utf-8")
        row = next(r for r in html.split("<tr ") if "参照テーブルと検索条件" in r and "dba" in r)
        self.assertIn('value="rejected" checked', row)
        # 判定し直す（v2 で記録）と、旧指紋の判定より後の判定が勝つ
        f = self.tmp / "triage.json"
        f.write_text(json.dumps({self.item(4).fingerprint: "adopted"}), encoding="utf-8")
        self.assertEqual(harness(self.tmp, "triage", str(f))[0], 0)
        res = history.load(self.tmp / "data/history.jsonl", results.load_all(self.tmp / "reviews"))
        self.assertEqual(res.decisions[self.item(4).fingerprint]["decision"], "adopted")
        self.assertEqual(len(res.decisions), 2)

    def test_v1_triage_json_is_accepted_unless_ambiguous(self):
        f = self.tmp / "triage.json"
        f.write_text(json.dumps({self.item(1).fingerprint_v1: "adopted"}), encoding="utf-8")
        self.assertEqual(harness(self.tmp, "triage", str(f))[0], 0)
        self.add_same_text_other_role()
        f.write_text(json.dumps({self.item(4).fingerprint_v1: "adopted"}), encoding="utf-8")
        self.assertEqual(harness(self.tmp, "triage", str(f))[0], 2)

    def test_lint_reports_duplicate_findings(self):
        self.add_same_text_other_role()
        rc, out = harness(self.tmp, "lint", "-f", "json")
        self.assertEqual((rc, self.rules(out)), (0, []))  # ロールが違えば重複ではない
        dup = self.DUP.replace("| 7 |", "| 8 |").replace("| infra |", "| dba |")
        p = self.tmp / REVIEW
        p.write_text(p.read_text(encoding="utf-8") + dup + "\n", encoding="utf-8")
        rc, out = harness(self.tmp, "lint", "-f", "json")
        self.assertEqual(rc, 1)
        found = [f for f in json.loads(out)["findings"] if f["rule_id"] == "lint.duplicate"]
        self.assertEqual(
            [(f["location"], f["message"][:16]) for f in found],
            [(f"{Path(REVIEW).name}#8", "No 4 と同じ指摘です（対象・")],
        )


class ConvertTest(Base):
    """doc2md の終了コード: 0 成功（warning があっても 0）/ 1 変換失敗あり / 2 引数・入力の誤り / 3 実行時エラー."""

    def fake_doc2md(self, rc: int, findings: list[dict]) -> None:
        import os
        import stat
        import sys as _sys

        bindir = self.tmp / "bin"
        bindir.mkdir()
        report_obj = {"findings": findings}
        # POSIX は #! 付きのスクリプト、Windows は doc2md.cmd から Python のスクリプトを呼ぶ
        script = bindir / ("doc2md.py" if os.name == "nt" else "doc2md")
        if os.name == "nt":
            (bindir / "doc2md.cmd").write_text(f'@"{_sys.executable}" "%~dp0doc2md.py" %*\r\n', encoding="utf-8")
        script.write_text(
            f"#!{_sys.executable}\n"
            "import json, pathlib, sys\n"
            "a = sys.argv[1:]\n"
            "out = a[a.index('-o') + 1] if '-o' in a else None\n"
            "dest = pathlib.Path(a[a.index('-d') + 1]); dest.mkdir(parents=True, exist_ok=True)\n"
            "(dest / (pathlib.Path(a[0]).stem + '.md')).write_text('# x\\n', encoding='utf-8')\n"
            f"text = {json.dumps(json.dumps(report_obj, ensure_ascii=False))}\n"
            "pathlib.Path(out).write_text(text, encoding='utf-8') if out else print(text)\n"
            f"sys.exit({rc})\n",
            encoding="utf-8",
        )
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        old = os.environ.get("PATH", "")
        os.environ["PATH"] = f"{bindir}{os.pathsep}{old}"
        self.addCleanup(os.environ.__setitem__, "PATH", old)

    def convert(self) -> tuple[int, str, str]:
        src = self.tmp / "設計書.xlsx"
        src.write_bytes(b"x")
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = run_main(cli.main, ["--root", str(self.tmp), "convert", str(src), "--type", "api"])
        return rc, out.getvalue(), err.getvalue()

    def test_exit_1_is_failure(self):
        self.fake_doc2md(
            1,
            [
                {
                    "rule_id": "convert.failed",
                    "severity": "error",
                    "message": "BadZipFile: 壊れています",
                    "location": "設計書.xlsx",
                }
            ],
        )
        rc, out, err = self.convert()
        self.assertNotEqual(rc, 0)
        self.assertIn("変換に失敗", err)
        self.assertIn("BadZipFile", err)
        self.assertNotIn("→", out)

    def test_warnings_are_shown_and_recorded(self):
        w = {
            "rule_id": "loss.raw_text",
            "severity": "warning",
            "message": "元ファイルの文字の 80% しか出力されていない",
            "location": "設計書.xlsx",
        }
        self.fake_doc2md(
            0, [{"rule_id": "convert.ok", "severity": "info", "message": "ok", "location": "設計書.xlsx"}, w]
        )
        rc, out, err = self.convert()
        self.assertEqual(rc, 0)
        self.assertIn("loss.raw_text", out + err)
        self.assertIn("80%", out + err)
        rep = self.tmp / "data/convert-reports/api/設計書.json"
        self.assertTrue(rep.is_file())
        self.assertIn("loss.raw_text", rep.read_text("utf-8"))
        self.assertTrue((self.tmp / "inputs/api/設計書.md").is_file())

    def test_usage_error_from_doc2md(self):
        self.fake_doc2md(2, [])
        rc, _, err = self.convert()
        self.assertEqual(rc, 2)
        self.assertIn("doc2md", err)


if __name__ == "__main__":
    unittest.main()
